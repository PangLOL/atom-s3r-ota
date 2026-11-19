"""GE8 Fit F-CAN gauges + chibi Sonic for M5Stack Atom S3R + Atomic CAN Base.

Layout: animation in the top four-fifths. Bottom fifth is a 2-by-2
grid of horizontal gauges, no labels. Upper left is RPM, upper right
is speed (orange), lower left is throttle, lower right is coolant.
Each bar fills left to right. The other three use the green-to-red
value colours. A CAN fault is a small red dot in the upper-left
corner; the rest of the layout keeps running.

Hardware: Atomic CAN Base on the Atom S3R bottom connector.
    MCU TX = GPIO 5,  MCU RX = GPIO 6
    ATOMCANBase port tuple is (RX, TX) = (6, 5)
    Listen-only, 500 kbps F-CAN. No extra 120 ohm terminator.

Decode (Honda Fit GE8):
    0x158 bytes 0-1  big-endian * 0.01  -> speed km/h   (0-180)
    0x17C bytes 2-3  big-endian raw     -> RPM          (0-8000)
    0x13A byte 1     raw counts         -> throttle     (0-255)
    0x324 byte 0     minus 40           -> coolant C    (-40-125)

Screen-button double-tap starts a bench sweep: speed 0-100-80-0 with
matching RPM, throttle and coolant, so the run cycle and the 80 km/h
infinity-leg sprite can be tested without a vehicle.

Sprites are composited from separate pieces (legs, arms, torso, head,
gloves, shoes, infinity) that can be retimed or swapped. Full frames
are not loaded each tick.

Mouth: closed Super Sonic smile when speed is under 1 km/h; locked
open-mouth expression when speed is above 80 km/h; otherwise the
optional 60 percent scroll-period open at cruise. Overheat (coolant
at or above SWEAT_CLT) tints Sonic with a sine-like breathing red;
sweat droplets are not drawn.
"""

import math
import time

import M5
from M5 import Lcd


CAN_TX_PIN = 5
CAN_RX_PIN = 6
CAN_BITRATE = 500000

ID_SPEED = 0x158
ID_RPM = 0x17C
ID_THROTTLE = 0x13A
ID_COOLANT = 0x324

SPEED_MIN, SPEED_MAX = 0.0, 180.0
RPM_MIN, RPM_MAX = 0, 8000
TPS_MIN, TPS_MAX = 0, 255
CLT_MIN, CLT_MAX = -40, 125

WATCHDOG_MS = 2000
REFRESH_MS = 50
INF_SPEED = 80.0
SWEAT_CLT = 90.0
DBL_TAP_MS = 450
APP_VERSION = 1
APP_VERSION_NAME = "2026.11.21-a1"
BREATH_MS = 1600

W, H = 128, 128
GAUGE_H = H // 5
GAUGE_Y = H - GAUGE_H
BAR_PAD_X = 2
BAR_PAD_Y = 2
BAR_GAP_X = 2
BAR_GAP_Y = 1
BAR_W = (W - BAR_PAD_X * 2 - BAR_GAP_X) // 2
BAR_H = (GAUGE_H - BAR_PAD_Y * 2 - BAR_GAP_Y) // 2
_REM_X = W - BAR_PAD_X * 2 - BAR_GAP_X - BAR_W * 2
_REM_Y = GAUGE_H - BAR_PAD_Y * 2 - BAR_GAP_Y - BAR_H * 2
COL0_X = BAR_PAD_X + _REM_X // 2
COL1_X = COL0_X + BAR_W + BAR_GAP_X
ROW0_Y = GAUGE_Y + BAR_PAD_Y + _REM_Y // 2
ROW1_Y = ROW0_Y + BAR_H + BAR_GAP_Y
DOT_X, DOT_Y, DOT_R = 3, 3, 3

COL_BG = 0x3AA4F0
COL_TRACK = 0x2A2A2A
COL_ORANGE = 0xFF8C00
COL_ERR = 0xFF1744
COL_OUTLINE = 0x111114
COL_SONIC = 0x1A8EF0
COL_HEAT = 0xFF1A1A
COL_GLOVE = 0xFFFFFF
COL_SHOE = 0xE21B23
COL_STRAP = 0xFFFFFF
COL_MOUTH = 0xF26A8A
COL_TONGUE = 0xE84A6E
COL_MOUTH_LOCK = 0xFF0000
COL_MOUTH_CAVITY = 0xB40000
COL_INF = 0xE2262A
COL_INF_EDGE = 0x7A1014
COL_SKY = 0x3AA4F0
COL_CLOUD = 0xFFFFFF
COL_MTN = 0x7A8A9A
COL_MTN_DK = 0x5A6A78
COL_MTN_GRASS = 0x4AAA4A
COL_WATER = 0x4A9CC8
COL_WATER_HI = 0x7EC8E0
COL_GRASS = 0x46C04A
COL_GRASS_DK = 0x2E9A32
COL_DIRT = 0xD2A85A
COL_DIRT_DK = 0xA88440
COL_TRUNK = 0x8B5520
COL_PALM = 0x2EAA30
COL_PALM_LT = 0x5AD04A
COL_LOOP = 0xC8A45A
COL_LOOP_DK = 0xA08040
COL_FLOWER_R = 0xE04040
COL_FLOWER_Y = 0xF0D030

# key, lo, hi, x, y, w, h, color mode ("orange" or "value")
# 2x2 in the bottom fifth: RPM | speed / throttle | coolant.
GAUGES = (
    ("rpm", RPM_MIN, RPM_MAX,     COL0_X, ROW0_Y, BAR_W, BAR_H, "value"),
    ("spd", SPEED_MIN, SPEED_MAX, COL1_X, ROW0_Y, BAR_W, BAR_H, "orange"),
    ("tps", TPS_MIN, TPS_MAX,     COL0_X, ROW1_Y, BAR_W, BAR_H, "value"),
    ("clt", CLT_MIN, CLT_MAX,     COL1_X, ROW1_Y, BAR_W, BAR_H, "value"),
)

# 4-frame run cycle, limb endpoints relative to hip, in pixels.
# Each frame: (l_knee, l_foot, r_knee, r_foot, l_elbow, l_hand, r_elbow, r_hand)
# Character faces right. Phase 0-3 loops.
RUN_FRAMES = (
    ((-4, 8), (-8, 16), (6, 7), (12, 16), (6, 4), (12, 2), (-6, 5), (-12, 8)),
    ((-2, 9), (-2, 16), (4, 8), (8, 16), (4, 5), (8, 6), (-4, 5), (-8, 8)),
    ((6, 7), (12, 16), (-4, 8), (-8, 16), (-6, 5), (-12, 8), (6, 4), (12, 2)),
    ((4, 8), (8, 16), (-2, 9), (-2, 16), (-4, 5), (-8, 8), (4, 5), (8, 6)),
)

STAND_FRAME = (
    (-3, 8), (-5, 16), (3, 8), (5, 16), (-4, 5), (-8, 8), (5, 4), (9, 2)
)

# Green Hill Zone backdrop, 180 px wrap, scrolled by speed.
PERIOD = 180
WATER_Y = 54
WATER_H = 12
GRASS_H = 7
TILE = 6

# x, y, rx, ry
CLOUDS = (
    (12, 12, 16, 5),
    (52, 7, 12, 4),
    (88, 15, 18, 6),
    (140, 9, 14, 4),
)
# x, w, h, waterfall dx from left (0 = none)
MOUNTAINS = (
    (6, 38, 36, 16),
    (42, 26, 22, 0),
    (70, 42, 42, 20),
    (116, 30, 28, 0),
    (148, 38, 38, 18),
)
# x, rx, ry  (sit in the water)
ISLANDS = (
    (28, 10, 4),
    (100, 14, 5),
    (158, 8, 3),
)
# x, rx, ry  (sit on the grass)
HILLS = (
    (22, 16, 8),
    (82, 22, 10),
    (128, 14, 7),
)
# x, height
PALMS = (
    (10, 26),
    (46, 18),
    (98, 22),
    (168, 28),
)
LOOP_X = 148
LOOP_RO = 20
LOOP_RI = 12
# x, color
FLOWERS = (
    (18, 0xE04040),
    (24, 0xF0D030),
    (62, 0xE04040),
    (86, 0xF0D030),
    (108, 0xE06080),
    (172, 0xF0D030),
)


def be16(data, offset):
    return (data[offset] << 8) | data[offset + 1]


def clamp(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def decode_frame(can_id, data):
    n = len(data)
    if can_id == ID_SPEED and n >= 2:
        return "spd", be16(data, 0) * 0.01
    if can_id == ID_RPM and n >= 4:
        return "rpm", float(be16(data, 2))
    if can_id == ID_THROTTLE and n >= 2:
        return "tps", float(data[1])
    if can_id == ID_COOLANT and n >= 1:
        return "clt", float(data[0] - 40)
    return None


def _lerp(a, b, t):
    return int(a + (b - a) * t)


def mix_rgb(c1, c2, t):
    if t <= 0:
        return c1
    if t >= 1:
        return c2
    r1, g1, b1 = (c1 >> 16) & 0xFF, (c1 >> 8) & 0xFF, c1 & 0xFF
    r2, g2, b2 = (c2 >> 16) & 0xFF, (c2 >> 8) & 0xFF, c2 & 0xFF
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return (r << 16) | (g << 8) | b


def sonic_body_color(clt, now_ms):
    """Sine-like red breath when overheating; no on/off strobe."""
    if clt is None or clt < SWEAT_CLT:
        return COL_SONIC
    period = float(BREATH_MS)
    phase = ((now_ms % BREATH_MS) / period) * 6.28318530718
    breath = 0.5 + 0.5 * math.sin(phase)
    # Stay tinted the whole time, pulsing between mild and strong.
    return mix_rgb(COL_SONIC, COL_HEAT, 0.42 + 0.46 * breath)


def mouth_mode(speed, scroll, period=PERIOD, hold_speed=INF_SPEED):
    """closed | open | locked_open. Parked never inherits leftover scroll."""
    if speed is None:
        speed = 0.0
    if speed < 1.0:
        return "closed"
    if speed > hold_speed:
        return "locked_open"
    if scroll >= period * 0.6:
        return "open"
    return "closed"


def value_rgb(t):
    if t <= 0:
        return 0x00C853
    if t >= 1:
        return 0xFF1744
    if t < 0.5:
        u = t * 2.0
        r, g, b = _lerp(0x00, 0xFF, u), _lerp(0xC8, 0xD6, u), _lerp(0x53, 0x00, u)
    else:
        u = (t - 0.5) * 2.0
        r, g, b = 0xFF, _lerp(0xD6, 0x17, u), _lerp(0x00, 0x44, u)
    return (r << 16) | (g << 8) | b


def fill_circle(lcd, x, y, r, color):
    if r < 1:
        r = 1
    if hasattr(lcd, "fillCircle"):
        lcd.fillCircle(int(x), int(y), int(r), color)
    else:
        lcd.fillRect(int(x) - r, int(y) - r, 2 * r + 1, 2 * r + 1, color)


def fill_ellipse(lcd, x, y, rx, ry, color):
    x, y, rx, ry = int(x), int(y), max(1, int(rx)), max(1, int(ry))
    if hasattr(lcd, "fillEllipse"):
        lcd.fillEllipse(x, y, rx, ry, color)
        return
    # Cheap fallback: stacked circles.
    if rx == ry:
        fill_circle(lcd, x, y, rx, color)
        return
    steps = max(rx, ry)
    for i in range(-rx, rx + 1):
        yy = int(ry * math.sqrt(max(0.0, 1.0 - (i / rx) ** 2)))
        lcd.fillRect(x + i, y - yy, 1, yy * 2 + 1, color)


def outlined_ellipse(lcd, x, y, rx, ry, fill, outline=COL_OUTLINE, ow=1):
    fill_ellipse(lcd, x, y, rx + ow, ry + ow, outline)
    fill_ellipse(lcd, x, y, rx, ry, fill)


def fill_triangle(lcd, x0, y0, x1, y1, x2, y2, color):
    if hasattr(lcd, "fillTriangle"):
        lcd.fillTriangle(int(x0), int(y0), int(x1), int(y1), int(x2), int(y2), color)
        return
    lcd.drawLine(int(x0), int(y0), int(x1), int(y1), color)
    lcd.drawLine(int(x1), int(y1), int(x2), int(y2), color)
    lcd.drawLine(int(x2), int(y2), int(x0), int(y0), color)


def thick_line(lcd, x0, y0, x1, y1, color, width):
    r = max(1, int(width) // 2)
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx = x1 - x0
    dy = y1 - y0
    steps = max(abs(dx), abs(dy), 1)
    for i in range(steps + 1):
        t = i / steps
        fill_circle(lcd, x0 + dx * t, y0 + dy * t, r, color)


def find_btn():
    for name in ("BtnA", "Btn", "BtnPWR"):
        btn = getattr(M5, name, None)
        if btn is not None:
            return btn
    return None


class Sweep:
    """Speed 0 → 100 → 80 → 0, with matching RPM / throttle / coolant."""

    # (duration_ms, start_speed, end_speed, kind)
    STAGES = (
        (3500, 0.0, 100.0, "accel"),
        (1200, 100.0, 80.0, "settle"),
        (400, 80.0, 80.0, "hold"),
        (3500, 80.0, 0.0, "decel"),
    )

    def __init__(self):
        self.active = False
        self._t0 = 0
        self._stage = 0

    def start(self, now):
        self.active = True
        self._t0 = now
        self._stage = 0

    def step(self, now):
        if not self.active:
            return None
        elapsed = time.ticks_diff(now, self._t0)
        for dur, a, b, kind in self.STAGES:
            if elapsed < dur:
                u = 0.0 if dur == 0 else elapsed / dur
                # Ease in-out so the 80 km/h threshold is easy to see.
                if kind != "hold":
                    u = u * u * (3.0 - 2.0 * u)
                speed = a + (b - a) * u
                return self._values(speed, kind)
            elapsed -= dur
        self.active = False
        return self._values(0.0, "decel")

    def _values(self, speed, kind):
        if kind == "accel":
            tps = 50.0 + 1.7 * speed
            rpm = 800.0 + 32.0 * speed
            clt = 78.0 + 0.24 * speed
        elif kind == "settle" or kind == "hold":
            tps = 36.0 + 0.4 * (speed - 80.0)
            rpm = 800.0 + 28.0 * speed
            clt = 96.0
        else:
            tps = 0.0
            rpm = 800.0 + 22.0 * speed
            clt = 88.0 + 0.1 * speed
        return {
            "spd": clamp(speed, SPEED_MIN, SPEED_MAX),
            "rpm": clamp(rpm, RPM_MIN, RPM_MAX),
            "tps": clamp(tps, TPS_MIN, TPS_MAX),
            "clt": clamp(clt, CLT_MIN, CLT_MAX),
        }


class Sprite:
    """Composited chibi Sonic: separate pieces, swapped by speed, retimed by phase."""

    def __init__(self):
        self.scroll = 0.0
        self.phase = 0.0
        self.ground_y = GAUGE_Y - 2
        self.body_col = COL_SONIC

    def advance(self, speed, dt_ms):
        if speed is None:
            speed = 0.0
        # Backdrop scrolls with speed; run cycle is retimed by speed.
        self.scroll = (self.scroll + speed * dt_ms * 0.012) % 180.0
        if speed < 1.0:
            self.phase = 0.0
        else:
            self.phase = (self.phase + dt_ms * (0.002 + 0.00035 * speed)) % 4.0

    def draw(self, lcd, values, now=0):
        speed = values.get("spd") or 0.0
        tps = values.get("tps") or 0.0
        clt = values.get("clt") or 0.0
        self.body_col = sonic_body_color(clt, now)
        self._draw_background(lcd)
        self._draw_sonic(lcd, speed, tps, clt)

    def _wrapped(self, x, w, off):
        xx = (x - off) % PERIOD
        for cand in (xx, xx - PERIOD):
            if cand < W and cand + w > 0:
                yield cand

    def _draw_background(self, lcd):
        gy = self.ground_y
        lcd.fillRect(0, 0, W, GAUGE_Y, COL_SKY)
        off = int(self.scroll)
        off_c = int(self.scroll * 0.25)
        off_m = int(self.scroll * 0.4)
        off_i = int(self.scroll * 0.6)

        for cx, cy, rx, ry in CLOUDS:
            for x in self._wrapped(cx, rx * 2, off_c):
                fill_ellipse(lcd, x, cy, rx, ry, COL_CLOUD)

        base = WATER_Y + 2
        for mx, mw, mh, fall in MOUNTAINS:
            for x in self._wrapped(mx, mw, off_m):
                mid = x + mw // 2
                peak = base - mh
                fill_triangle(lcd, x, base, x + mw, base, mid, peak, COL_MTN)
                cap_y = peak + mh // 3
                fill_triangle(lcd, x + mw // 5, cap_y, x + mw - mw // 5, cap_y, mid, peak, COL_MTN_GRASS)
                if fall:
                    fx = x + fall
                    lcd.fillRect(fx, peak + 4, 2, base - (peak + 4), 0xD8F0F8)
                    lcd.fillRect(fx, peak + 4, 1, base - (peak + 4), 0xFFFFFF)

        lcd.fillRect(0, WATER_Y, W, WATER_H, COL_WATER)
        lcd.fillRect(0, WATER_Y + 2, W, 1, COL_WATER_HI)

        for ix, rx, ry in ISLANDS:
            for x in self._wrapped(ix, rx * 2, off_i):
                fill_ellipse(lcd, x, WATER_Y + 2, rx, ry, COL_MTN_GRASS)
                fill_ellipse(lcd, x, WATER_Y, rx - 2, 2, COL_GRASS)

        for hx, rx, ry in HILLS:
            for x in self._wrapped(hx, rx * 2, off):
                fill_ellipse(lcd, x, gy - GRASS_H + 2, rx, ry, COL_GRASS_DK)
                fill_ellipse(lcd, x, gy - GRASS_H, rx - 2, ry - 2, COL_GRASS)

        for x in self._wrapped(LOOP_X, LOOP_RO * 2, off):
            self._draw_loop(lcd, x, gy - LOOP_RO - 2)

        for px, ph in PALMS:
            for x in self._wrapped(px, 22, off):
                self._draw_palm(lcd, x, gy - GRASS_H + 2, ph)

        start = -(off % TILE)
        x = start
        while x < W:
            col = ((x + off) // TILE) & 1
            lcd.fillRect(x, gy - GRASS_H, TILE, GRASS_H - 2, COL_GRASS if col == 0 else COL_GRASS_DK)
            lcd.fillRect(x, gy - 2, TILE, 3, COL_DIRT if col == 0 else COL_DIRT_DK)
            x += TILE

        for fx, col in FLOWERS:
            for x in self._wrapped(fx, 3, off):
                lcd.fillRect(x, gy - GRASS_H - 1, 2, 2, col)

    def _draw_loop(self, lcd, cx, gy):
        cy = gy - LOOP_RO
        fill_ellipse(lcd, cx, cy, LOOP_RO + 1, LOOP_RO + 1, COL_LOOP_DK)
        fill_ellipse(lcd, cx, cy, LOOP_RO, LOOP_RO, COL_LOOP)
        fill_ellipse(lcd, cx, cy, LOOP_RI, LOOP_RI, COL_SKY)
        fill_ellipse(lcd, cx - 8, cy - LOOP_RO + 2, 6, 3, COL_GRASS)
        fill_ellipse(lcd, cx, cy - LOOP_RO + 1, 7, 4, COL_GRASS)
        fill_ellipse(lcd, cx + 8, cy - LOOP_RO + 2, 6, 3, COL_GRASS)

    def _draw_palm(self, lcd, x, gy, h):
        lcd.fillRect(x, gy - h, 3, h, COL_TRUNK)
        tx, ty = x + 1, gy - h
        fill_triangle(lcd, tx, ty, tx - 10, ty + 2, tx - 4, ty - 6, COL_PALM)
        fill_triangle(lcd, tx, ty, tx + 10, ty + 2, tx + 4, ty - 6, COL_PALM)
        fill_triangle(lcd, tx, ty, tx - 6, ty - 8, tx + 6, ty - 8, COL_PALM_LT)
        fill_triangle(lcd, tx, ty, tx - 8, ty + 6, tx + 2, ty + 4, COL_PALM)
        fill_ellipse(lcd, tx, ty, 2, 2, COL_TRUNK)

    def _draw_sonic(self, lcd, speed, tps, clt):
        # Two-head-tall chibi, facing right, centered in the playfield.
        hx, hy = 70, 68
        # Mid/high throttle widens the face.
        widen = clamp(tps, 0, 255) / 255.0
        rx = 11 + int(3 * widen)
        ry = 9
        torso = (66, 84)
        hip = (torso[0], torso[1] + 3)
        gy = self.ground_y

        # Draw order: limbs first, then torso, then body/head in front.
        if speed >= INF_SPEED:
            self._piece_infinity(lcd, hip[0], hip[1] + 4)
            self._piece_arms_lean(lcd, torso)
        else:
            frame = STAND_FRAME
            if speed >= 1.0:
                frame = RUN_FRAMES[int(self.phase) % 4]
            self._piece_run_limbs(lcd, hip, torso, frame, with_feet=True)

        self._piece_torso(lcd, torso)
        self._piece_head(lcd, hx, hy, rx, ry, speed)
        if speed >= INF_SPEED:
            self._piece_gloves_lean(lcd, torso)
        else:
            frame = STAND_FRAME if speed < 1.0 else RUN_FRAMES[int(self.phase) % 4]
            self._piece_gloves(lcd, torso, frame)


    def _piece_infinity(self, lcd, cx, cy):
        # Red no-feet lemniscate, keychain pose. Under-stroke then fill.
        self._infinity_stroke(lcd, cx, cy, 16, 7, 5, COL_INF_EDGE)
        self._infinity_stroke(lcd, cx, cy, 16, 7, 4, COL_INF)

    def _infinity_stroke(self, lcd, cx, cy, rx, ry, thickness, color):
        r = max(1, thickness // 2)
        n = 36
        for i in range(n):
            t = 6.28318530718 * i / n
            x = cx + rx * math.sin(t)
            y = cy + ry * math.sin(t) * math.cos(t)
            fill_circle(lcd, x, y, r, color)

    def _piece_run_limbs(self, lcd, hip, torso, frame, with_feet):
        l_knee, l_foot, r_knee, r_foot, l_elb, l_hand, r_elb, r_hand = frame
        # Legs behind the body.
        self._leg(lcd, hip, l_knee, l_foot, with_feet)
        self._leg(lcd, hip, r_knee, r_foot, with_feet)
        # Arms. Gloves are a later piece so they sit in front.
        ax, ay = torso[0], torso[1] - 3
        thick_line(lcd, ax, ay, ax + l_elb[0], ay + l_elb[1], COL_OUTLINE, 2)
        thick_line(lcd, ax + l_elb[0], ay + l_elb[1], ax + l_hand[0], ay + l_hand[1], COL_OUTLINE, 2)
        thick_line(lcd, ax, ay, ax + r_elb[0], ay + r_elb[1], COL_OUTLINE, 2)
        thick_line(lcd, ax + r_elb[0], ay + r_elb[1], ax + r_hand[0], ay + r_hand[1], COL_OUTLINE, 2)

    def _leg(self, lcd, hip, knee, foot, with_feet):
        kx, ky = hip[0] + knee[0], hip[1] + knee[1]
        fx, fy = hip[0] + foot[0], hip[1] + foot[1]
        thick_line(lcd, hip[0], hip[1], kx, ky, COL_OUTLINE, 2)
        thick_line(lcd, kx, ky, fx, fy, COL_OUTLINE, 2)
        if with_feet:
            self._piece_shoe(lcd, fx, fy, 1 if foot[0] >= 0 else -1)

    def _piece_shoe(self, lcd, x, y, direction):
        # Red oval, white strap. direction 1 = toe right.
        rx, ry = 6, 3
        outlined_ellipse(lcd, x, y, rx, ry, COL_SHOE, ow=1)
        lcd.fillRect(x - 1, y - 2, 3, 5, COL_STRAP)

    def _piece_torso(self, lcd, torso):
        outlined_ellipse(lcd, torso[0], torso[1], 5, 6, self.body_col, ow=1)

    def _piece_head(self, lcd, hx, hy, rx, ry, speed=0.0):
        # Quills and ears first, then the wide head, then the face.
        body = self.body_col
        fill_triangle(lcd, hx - 2, hy - 6, hx - 16, hy - 14, hx - 8, hy - 1, COL_OUTLINE)
        fill_triangle(lcd, hx - 3, hy - 5, hx - 15, hy - 13, hx - 8, hy - 2, body)
        fill_triangle(lcd, hx - 4, hy - 2, hx - 20, hy - 2, hx - 5, hy + 5, COL_OUTLINE)
        fill_triangle(lcd, hx - 5, hy - 1, hx - 18, hy - 2, hx - 6, hy + 4, body)
        fill_triangle(lcd, hx - 2, hy + 2, hx - 15, hy + 11, hx + 1, hy + 7, COL_OUTLINE)
        fill_triangle(lcd, hx - 3, hy + 3, hx - 13, hy + 10, hx + 0, hy + 6, body)
        # Ears
        fill_triangle(lcd, hx - 4, hy - 7, hx - 3, hy - 13, hx + 1, hy - 7, COL_OUTLINE)
        fill_triangle(lcd, hx - 3, hy - 7, hx - 2, hy - 11, hx + 0, hy - 7, body)
        fill_triangle(lcd, hx + 2, hy - 7, hx + 5, hy - 12, hx + 7, hy - 5, COL_OUTLINE)
        fill_triangle(lcd, hx + 3, hy - 7, hx + 5, hy - 10, hx + 6, hy - 5, body)
        outlined_ellipse(lcd, hx, hy, rx, ry, body, ow=1)
        # Official tick eyes and a pointy black nose. Mouth is speed-gated:
        # parked is always the closed Super Sonic smile; above 80 km/h the
        # locked open-mouth holds; cruise may open at 60% of the scroll period.
        lcd.fillRect(hx - 5, hy - 3, 2, 4, COL_OUTLINE)
        lcd.fillRect(hx + 3, hy - 3, 2, 4, COL_OUTLINE)
        mode = mouth_mode(speed, self.scroll)
        mx, my = hx + 1, hy + 6
        if mode == "locked_open":
            outlined_ellipse(lcd, mx, my, 5, 4, COL_MOUTH_LOCK, ow=1)
            fill_ellipse(lcd, mx, my + 1, 3, 2, COL_MOUTH_CAVITY)
        elif mode == "open":
            outlined_ellipse(lcd, mx, my, 5, 4, COL_MOUTH, ow=1)
            fill_ellipse(lcd, mx, my + 2, 3, 2, COL_TONGUE)
        else:
            thick_line(lcd, hx - 3, hy + 5, hx + 1, hy + 6, COL_OUTLINE, 2)
            thick_line(lcd, hx + 1, hy + 6, hx + 5, hy + 5, COL_OUTLINE, 2)
        nx = hx + rx - 2
        ny = hy + 2
        fill_triangle(lcd, nx, ny - 2, nx, ny + 2, nx + 5, ny, COL_OUTLINE)

    def _piece_gloves(self, lcd, torso, frame):
        _lk, _lf, _rk, _rf, _le, l_hand, _re, r_hand = frame
        ax, ay = torso[0], torso[1] - 3
        self._glove(lcd, ax + l_hand[0], ay + l_hand[1])
        self._glove(lcd, ax + r_hand[0], ay + r_hand[1])

    def _piece_arms_lean(self, lcd, torso):
        ax, ay = torso[0], torso[1] - 3
        thick_line(lcd, ax, ay, ax + 8, ay + 3, COL_OUTLINE, 2)
        thick_line(lcd, ax + 8, ay + 3, ax + 14, ay + 2, COL_OUTLINE, 2)
        thick_line(lcd, ax, ay, ax - 7, ay + 5, COL_OUTLINE, 2)
        thick_line(lcd, ax - 7, ay + 5, ax - 13, ay + 8, COL_OUTLINE, 2)

    def _piece_gloves_lean(self, lcd, torso):
        ax, ay = torso[0], torso[1] - 3
        self._glove(lcd, ax + 14, ay + 2)
        self._glove(lcd, ax - 13, ay + 8)

    def _glove(self, lcd, x, y):
        outlined_ellipse(lcd, x, y, 4, 4, COL_GLOVE, ow=1)


def make_canvas(lcd):
    # Off-screen frame, pushed once per tick so the panel never shows a clear.
    for kwargs in (
        {"w": W, "h": H, "bpp": 16, "psram": True},
        {"w": W, "h": H, "bpp": 16},
    ):
        try:
            canvas = lcd.newCanvas(**kwargs)
            if canvas is not None:
                return canvas, True
        except Exception:
            pass
    try:
        canvas = lcd.newCanvas(W, H, 16, True)
        if canvas is not None:
            return canvas, True
    except Exception:
        pass
    return lcd, False


class Dashboard:
    def __init__(self, lcd):
        self.lcd, self._use_push = make_canvas(lcd)
        self.sprite = Sprite()
        self._last_fill = {g[0]: -1 for g in GAUGES}
        self._fault_shown = None
        self._last_ms = time.ticks_ms()

    def tick(self, values, fault, now):
        dt = time.ticks_diff(now, self._last_ms)
        if dt < 0 or dt > 500:
            dt = REFRESH_MS
        self._last_ms = now
        speed = values.get("spd") or 0.0
        self.sprite.advance(speed, dt)

        lcd = self.lcd
        lcd.fillScreen(COL_BG)
        self.sprite.draw(lcd, values, now)
        self._draw_gauges(values)
        self._draw_fault(fault)
        if self._use_push:
            try:
                lcd.push(0, 0)
            except Exception:
                self._use_push = False

    def _draw_gauges(self, values):
        lcd = self.lcd
        lcd.fillRect(0, GAUGE_Y, W, GAUGE_H, 0x101012)
        for key, lo, hi, x, y, w, h, mode in GAUGES:
            raw = values.get(key)
            if raw is None:
                frac = 0.0
                fill_w = 0
            else:
                v = clamp(raw, lo, hi)
                span = hi - lo
                frac = ((v - lo) / span) if span else 0.0
                fill_w = int(frac * w)
            lcd.fillRect(x, y, w, h, COL_TRACK)
            if fill_w > 0:
                color = COL_ORANGE if mode == "orange" else value_rgb(frac)
                lcd.fillRect(x, y, fill_w, h, color)

    def _draw_fault(self, fault):
        if fault:
            fill_circle(self.lcd, DOT_X, DOT_Y, DOT_R, COL_ERR)
        self._fault_shown = fault


def init_can():
    from base import ATOMCANBase

    return ATOMCANBase(
        0,
        (CAN_RX_PIN, CAN_TX_PIN),
        ATOMCANBase.LISTEN_ONLY,
        baudrate=CAN_BITRATE,
    )


def pull_frames(bus, values):
    n = 0
    while True:
        waiting = False
        try:
            waiting = bus.any(0)
        except TypeError:
            waiting = bus.any()
        if not waiting:
            break
        try:
            msg = bus.recv(0, timeout=0)
        except TypeError:
            msg = bus.recv()
        if not msg:
            break
        data = msg[-1]
        decoded = decode_frame(msg[0], data)
        if decoded is not None:
            values[decoded[0]] = decoded[1]
        # Brake switch lives on the same 0x17C frame as RPM.
        if msg[0] == ID_RPM and data is not None and len(data) >= 5:
            values["brk"] = 1.0 if (data[4] & 0x01) else 0.0
        n += 1
        if n >= 64:
            break
    return n


def main():
    M5.begin()
    lcd = Lcd
    ui = Dashboard(lcd)
    sweep = Sweep()
    btn = find_btn()
    try:
        import ota_client
    except ImportError:
        ota_client = None

    values = {"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0, "brk": 0.0}
    last_rx_ms = 0
    last_draw_ms = 0
    last_click_ms = -1000
    pending_click = False

    bus = None
    try:
        bus = init_can()
    except Exception:
        bus = None

    while True:
        M5.update()
        now = time.ticks_ms()

        tapped = False
        if btn is not None:
            if hasattr(btn, "wasDoubleClicked"):
                try:
                    tapped = bool(btn.wasDoubleClicked())
                except Exception:
                    tapped = False
            if not tapped and hasattr(btn, "wasClicked"):
                try:
                    clicked = bool(btn.wasClicked())
                except Exception:
                    clicked = False
                if clicked:
                    if pending_click and time.ticks_diff(now, last_click_ms) <= DBL_TAP_MS:
                        tapped = True
                        pending_click = False
                    else:
                        pending_click = True
                        last_click_ms = now
            if pending_click and time.ticks_diff(now, last_click_ms) > DBL_TAP_MS:
                pending_click = False

        if tapped:
            sweep.start(now)

        if sweep.active:
            swept = sweep.step(now)
            if swept is not None:
                values.update(swept)
            fault = False
        else:
            if bus is not None:
                if pull_frames(bus, values):
                    last_rx_ms = now
            fault = bus is None or last_rx_ms == 0 or time.ticks_diff(now, last_rx_ms) > WATCHDOG_MS

        if ota_client is not None:
            try:
                ota_client.poll(now)
            except Exception:
                pass

        if time.ticks_diff(now, last_draw_ms) >= REFRESH_MS:
            ui.tick(values, fault, now)
            last_draw_ms = now


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as e:
        try:
            from utility import print_error_msg

            print_error_msg(e)
        except ImportError:
            print("GE8 gauges stopped:", e)
