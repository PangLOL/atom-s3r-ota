"""GE8 Fit F-CAN gauges + engine-sheet Sonic for M5Stack Atom S3R + Atomic CAN Base.

V8: same pre-scaled PIX6 art as V7 (Sonic 39x36, playfield 128x103, rings 14x14).
Sprites: engine-sheet Sonic cycles plus after-preview scenery / HUD / rings.
Chao, official mouths, and boost FX rips are not packed. Aura / shockwave /
barrier still use V2 primitives, layered on top of Sonic at tip (66, 72),
alpha 178. FACE_ENABLED stays off.
Companion asset pixidx.bin is fetched from the OTA host if missing, stale, or
the wrong size / geometry (a leftover V6 pack must not be used).

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
APP_VERSION = 8
APP_VERSION_NAME = "2026.11.21-v8"
BREATH_MS = 1600
BOOST_SPEED = 100.0
BOOST_END_MS = 2000
FX_FRAME_MS = 1000 // 15  # 15 fps, same as S3Vec run_fast
SONIC_MID_Y = 72
AURA_TIP_X = 66
SW_TIP_X = 76
FACE_ENABLED = False      # sheet has no face / mouth layer; stay off

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
COL_DIRT = 0xD6A656
COL_DIRT_DK = 0xBA7C32
COL_DIRT_HI = 0xE8C87C
COL_DIRT_SH = 0x683A14
COL_MORTAR = 0x5A3212
COL_GRASS_BL = 0x38B03C
COL_RING = 0xF0A018
COL_RING_HI = 0xFFE868
COL_RING_DK = 0xC06008
COL_SUN = 0xF0D030
COL_SUN_DK = 0xC07010
COL_AURA_W = 0xFFFFFF
COL_AURA_I = 0xA8E8FF
COL_AURA_M = 0x3A98FF
COL_AURA_D = 0x1E58D8
COL_HUD = 0x101018
COL_HUD_EDGE = 0xE8F0FF
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


# --- PIX6 indexed-sprite interpreter (V6) ---------------------------------
# Compact pixel-index pack. See FORMAT.md. Loaded from pixidx.bin; the live
# ota_client only replaces main.py, so the pack is a companion asset fetched
# from the same OTA host on first run. Not inlined: 141 KB would blow the
# MicroPython import budget (V2 main.py is already 44 KB).

PIXIDX_NAME = "pixidx.bin"
PIXIDX_SHA256 = "6009435444f0016110ed1c5b620b686508de15a4db10e11c3f0a5b98a5fedca8"
PIXIDX_SIZE = 107656
PIXIDX_URL = "https://panglol.github.io/atom-s3r-ota/pixidx.bin"
PIXIDX_FETCH_EVERY_MS = 90000
PIX6_MAGIC = b"PIX6"

SPEED_SLOW = 40.0
SPEED_FAST = 80.0
IDLE_TRIGGER_AFTER_MS = 3500

# Locked after-preview layout (128x128). Sonic sprite is 39x36 at top-left
# (16, 55). Frames use a feet-centre origin, so the blit dest is the exclusive
# bottom of that box: (35, 91). Feet plant on y 90. Playfield is y 0-102.
SONIC_X = 16
SONIC_Y = 55
SONIC_W = 39
SONIC_H = 36
SONIC_FEET_Y = 90
SONIC_HEAD_Y = 63
SONIC_FRONT_X = 54
SONIC_FEET_DEST_X = SONIC_X + SONIC_W // 2  # 35
SONIC_FEET_DEST_Y = SONIC_Y + SONIC_H       # 91
AURA_TIP_Y = SONIC_MID_Y  # 72
AURA_ALPHA = 178
HUD_RING_POS = (4, 3)
HUD_BAR_POS = (14, 6)
GROUND_SURFACE_IN_TILE = 0  # unused; feet dest is locked, backdrop includes ground
PROP_PERIOD = 384   # 384x103 backdrop width
RING_PERIOD = 1536  # long gaps between groups of 3-8
RING_FPS_MS = 1000 // 30  # 33

# World-x of mid-ground ring centres. First group matches the after still
# (centres 66, 82, 97 at y 78). Second group of 5 after a long gap.
FIELD_RING_X = (66, 82, 97, 866, 882, 898, 914, 930)
FIELD_RING_Y = 78

# Scenery is baked into the 384x103 backdrop. These are kept as a fallback
# only if a pack has no ocean frame (should not happen in V7).
SCENERY_PROPS = (
    ("palm_0", 5),
    ("island_0", 65),
    ("palm_1", 114),
    ("bush_0", 0),
    ("flowers_0", 102),
)


def _u16(data, o):
    return data[o] | (data[o + 1] << 8)


def _i16(data, o):
    v = _u16(data, o)
    if v >= 32768:
        return v - 65536
    return v


def _u32(data, o):
    return data[o] | (data[o + 1] << 8) | (data[o + 2] << 16) | (data[o + 3] << 24)


def _rgb565_to_888(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    r = (r << 3) | (r >> 2)
    g = (g << 2) | (g >> 4)
    b = (b << 3) | (b >> 2)
    return (r << 16) | (g << 8) | b


def _sha256_hex(data):
    """Best-effort sha256 hex. Returns None if the hash module is missing
    or the digest API is too limited — callers must not treat that as a match.
    """
    h = None
    try:
        import hashlib
        h = hashlib.sha256(data)
    except Exception:
        try:
            import uhashlib
            h = uhashlib.sha256(data)
        except Exception:
            h = None
    if h is None:
        return None
    try:
        return h.hexdigest()
    except Exception:
        try:
            digest = h.digest()
        except Exception:
            return None
        try:
            import ubinascii
            return ubinascii.hexlify(digest).decode()
        except Exception:
            return "".join("%02x" % b for b in digest)


class PixFrame:
    def __init__(self, name, data_offset, w, h, duration_ms, ox, oy, pal_id, flags):
        self.name = name
        self.data_offset = data_offset
        self.w = w
        self.h = h
        self.duration_ms = duration_ms
        self.origin_x = ox
        self.origin_y = oy
        self.pal_id = pal_id
        self.flags = flags


class PixIdx:
    """In-memory PIX6 pack. Pixel bytes stay packed; the blit loop is the interpreter."""

    def __init__(self, data):
        if data[:4] != PIX6_MAGIC:
            raise ValueError("bad PIX6 magic")
        ver = _u16(data, 4)
        flags = _u16(data, 6)
        n_pal = _u16(data, 8)
        colors = _u16(data, 10)
        n_frames = _u16(data, 12)
        pal_off = _u32(data, 16)
        tab_off = _u32(data, 20)
        pix_off = _u32(data, 24)
        if ver != 1:
            raise ValueError("bad PIX6 version")
        self.data = data
        self.bpp = 4 if (flags & 1) else 8
        self.colors = colors
        self.pix_off = pix_off
        self.palettes = []
        for p in range(n_pal):
            pal = [0] * colors
            for i in range(1, colors):
                val = _u16(data, pal_off + (p * colors + i) * 2)
                pal[i] = _rgb565_to_888(val)
            self.palettes.append(pal)
        self.frames = []
        self.by_name = {}
        for n in range(n_frames):
            eoff = tab_off + n * 32
            end = eoff
            while end < eoff + 16 and data[end] != 0:
                end += 1
            name = bytes(data[eoff:end]).decode("ascii")
            off = _u32(data, eoff + 16)
            w = _u16(data, eoff + 20)
            h = _u16(data, eoff + 22)
            dur = _u16(data, eoff + 24)
            ox = _i16(data, eoff + 26)
            oy = _i16(data, eoff + 28)
            pal_id = data[eoff + 30]
            fr_flags = data[eoff + 31]
            fr = PixFrame(name, off, w, h, dur, ox, oy, pal_id, fr_flags)
            self.frames.append(fr)
            self.by_name[name] = fr
        self.cycles = {}
        for fr in self.frames:
            name = fr.name
            if name.startswith("rings_sparkle_"):
                key = "rings_sparkle"
                idx = name[len("rings_sparkle_"):]
            elif len(name) and name[-1] >= "0" and name[-1] <= "9" and "_" in name:
                cut = name.rfind("_")
                key = name[:cut]
                idx = name[cut + 1:]
            else:
                self.cycles[name] = [fr]
                continue
            try:
                i = int(idx)
            except Exception:
                self.cycles[name] = [fr]
                continue
            bucket = self.cycles.get(key)
            if bucket is None:
                bucket = []
                self.cycles[key] = bucket
            bucket.append((i, fr))
        for key, bucket in list(self.cycles.items()):
            if bucket and isinstance(bucket[0], tuple):
                bucket.sort(key=lambda t: t[0])
                self.cycles[key] = [fr for _i, fr in bucket]

    def pal(self, pal_id):
        if pal_id < 0 or pal_id >= len(self.palettes):
            pal_id = 0
        return self.palettes[pal_id]


def blit_frame(lcd, pack, frame, dest_x, dest_y, pal, clip_t=0, clip_b=103, use_origin=True):
    """Interpreter: walk packed indices, skip 0, fillRect horizontal spans. V2 Lcd path."""
    if frame is None:
        return
    if use_origin:
        x0 = dest_x - frame.origin_x
        y0 = dest_y - frame.origin_y
    else:
        x0 = dest_x
        y0 = dest_y
    w = frame.w
    h = frame.h
    data = pack.data
    base = pack.pix_off + frame.data_offset
    bpp4 = pack.bpp == 4
    row_bytes = ((w + 1) // 2) if bpp4 else w
    for y in range(h):
        dy = y0 + y
        if dy < clip_t or dy >= clip_b:
            continue
        row = base + y * row_bytes
        x = 0
        while x < w:
            if bpp4:
                b = data[row + (x >> 1)]
                idx = (b >> 4) if (x & 1) == 0 else (b & 0x0F)
            else:
                idx = data[row + x]
            if idx == 0:
                x += 1
                continue
            x1 = x + 1
            while x1 < w:
                if bpp4:
                    b2 = data[row + (x1 >> 1)]
                    i2 = (b2 >> 4) if (x1 & 1) == 0 else (b2 & 0x0F)
                else:
                    i2 = data[row + x1]
                if i2 != idx:
                    break
                x1 += 1
            dx = x0 + x
            dw = x1 - x
            if dx < 0:
                dw += dx
                dx = 0
            if dx + dw > W:
                dw = W - dx
            if dw > 0 and idx < len(pal):
                lcd.fillRect(dx, dy, dw, 1, pal[idx])
            x = x1


def blit_tiled_x(lcd, pack, frame, y, scroll, pal, clip_t=0, clip_b=103):
    if frame is None:
        return
    tw = frame.w
    if tw <= 0:
        return
    x = -(scroll % tw)
    if x > 0:
        x -= tw
    while x < W:
        blit_frame(lcd, pack, frame, x, y, pal, clip_t, clip_b, False)
        x += tw


def _pixidx_geometry_ok(pack):
    """V7/V8 art: 384x103 backdrop and 39x36 Sonic. A leftover V6 pack fails this."""
    ocean = pack.by_name.get("ocean")
    sonic = pack.by_name.get("run_fast_0")
    if sonic is None:
        sonic = pack.by_name.get("idle_0")
    if ocean is None or ocean.w != 384 or ocean.h != 103:
        return False
    if sonic is None or sonic.w != 39 or sonic.h != 36:
        return False
    return True


def _pixidx_is_current(data, pack=None):
    """Accept only the published V7/V8 pixel-index file.

    Hash is required to match when a sha256 API is available. If hashing is
    unavailable, size plus frame geometry still reject a leftover V6 pack.
    """
    if not data or len(data) != PIXIDX_SIZE:
        return False
    got = _sha256_hex(data)
    if got is not None and got != PIXIDX_SHA256:
        return False
    if pack is None:
        try:
            pack = PixIdx(data)
        except Exception:
            return False
    return _pixidx_geometry_ok(pack)


def _read_local_pixidx():
    try:
        with open(PIXIDX_NAME, "rb") as f:
            return f.read()
    except Exception:
        return None


def try_load_pixidx():
    data = _read_local_pixidx()
    if not data:
        return None
    got = _sha256_hex(data)
    if got is not None and got != PIXIDX_SHA256:
        print("pixidx.bin sha256 mismatch")
        return None
    if len(data) != PIXIDX_SIZE:
        print("pixidx.bin size mismatch")
        return None
    try:
        pack = PixIdx(data)
    except Exception as e:
        print("pixidx parse failed:", e)
        return None
    if not _pixidx_is_current(data, pack):
        print("pixidx.bin is not the current pack")
        return None
    return pack


def try_fetch_pixidx():
    """Download pixidx.bin from the public OTA host if Wi-Fi is already up.

    Does not touch secrets.py. Relies on ota_client having joined the hotspot,
    or on whatever STA is already connected. Safe to call when the file is
    not yet published — the request fails and V2 primitives keep drawing.
    A leftover local pack is never treated as success; only the current
    sha256 / size / geometry is saved and used.
    """
    try:
        import ota_client
        if hasattr(ota_client, "wifi_connected") and not ota_client.wifi_connected():
            return None
    except Exception:
        pass
    try:
        import urequests as requests
    except ImportError:
        try:
            import requests
        except ImportError:
            return None
    try:
        resp = requests.get(PIXIDX_URL)
    except Exception:
        return None
    try:
        status = getattr(resp, "status_code", None)
        if status is None:
            status = getattr(resp, "status", 200)
        if int(status) != 200:
            try:
                resp.close()
            except Exception:
                pass
            return None
        data = resp.content
        resp.close()
    except Exception:
        try:
            resp.close()
        except Exception:
            pass
        return None
    got = _sha256_hex(data)
    if got is not None and got != PIXIDX_SHA256:
        print("fetched pixidx.bin sha256 mismatch")
        return None
    if len(data) != PIXIDX_SIZE:
        print("fetched pixidx.bin size mismatch")
        return None
    try:
        pack = PixIdx(data)
    except Exception as e:
        print("fetched pixidx parse failed:", e)
        return None
    if not _pixidx_is_current(data, pack):
        print("fetched pixidx.bin is not the current pack")
        return None
    try:
        with open(PIXIDX_NAME, "wb") as f:
            f.write(data)
    except Exception as e:
        print("pixidx save failed:", e)
        return None
    return pack


def sonic_tint_amount(clt, now_ms):
    if clt is None or clt < SWEAT_CLT:
        return 0.0
    period = float(BREATH_MS)
    phase = ((now_ms % BREATH_MS) / period) * 6.28318530718
    breath = 0.5 + 0.5 * math.sin(phase)
    return 0.42 + 0.46 * breath


def tint_blue_palette(base, amount):
    if amount <= 0:
        return base
    out = [0] * len(base)
    out[0] = 0
    for i in range(1, len(base)):
        c = base[i]
        r = (c >> 16) & 0xFF
        g = (c >> 8) & 0xFF
        b = c & 0xFF
        if b > r + 20 and b > g + 10:
            out[i] = mix_rgb(c, COL_HEAT, amount)
        else:
            out[i] = c
    return out

# --- end PIX6 interpreter --------------------------------------------------

class Sweep:
    """Speed 0 → 100 → 80 → 0, with matching RPM / throttle / coolant."""

    # (duration_ms, start_speed, end_speed, kind)
    STAGES = (
        (4000, 0.0, 120.0, "accel"),
        (800, 120.0, 120.0, "boost_hold"),
        (1200, 120.0, 80.0, "settle"),
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
        elif kind == "boost_hold":
            tps = 220.0
            rpm = 800.0 + 36.0 * speed
            clt = 86.0
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



# Mid-ground gold rings in the 180 px period (drawn, not ripped).
# x, y, rx, ry
FIELD_RINGS = (
    (36, 78, 7, 8),
    (68, 78, 7, 8),
    (100, 78, 7, 8),
    (154, 70, 6, 7),
)

# Sunflowers sitting in the grass: x, stem_h
SUNFLOWERS = (
    (112, 10),
    (124, 12),
    (174, 9),
)

# 5x7 bitmap digits for the ring HUD, with a 1px dark outline.
DIGITS = (
    (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    (0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111),
    (0b01110, 0b10001, 0b00001, 0b00110, 0b00001, 0b10001, 0b01110),
    (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    (0b01110, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b01110),
    (0b11111, 0b00001, 0b00010, 0b00100, 0b00100, 0b00100, 0b00100),
    (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    (0b01110, 0b10001, 0b10001, 0b10001, 0b01111, 0b00001, 0b01110),
)
COL_HUD_DIGIT = 0xF8F8F8
COL_HUD_OUTLINE = 0x101018


def draw_digit(lcd, x, y, n, color=None, s=1, outline=None):
    n = int(n) % 10
    bits = DIGITS[n]
    if color is None:
        color = COL_HUD_DIGIT
    if outline is None:
        outline = COL_HUD_OUTLINE
    # outline first
    for row in range(7):
        rowb = bits[row]
        for col in range(5):
            if rowb & (0b10000 >> col):
                for oy in (-1, 0, 1):
                    for ox in (-1, 0, 1):
                        if ox == 0 and oy == 0:
                            continue
                        lcd.fillRect(x + col * s + ox, y + row * s + oy, s, s, outline)
    for row in range(7):
        rowb = bits[row]
        for col in range(5):
            if rowb & (0b10000 >> col):
                lcd.fillRect(x + col * s, y + row * s, s, s, color)


def draw_ring_glyph(lcd, cx, cy, rx, ry):
    outlined_ellipse(lcd, cx, cy, rx, ry, COL_RING, COL_RING_DK, 1)
    fill_ellipse(lcd, cx, cy, max(1, rx - 2), max(1, ry - 2), COL_BG)
    # restore a small highlight on the gold rim
    lcd.fillRect(cx - 1, cy - ry, 2, 1, COL_RING_HI)


def _chevron_column(lcd, x, cy, half_h, width, color, steps=4):
    if half_h < 2 or width < 2:
        return
    step = max(3, (half_h * 2) // steps)
    y = cy - half_h
    end = cy + half_h
    while y < end:
        y2 = y + step
        if y2 > end:
            y2 = end
        midy = (y + y2) // 2
        fill_triangle(lcd, x, y, x + width, midy, x, y2, color)
        y = y2


def draw_aura(lcd, tip_x, tip_y, frame):
    """Original 4-frame teardrop aura. Tip sits at Sonic mid-height."""
    # 0 initiate (greener), 1 transition, 2-3 cycle (blue).
    pals = (
        (0xC8F090, 0x6ED050, 0xFFFFFF, 0xE8FFE0),
        (0x50B0FF, 0x2E78F0, 0xFFFFFF, 0xA0E0FF),
        (0x3A98FF, 0x1E58D8, 0xFFFFFF, 0x7ED0FF),
        (0x2A80F0, 0x1848C0, 0xFFFFFF, 0x6EC4FF),
    )
    lo, mid, hi, ice = pals[frame % 4]
    scale = (0.72, 0.90, 1.0, 1.0)[frame % 4]
    rx = int(20 * scale)
    ry = int(13 * scale)
    cx = tip_x - rx + 2
    cy = tip_y
    fill_ellipse(lcd, cx, cy, rx + 1, ry + 1, mid)
    fill_triangle(lcd, cx + rx - 5, cy - 6, tip_x + 1, cy, cx + rx - 5, cy + 6, mid)
    fill_ellipse(lcd, cx + 2, cy, max(2, rx - 5), max(2, ry - 4), lo)
    fill_ellipse(lcd, cx + 6, cy, max(2, rx - 11), max(2, ry - 7), hi)
    shift = (0, 2, 4, 1)[frame % 4]
    for i, col in enumerate((hi, ice, mid)):
        _chevron_column(lcd, cx - 2 + i * 5 + shift, cy, max(4, ry - 2), 5, col, steps=4)
    back = cx - rx
    spikes = ((-8, 8), (-3, 12), (3, 11), (8, 7))
    for dy, length in spikes:
        col = lo if dy < 0 else mid
        fill_triangle(lcd, back + 2, cy + dy, back - length, cy + dy, back + 2, cy + dy + 3, col)


def draw_shockwave(lcd, tip_x, tip_y, frame):
    """Original 5-frame shockwave. Frame 5 (index 4) is the dissipating burst."""
    if frame < 0 or frame > 4:
        return
    heights = (8, 14, 22, 30, 34)
    widths = (6, 8, 10, 12, 14)
    h = heights[frame]
    w = widths[frame]
    if frame < 4:
        _chevron_column(lcd, tip_x - 1, tip_y, h, w, COL_AURA_W, steps=4 + frame)
        _chevron_column(lcd, tip_x - 6, tip_y, max(4, h - 3), w - 2, COL_AURA_I, steps=4)
        _chevron_column(lcd, tip_x - 11, tip_y, max(4, h - 6), max(3, w - 3), COL_AURA_M, steps=3)
        if frame >= 2:
            _chevron_column(lcd, tip_x + 3, tip_y, max(4, h - 8), 4, COL_AURA_I, steps=3)
    else:
        # Dissipating burst: fragments around the mid-height tip.
        frags = (
            (-18, -16, 6, COL_AURA_I), (-10, -22, 5, COL_AURA_W), (4, -18, 5, COL_AURA_M),
            (10, -8, 5, COL_AURA_W), (-20, 8, 5, COL_AURA_M), (-8, 18, 6, COL_AURA_W),
            (8, 16, 4, COL_AURA_I), (12, 4, 5, COL_AURA_M), (-24, -4, 4, COL_AURA_I),
            (6, 22, 3, COL_AURA_W), (-14, 20, 4, COL_AURA_M), (14, -20, 4, COL_AURA_I),
        )
        for dx, dy, s, col in frags:
            fill_triangle(lcd, tip_x + dx, tip_y + dy, tip_x + dx - s, tip_y + dy - 2, tip_x + dx - 2, tip_y + dy + s, col)


def draw_barrier(lcd, tip_x, tip_y, frame):
    """Original sound-barrier after the end shockwave. 6 short frames."""
    if frame < 0 or frame > 5:
        return
    # Grow a thin vertical ripple, then streak it forward and fade.
    if frame <= 2:
        h = 6 + frame * 6
        x = tip_x + 2 + frame
        _chevron_column(lcd, x, tip_y, h, 4, COL_AURA_W, steps=3)
        _chevron_column(lcd, x - 4, tip_y, max(4, h - 3), 3, COL_AURA_I, steps=3)
    else:
        k = frame - 2
        x = tip_x + 6 + k * 8
        h = 22 - k * 4
        _chevron_column(lcd, x, tip_y, max(4, h), 3, COL_AURA_I, steps=3)
        fill_triangle(lcd, x + 2, tip_y - h, x + 8 + k, tip_y - h + 2, x + 2, tip_y - h + 6, COL_AURA_W)
        fill_triangle(lcd, x + 2, tip_y + h, x + 8 + k, tip_y + h - 2, x + 2, tip_y + h - 6, COL_AURA_M)


class BoostFX:
    """Boost aura / shockwave / sound-barrier state machine, 15 fps."""

    def __init__(self):
        self.initiated = False
        self.phase = "idle"  # idle, run, end_sw, barrier
        self.accum = 0
        self.below_ms = 0
        self.aura_i = -1
        self.sw_i = -1
        self.bar_i = -1
        self.start_sw_done = False
        self.ticks = 0

    def reset(self):
        self.initiated = False
        self.phase = "idle"
        self.accum = 0
        self.below_ms = 0
        self.aura_i = -1
        self.sw_i = -1
        self.bar_i = -1
        self.start_sw_done = False
        self.ticks = 0

    def tick(self, speed, dt_ms):
        if speed > BOOST_SPEED:
            self.below_ms = 0
            if not self.initiated or self.phase in ("end_sw", "barrier", "idle"):
                # (Re)initiate: aura 1 + shockwave once on top.
                self.initiated = True
                self.phase = "run"
                self.accum = 0
                self.ticks = 0
                self.aura_i = 0
                self.sw_i = 0
                self.bar_i = -1
                self.start_sw_done = False
                return
        else:
            if self.initiated and self.phase == "run":
                self.below_ms += dt_ms
                if self.below_ms >= BOOST_END_MS:
                    self.phase = "end_sw"
                    self.accum = 0
                    self.ticks = 0
                    self.aura_i = -1  # hide aura
                    self.sw_i = 0
                    self.bar_i = -1
            elif self.phase == "idle":
                self.below_ms = 0

        if self.phase == "idle":
            return

        self.accum += dt_ms
        # Cap catch-up so a hitch does not skip the end animation.
        steps = 0
        while self.accum >= FX_FRAME_MS and steps < 3:
            self.accum -= FX_FRAME_MS
            self._step_frame()
            steps += 1
        if self.accum > FX_FRAME_MS * 3:
            self.accum = FX_FRAME_MS * 2

    def _step_frame(self):
        self.ticks += 1
        if self.phase == "run":
            # Aura: frame 1, then 2, then cycle 3 and 4.
            if self.ticks == 1:
                self.aura_i = 1
            elif self.ticks >= 2:
                # ticks 2+ : cycle frames 2 and 3 (aura 3 and 4)
                self.aura_i = 2 + ((self.ticks - 2) & 1)
            if not self.start_sw_done:
                if self.sw_i < 4:
                    self.sw_i += 1
                else:
                    self.sw_i = -1
                    self.start_sw_done = True
        elif self.phase == "end_sw":
            if self.sw_i < 4:
                self.sw_i += 1
            else:
                self.phase = "barrier"
                self.sw_i = -1
                self.bar_i = 0
        elif self.phase == "barrier":
            if self.bar_i < 5:
                self.bar_i += 1
            else:
                self.reset()

    def draw_behind(self, lcd, tip_x, tip_y):
        if self.aura_i >= 0:
            draw_aura(lcd, tip_x, tip_y, self.aura_i)

    def draw_front(self, lcd, tip_x, tip_y):
        if self.sw_i >= 0:
            draw_shockwave(lcd, tip_x, tip_y, self.sw_i)
        if self.bar_i >= 0:
            draw_barrier(lcd, tip_x, tip_y, self.bar_i)


class Sprite:
    """Composited chibi Sonic: separate pieces, swapped by speed, retimed by phase."""

    def __init__(self, pack=None):
        self.scroll = 0.0
        self.phase = 0.0
        self.ground_y = GAUGE_Y - 2
        self.body_col = COL_SONIC
        self.boost = BoostFX()
        self.pack = pack
        self.rings = 0
        self._ring_seen = 0
        self.anim_tag = "idle"
        self.anim_i = 0
        self.anim_accum = 0
        self.idle_wait = 0
        self.triggering = False
        self.ring_i = 0
        self.ring_accum = 0
        self.sparkles = []  # [x, y, i, accum]
        self._surface = GAUGE_Y - 2
        self._ground_top = GAUGE_Y - 2
        self._sync_ground()

    def _sync_ground(self):
        pack = self.pack
        if pack is None:
            self._ground_top = GAUGE_Y - 2
            self._surface = GAUGE_Y - 2
            self.ground_y = self._surface
            return
        # Locked after-preview feet dest, independent of the ground tile.
        self._surface = SONIC_FEET_DEST_Y
        self._ground_top = GAUGE_Y - 15
        self.ground_y = self._surface

    def advance(self, speed, dt_ms):
        if speed is None:
            speed = 0.0
        period = float(RING_PERIOD if self.pack is not None else PERIOD)
        self.scroll = (self.scroll + speed * dt_ms * 0.012) % period
        if speed < 1.0:
            self.phase = 0.0
        else:
            self.phase = (self.phase + dt_ms * (0.002 + 0.00035 * speed)) % 4.0
        self.boost.tick(speed, dt_ms)
        self._step_anim(speed, dt_ms)
        self._collect_rings()
        self._step_sparkles(dt_ms)

    def _cycle_frames(self, tag):
        pack = self.pack
        if pack is None:
            return None
        return pack.cycles.get(tag)

    def _step_anim(self, speed, dt_ms):
        pack = self.pack
        if pack is None:
            return
        if speed < 1.0:
            if self.triggering:
                tag = "idle_trigger"
            else:
                tag = "idle"
                self.idle_wait += dt_ms
                if self.idle_wait >= IDLE_TRIGGER_AFTER_MS:
                    self.triggering = True
                    self.anim_i = 0
                    self.anim_accum = 0
                    tag = "idle_trigger"
        else:
            self.idle_wait = 0
            self.triggering = False
            if speed < SPEED_SLOW:
                tag = "run_slow"
            elif speed < SPEED_FAST:
                tag = "run_medium"
            else:
                tag = "run_fast"
        if tag != self.anim_tag:
            self.anim_tag = tag
            self.anim_i = 0
            self.anim_accum = 0
        frames = self._cycle_frames(self.anim_tag)
        if not frames:
            return
        if self.anim_i >= len(frames):
            self.anim_i = 0
        dur = frames[self.anim_i].duration_ms
        if dur <= 0:
            dur = RING_FPS_MS
        self.anim_accum += dt_ms
        while self.anim_accum >= dur:
            self.anim_accum -= dur
            self.anim_i += 1
            if self.anim_i >= len(frames):
                self.anim_i = 0
                if self.anim_tag == "idle_trigger":
                    self.triggering = False
                    self.anim_tag = "idle"
                    self.idle_wait = 0
                    break
            frames = self._cycle_frames(self.anim_tag)
            if not frames:
                break
            dur = frames[self.anim_i].duration_ms
            if dur <= 0:
                dur = RING_FPS_MS
        # Spin the mid-ground rings at 30 fps regardless of Sonic's cycle.
        self.ring_accum += dt_ms
        while self.ring_accum >= RING_FPS_MS:
            self.ring_accum -= RING_FPS_MS
            self.ring_i = (self.ring_i + 1) & 7

    def _step_sparkles(self, dt_ms):
        if not self.sparkles:
            return
        pack = self.pack
        frames = self._cycle_frames("rings_sparkle") if pack is not None else None
        n = len(frames) if frames else 0
        kept = []
        for item in self.sparkles:
            x, y, i, acc = item
            acc += dt_ms
            while acc >= RING_FPS_MS:
                acc -= RING_FPS_MS
                i += 1
            if n and i < n:
                kept.append([x, y, i, acc])
        self.sparkles = kept


    def _collect_rings(self):
        # A ring counts when its scrolled centre crosses Sonic's front.
        off = int(self.scroll)
        mask = 0
        if self.pack is not None:
            mid = SONIC_FRONT_X
            period = RING_PERIOD
            for i, rx in enumerate(FIELD_RING_X):
                x = (rx - off) % period
                if x <= mid:
                    mask |= 1 << i
        else:
            mid = AURA_TIP_X
            period = PERIOD
            for i, (rx, ry, _a, _b) in enumerate(FIELD_RINGS):
                x = (rx - off) % period
                if x <= mid:
                    mask |= 1 << i
        gained = mask & ~self._ring_seen
        if gained:
            n = 0
            v = gained
            while v:
                n += v & 1
                v >>= 1
            self.rings = (self.rings + n) % 1000
            if self.pack is not None:
                period = RING_PERIOD
                for i, rx in enumerate(FIELD_RING_X):
                    if (gained >> i) & 1:
                        x = (rx - off) % period
                        self.sparkles.append([x, FIELD_RING_Y, 0, 0])
        self._ring_seen = mask

    def _draw_ring_hud(self, lcd):
        # Compact original ring + digits. Kept in the playfield, not in the gauges.
        # Offset from the CAN-fault dot in the upper-left corner.
        pack = self.pack
        if pack is not None:
            pal = pack.pal(1)
            bar = pack.by_name.get("hud_bar")
            ring = pack.by_name.get("hud_ring")
            bx, by = HUD_BAR_POS
            if bar is not None:
                blit_frame(lcd, pack, bar, bx, by, pal, 0, GAUGE_Y, False)
            if ring is not None:
                blit_frame(lcd, pack, ring, HUD_RING_POS[0], HUD_RING_POS[1], pal, 0, GAUGE_Y, False)
            dx, dy = bx + 22, by + 4
            n = int(self.rings) % 1000
            draw_digit(lcd, dx + 14, dy, n % 10)
            if n >= 10:
                draw_digit(lcd, dx + 7, dy, (n // 10) % 10)
            if n >= 100:
                draw_digit(lcd, dx, dy, (n // 100) % 10)
            return
        # V2 primitive fallback
        x, y = 16, 3
        draw_ring_glyph(lcd, x + 5, y + 6, 5, 6)
        lcd.fillRect(x + 12, y + 1, 26, 12, COL_HUD)
        n = int(self.rings) % 1000
        draw_digit(lcd, x + 24, y + 3, n % 10, COL_HUD_EDGE, 1)
        if n >= 10:
            draw_digit(lcd, x + 19, y + 3, (n // 10) % 10, COL_HUD_EDGE, 1)
        if n >= 100:
            draw_digit(lcd, x + 14, y + 3, (n // 100) % 10, COL_HUD_EDGE, 1)

    def _draw_sunflower(self, lcd, x, gy, h):
        stem_x, top = x, gy - h
        lcd.fillRect(stem_x, top, 2, h, COL_PALM)
        fill_circle(lcd, stem_x, top, 3, COL_SUN)
        fill_circle(lcd, stem_x, top, 1, COL_SUN_DK)
        # four chunky petals
        fill_circle(lcd, stem_x - 3, top, 2, COL_SUN)
        fill_circle(lcd, stem_x + 3, top, 2, COL_SUN)
        fill_circle(lcd, stem_x, top - 3, 2, COL_SUN)
        fill_circle(lcd, stem_x, top + 3, 2, COL_SUN)

    def _draw_field_ring(self, lcd, cx, cy, rx, ry):
        outlined_ellipse(lcd, cx, cy, rx, ry, COL_RING, COL_RING_DK, 1)
        fill_ellipse(lcd, cx, cy, max(1, rx - 3), max(1, ry - 3), COL_WATER)
        lcd.fillRect(cx - 1, cy - ry + 1, 2, 1, COL_RING_HI)

    def draw(self, lcd, values, now=0):
        speed = values.get("spd") or 0.0
        tps = values.get("tps") or 0.0
        clt = values.get("clt") or 0.0
        self.body_col = sonic_body_color(clt, now)
        if self.pack is not None:
            self._sync_ground()
            self._draw_sheet_scene(lcd, speed, clt, now)
        else:
            self._draw_background(lcd)
            tip_y = SONIC_MID_Y
            self._draw_sonic(lcd, speed, tps, clt)
            # Aura is layered on top of Sonic at the locked tip.
            self.boost.draw_behind(lcd, AURA_TIP_X, tip_y)
            self.boost.draw_front(lcd, SW_TIP_X, tip_y)
        self._draw_ring_hud(lcd)

    def _draw_sheet_scene(self, lcd, speed, clt, now):
        pack = self.pack
        pal_s = pack.pal(1)
        pal_c = pack.pal(0)
        pal_b = pack.pal(2) if len(pack.palettes) > 2 else pal_s
        amount = sonic_tint_amount(clt, now)
        if amount > 0:
            pal_c = tint_blue_palette(pal_c, amount)
        ocean = pack.by_name.get("ocean")
        off = int(self.scroll)
        # Backdrop already includes sky, water, scenery and the grass/dirt band.
        # Scroll 1:1 with the mid-ground rings so the after still stays aligned.
        if ocean is not None:
            blit_tiled_x(lcd, pack, ocean, 0, off, pal_b, 0, GAUGE_Y)
        ring_frames = pack.cycles.get("rings")
        if ring_frames:
            fr = ring_frames[self.ring_i % len(ring_frames)]
            ry = FIELD_RING_Y
            period = RING_PERIOD
            for wx in FIELD_RING_X:
                x = (wx - off) % period
                if x > W + 40:
                    x -= period
                if -40 < x < W + 40:
                    blit_frame(lcd, pack, fr, x, ry, pal_s, 0, GAUGE_Y, True)
        spark_frames = pack.cycles.get("rings_sparkle")
        if spark_frames:
            for item in self.sparkles:
                x, y, i, _acc = item
                if i < len(spark_frames):
                    blit_frame(lcd, pack, spark_frames[i], int(x), int(y), pal_s, 0, GAUGE_Y, True)
        frames = self._cycle_frames(self.anim_tag)
        if frames:
            fr = frames[self.anim_i % len(frames)]
            blit_frame(lcd, pack, fr, SONIC_FEET_DEST_X, SONIC_FEET_DEST_Y, pal_c, 0, GAUGE_Y, True)
        # Aura / shockwave on top of Sonic, tip 12 px ahead at mid_y 72.
        self.boost.draw_behind(lcd, AURA_TIP_X, AURA_TIP_Y)
        self.boost.draw_front(lcd, SW_TIP_X, AURA_TIP_Y)

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
        sh = int(self.scroll * 0.25) % 6
        lcd.fillRect(0, WATER_Y + 5, W, 1, COL_WATER_HI if sh < 3 else COL_WATER)
        lcd.fillRect(0, WATER_Y + 8, W, 1, COL_WATER if sh < 3 else COL_WATER_HI)

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

        # After-style checker dirt with a bevel, plus a denser grass crown.
        start = -(off % TILE)
        x = start
        dirt_y = gy - 2
        grass_y = gy - GRASS_H
        while x < W:
            cx = ((x + off) // TILE) & 1
            on = cx == 0
            # grass band
            lcd.fillRect(x, grass_y, TILE, GRASS_H - 2, COL_GRASS if on else COL_GRASS_DK)
            # bevelled dirt cell
            base = COL_DIRT if on else COL_DIRT_DK
            lcd.fillRect(x, dirt_y, TILE, 3, base)
            lcd.fillRect(x, dirt_y, TILE, 1, COL_DIRT_HI if on else COL_DIRT)
            lcd.fillRect(x, dirt_y + 2, TILE, 1, COL_DIRT_SH)
            lcd.fillRect(x, dirt_y, 1, 3, COL_MORTAR)
            x += TILE
        # vertical grass blades so the crown reads after-like at 128
        bx = -(off % 3)
        while bx < W:
            h = 3 + ((bx + off) & 3)
            col = COL_GRASS_BL if ((bx + off) & 1) == 0 else COL_GRASS
            lcd.fillRect(bx, grass_y - h + 2, 1, h, col)
            bx += 3

        for fx, col in FLOWERS:
            for x in self._wrapped(fx, 3, off):
                lcd.fillRect(x, gy - GRASS_H - 1, 2, 2, col)

        for sx, sh in SUNFLOWERS:
            for x in self._wrapped(sx, 8, off):
                self._draw_sunflower(lcd, x, gy - GRASS_H, sh)

        for rx, ry, rrx, rry in FIELD_RINGS:
            for x in self._wrapped(rx, rrx * 2, off):
                self._draw_field_ring(lcd, x, ry, rrx, rry)

    def _draw_loop(self, lcd, cx, gy):
        # Floating after-style island: dirt strata, grass cap, loop opening.
        cy = gy - LOOP_RO
        # island block under / around the loop
        x0, x1 = cx - LOOP_RO - 6, cx + LOOP_RO + 6
        top = cy + LOOP_RI - 2
        bot = gy - GRASS_H + 2
        lcd.fillRect(x0, top, x1 - x0, bot - top, COL_DIRT_DK)
        lcd.fillRect(x0, top + 4, x1 - x0, 3, COL_DIRT)
        lcd.fillRect(x0, bot - 3, x1 - x0, 3, COL_MORTAR)
        fill_ellipse(lcd, cx, top + 1, LOOP_RO + 6, 5, COL_GRASS_DK)
        fill_ellipse(lcd, cx, top - 1, LOOP_RO + 5, 4, COL_GRASS)
        # the loop itself
        fill_ellipse(lcd, cx, cy, LOOP_RO + 1, LOOP_RO + 1, COL_LOOP_DK)
        fill_ellipse(lcd, cx, cy, LOOP_RO, LOOP_RO, COL_LOOP)
        fill_ellipse(lcd, cx, cy, LOOP_RI, LOOP_RI, COL_WATER)
        # grass cap on the top of the ring
        fill_ellipse(lcd, cx - 8, cy - LOOP_RO + 2, 6, 3, COL_GRASS)
        fill_ellipse(lcd, cx, cy - LOOP_RO + 1, 8, 4, COL_GRASS)
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
    def __init__(self, lcd, pack=None):
        self.lcd, self._use_push = make_canvas(lcd)
        self.sprite = Sprite(pack)
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
    pack = try_load_pixidx()
    ui = Dashboard(lcd, pack)
    sweep = Sweep()
    btn = find_btn()
    try:
        import ota_client
    except ImportError:
        ota_client = None
    _last_fetch_ms = -PIXIDX_FETCH_EVERY_MS

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

        if ui.sprite.pack is None and time.ticks_diff(now, _last_fetch_ms) >= PIXIDX_FETCH_EVERY_MS:
            # Keep retrying while the current pack is missing or was rejected
            # (a leftover V6 file must not block the download).
            wifi_ok = False
            if ota_client is not None and hasattr(ota_client, "wifi_connected"):
                try:
                    wifi_ok = bool(ota_client.wifi_connected())
                except Exception:
                    wifi_ok = False
            if wifi_ok or ota_client is None:
                _last_fetch_ms = now
                try:
                    fetched = try_fetch_pixidx()
                except Exception:
                    fetched = None
                if fetched is not None:
                    ui.sprite.pack = fetched
                    ui.sprite._sync_ground()

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
