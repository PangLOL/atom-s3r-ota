"""GE8 Fit F-CAN gauges + engine-sheet Sonic for M5Stack Atom S3R + Atomic CAN Base.

V13: the V12 picture. Official Sega artwork may be packed into pixidx.bin
and flashed. A local pixidx.bin is kept as flashed: hash, byte size, and
sprite size do not reject a PIX6 pack, and a later fetch does not
overwrite the file.
Chao, mouth, aura, shockwave, and barrier frames in that pack are drawn.
FACE_ENABLED is on, so a packed mouth follows speed. When those frames
are absent, aura / shockwave / barrier stay on the V2 primitives.

V12: the V11 picture, with on-device timing and skipped redraws.
Same pre-scaled PIX6 art (Sonic 39x36, playfield 128x103, rings 14x14).
At pack load the 384x103 ocean tile is pre-rendered into an RGB565 buffer
(~79 KB) and split into parallax layers: sky/clouds at 0.25, water and
islands at 0.6, ground and near palms at 1.0. The packed art has no
mountain band, so 0.4 is unused. Sonic cycles, the HUD ring and bar, field
rings, and ring sparkles are baked once into RGB565 plus a 1-bit mask.
Each frame still pushes one 128x103 playfield image. A band is recopied
only when int(scroll * factor) changes. A scenery stamp is rebuilt only
inside the rectangles a shifted band or a moved stamp actually dirtied,
so the window stays pixel-identical to a full V11 compose. Unchanged
sprite frames are left in place; a small underlay restores the backdrop
under a sprite that did change (animation, spinning rings, sparkles, or
the overheat tint). Parked, the parallax compose is skipped. Every two
seconds a timing line prints average compose / sprite-stamp / push
milliseconds, one GC sample, total, fps, and how many frames held the
backdrop. That print does not rebuild the ocean or the sprite cache.
If the underlay cannot be allocated, drawing stays on the V11 full
compose. If a stamp fails later, the bake flag is cleared and drawing
stays on the fillRect interpreter. This file is version 13.
The face button is polled every main-loop iteration and during a draw, so a
450 ms double-press can be caught even if a frame runs long.
Packed boost FX is layered on top of Sonic at tip (66, 72). Primitive
aura still uses alpha 178 when the pack has no aura frames.
Companion asset pixidx.bin is fetched from the OTA host only when the
device has no usable local PIX6 file.

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
APP_VERSION = 13
APP_VERSION_NAME = "2026.11.25-v13"
BREATH_MS = 1600
BOOST_SPEED = 100.0
BOOST_END_MS = 2000
FX_FRAME_MS = 1000 // 15  # 15 fps, same as S3Vec run_fast
SONIC_MID_Y = 72
AURA_TIP_X = 66
SW_TIP_X = 76
FACE_ENABLED = True       # draw mouth_* frames when the pack includes them
CHAO_FRAME_MS = 500
CHAO_FOLLOW_X = 10
CHAO_KINDS = ("normal", "amy", "knuckles", "sonic", "tails")
MOUTH_FRAMES = {
    "closed": "mouth_closed",
    "open": "mouth_open",
    "locked_open": "mouth_locked",
}

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
PIXIDX_SHA256 = "dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e"
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
# V2 fallback ratios, applied to bands split from the prerendered ocean.
PARALLAX_CLOUDS = 0.25
PARALLAX_MOUNTAINS = 0.4  # packed art has no mountain band
PARALLAX_WATER = 0.6
PARALLAX_GROUND = 1.0
HORIZON_Y = 32   # sky/clouds above; water and the horizon line from here
GRASS_Y = 88     # near ground (grass, dirt, planted palms) from here
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
        self.palettes_565 = []
        for p in range(n_pal):
            pal = [0] * colors
            pal565 = [0] * colors
            for i in range(1, colors):
                val = _u16(data, pal_off + (p * colors + i) * 2)
                pal565[i] = val
                pal[i] = _rgb565_to_888(val)
            self.palettes.append(pal)
            self.palettes_565.append(pal565)
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


def _rgb888_to_565(c):
    r = (c >> 16) & 0xFF
    g = (c >> 8) & 0xFF
    b = c & 0xFF
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def prerender_ocean_rgb565(pack, frame, bg_color=COL_BG):
    """Bake the ocean tile into a little-endian RGB565 buffer once at pack load.

    Index 0 (transparent in the sprite blitter) is filled with the sky
    background so a window copy is fully opaque.
    """
    w = frame.w
    h = frame.h
    buf = bytearray(w * h * 2)
    pal_id = frame.pal_id
    if pal_id < 0 or pal_id >= len(pack.palettes_565):
        pal_id = 0
    pal565 = pack.palettes_565[pal_id]
    bg = _rgb888_to_565(bg_color)
    pairs = [bytes((bg & 0xFF, (bg >> 8) & 0xFF))] * pack.colors
    for i in range(1, min(len(pal565), pack.colors)):
        v = pal565[i]
        pairs[i] = bytes((v & 0xFF, (v >> 8) & 0xFF))
    data = pack.data
    base = pack.pix_off + frame.data_offset
    bpp4 = pack.bpp == 4
    row_bytes = ((w + 1) // 2) if bpp4 else w
    di = 0
    for y in range(h):
        row = base + y * row_bytes
        for x in range(w):
            if bpp4:
                b = data[row + (x >> 1)]
                idx = (b >> 4) if (x & 1) == 0 else (b & 0x0F)
            else:
                idx = data[row + x]
            if idx >= len(pairs):
                idx = 0
            pair = pairs[idx]
            buf[di] = pair[0]
            buf[di + 1] = pair[1]
            di += 2
    return buf, w, h


def copy_ocean_window(tile, tw, th, scroll, out, vw=W):
    """Wrap-copy a vw-wide window of the 384-wide RGB565 tile into out."""
    off = int(scroll) % tw
    if off < 0:
        off += tw
    n1 = tw - off
    if n1 > vw:
        n1 = vw
    end1 = n1 * 2
    rest = (vw - n1) * 2
    for y in range(th):
        src = y * tw * 2
        dst = y * vw * 2
        out[dst:dst + end1] = tile[src + off * 2:src + off * 2 + end1]
        if rest:
            out[dst + end1:dst + end1 + rest] = tile[src:src + rest]


def blit_ocean_window(lcd, tile, tw, th, scroll, window, vw=W):
    """Copy the visible 128-wide window, then push it as one image."""
    copy_ocean_window(tile, tw, th, scroll, window, vw)
    if hasattr(lcd, "pushImage"):
        try:
            lcd.pushImage(0, 0, vw, th, window)
            return True
        except Exception:
            pass
    if hasattr(lcd, "writePixels"):
        try:
            if hasattr(lcd, "setAddrWindow"):
                lcd.setAddrWindow(0, 0, vw - 1, th - 1)
            lcd.writePixels(window)
            return True
        except Exception:
            pass
    return False



def push_rgb565_window(lcd, window, vw, th):
    """Push a precomposed RGB565 window as one image copy."""
    if hasattr(lcd, "pushImage"):
        try:
            lcd.pushImage(0, 0, vw, th, window)
            return True
        except Exception:
            pass
    if hasattr(lcd, "writePixels"):
        try:
            if hasattr(lcd, "setAddrWindow"):
                lcd.setAddrWindow(0, 0, vw - 1, th - 1)
            lcd.writePixels(window)
            return True
        except Exception:
            pass
    return False


def _565_at(buf, tw, x, y):
    i = (y * tw + x) * 2
    return buf[i] | (buf[i + 1] << 8)


def _set_565(buf, tw, x, y, val):
    i = (y * tw + x) * 2
    buf[i] = val & 0xFF
    buf[i + 1] = (val >> 8) & 0xFF


def _is_backdrop_888(c):
    r = (c >> 16) & 0xFF
    g = (c >> 8) & 0xFF
    b = c & 0xFF
    if b >= 240 and 120 <= g <= 210 and r <= 40:
        return True
    if b >= 180 and g >= 160:
        return True
    return False


def _copy_rows(tile, tw, y0, y1, scroll, out, vw, out_y):
    off = int(scroll) % tw
    if off < 0:
        off += tw
    width_y = y1 - y0
    n1 = tw - off
    if n1 > vw:
        n1 = vw
    end1 = n1 * 2
    rest = (vw - n1) * 2
    for row in range(width_y):
        src = (y0 + row) * tw * 2
        dst = (out_y + row) * vw * 2
        out[dst:dst + end1] = tile[src + off * 2:src + off * 2 + end1]
        if rest:
            out[dst + end1:dst + end1 + rest] = tile[src:src + rest]


def _object_mask(tile, tw, th, grass_y):
    n = tw * grass_y
    mask = bytearray(n)
    for y in range(grass_y):
        row = y * tw
        for x in range(tw):
            c = _rgb565_to_888(_565_at(tile, tw, x, y))
            if not _is_backdrop_888(c):
                mask[row + x] = 1
    return mask


def _components(mask, tw, grass_y):
    n = tw * grass_y
    seen = bytearray(n)
    comps = []
    neigh = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    for i0 in range(n):
        if mask[i0] == 0 or seen[i0]:
            continue
        stack = [i0]
        seen[i0] = 1
        pts = []
        while stack:
            i = stack.pop()
            pts.append(i)
            y = i // tw
            x = i - y * tw
            for dy, dx in neigh:
                ny = y + dy
                nx = x + dx
                if nx < 0:
                    nx += tw
                elif nx >= tw:
                    nx -= tw
                if ny < 0 or ny >= grass_y:
                    continue
                j = ny * tw + nx
                if mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        if len(pts) >= 8:
            comps.append(pts)
    return comps


def _pts_bbox(pts, tw, wrap_ok=True):
    y0 = 10000
    y1 = -1
    xs = []
    for i in pts:
        y = i // tw
        x = i - y * tw
        if y < y0:
            y0 = y
        if y > y1:
            y1 = y
        xs.append(x)
    xs.sort()
    x_min = xs[0]
    x_max = xs[-1]
    width = x_max - x_min + 1
    wrapped = wrap_ok and (x_max - x_min > tw // 2)
    if wrapped:
        # width = tw - largest gap
        largest = xs[0] + tw - xs[-1]
        for k in range(len(xs) - 1):
            gap = xs[k + 1] - xs[k]
            if gap > largest:
                largest = gap
        width = tw - largest + 1
    return x_min, x_max, y0, y1, width, wrapped


def _row_widths(pts, tw):
    # returns dict-like list of (y, width) by scanning
    counts = {}
    for i in pts:
        y = i // tw
        counts[y] = counts.get(y, 0) + 1
    return counts


def _should_cut(upper, lower, tw, grass_y, cut_y):
    if not upper or not lower:
        return False
    _a, _b, uy0, uy1, uw, _w = _pts_bbox(upper, tw)
    _a, _b, ly0, ly1, lw, _w2 = _pts_bbox(lower, tw)
    uh = uy1 - uy0 + 1
    lh = ly1 - ly0 + 1
    lower_grass = ly1 >= grass_y - 2
    case_island = uw >= 35 and uy0 <= 40
    case_bush = lower_grass and lh <= 20 and lw >= 12 and uw <= 8
    return case_island or case_bush


def _split_necks(pts, tw, grass_y):
    if len(pts) < 20:
        return [pts]
    counts = _row_widths(pts, tw)
    ys = sorted(counts.keys())
    y_top = ys[0]
    y_bot = ys[-1]
    if y_bot < grass_y - 3 or y_top > 70:
        return [pts]
    necks = []
    for y in ys:
        w_ = counts[y]
        if 50 <= y <= 84 and w_ <= 6:
            above = 0
            below = 0
            for yy in ys:
                if yy < y:
                    above += counts[yy]
                elif yy > y:
                    below += counts[yy]
            if above >= 40 and below >= 20:
                necks.append((y, w_))
    if not necks:
        return [pts]
    cut_y = necks[0][0]
    for y, w_ in necks:
        if y >= cut_y:
            cut_y = y
    # narrowest among lowest 8
    best = None
    for y, w_ in necks:
        if y >= cut_y - 8 and (best is None or w_ < best[1] or (w_ == best[1] and y > best[0])):
            best = (y, w_)
    cut_y = best[0]
    upper = [i for i in pts if (i // tw) < cut_y]
    lower = [i for i in pts if (i // tw) >= cut_y]
    if not _should_cut(upper, lower, tw, grass_y, cut_y):
        return [pts]
    out = []
    for part in (upper, lower):
        if 20 < len(part) < len(pts):
            out.extend(_split_necks(part, tw, grass_y))
        else:
            out.append(part)
    return out


def _classify(pts, tw, grass_y):
    counts = _row_widths(pts, tw)
    ys = sorted(counts.keys())
    y_top = ys[0]
    y_bot = ys[-1]
    solid_base = None
    y = y_bot
    while y >= y_top:
        if counts.get(y, 0) >= 8:
            solid_base = y
            break
        y -= 1
    touches = y_bot >= grass_y - 2
    mid_mass = 0
    low_mass = 0
    for y, c in counts.items():
        if y <= 70:
            mid_mass += c
        if y >= 80:
            low_mass += c
    width = _pts_bbox(pts, tw)[4]
    if width >= 35 and mid_mass > 80 and (solid_base is None or solid_base <= 72):
        return 0.6
    if touches and solid_base is not None and solid_base >= 84 and y_top >= 60:
        return 1.0
    if touches and y_top <= 50:
        return 1.0
    if not touches:
        return 0.6
    return 1.0 if touches else 0.6


def _inpaint(tile, tw, th, mask_pts, grass_y):
    flagged = {}
    for i in mask_pts:
        flagged[i] = 1
    for y in range(grass_y):
        last = None
        for x in range(tw):
            i = y * tw + x
            if i not in flagged:
                last = _565_at(tile, tw, x, y)
            elif last is not None:
                _set_565(tile, tw, x, y, last)
        last = None
        x = tw - 1
        while x >= 0:
            i = y * tw + x
            if i not in flagged:
                last = _565_at(tile, tw, x, y)
            elif last is not None:
                _set_565(tile, tw, x, y, last)
            x -= 1


def _make_stamp(tile, tw, pts, factor):
    # may split wrapped stamps
    groups = [pts]
    xs_wrap = _pts_bbox(pts, tw)[5]
    if xs_wrap:
        mid = tw // 2
        low = [i for i in pts if (i - (i // tw) * tw) < mid]
        high = [i for i in pts if (i - (i // tw) * tw) >= mid]
        groups = [g for g in (low, high) if g]
    stamps = []
    for g in groups:
        x0, x1, y0, y1, _w, wrapped = _pts_bbox(g, tw, wrap_ok=False)
        sw = x1 - x0 + 1
        sh = y1 - y0 + 1
        sbuf = bytearray(sw * sh * 2)
        mask = bytearray((sw * sh + 7) // 8)
        for i in g:
            y = i // tw
            x = i - y * tw
            dx = x - x0
            dy = y - y0
            di = dy * sw + dx
            val = _565_at(tile, tw, x, y)
            sbuf[di * 2] = val & 0xFF
            sbuf[di * 2 + 1] = (val >> 8) & 0xFF
            mask[di >> 3] |= 1 << (di & 7)
        stamps.append((factor, x0, y0, sw, sh, sbuf, mask))
    return stamps


def _blit_stamp(window, vw, tw, stamp, scroll):
    factor, ox, oy, sw, sh, sbuf, mask = stamp
    off = int(scroll * factor) % tw
    if off < 0:
        off += tw
    sx = ox - off
    # normalize into a range that we can try sx, sx+tw, sx-tw
    for cand in (sx, sx - tw, sx + tw):
        if cand + sw <= 0 or cand >= vw:
            continue
        src_x0 = 0
        dst_x0 = cand
        if dst_x0 < 0:
            src_x0 = -dst_x0
            dst_x0 = 0
        src_x1 = sw
        if cand + sw > vw:
            src_x1 = vw - cand
        if src_x1 <= src_x0:
            continue
        for dy in range(sh):
            wy = oy + dy
            row_i = dy * sw
            dst_row = wy * vw
            for dx in range(src_x0, src_x1):
                di = row_i + dx
                if mask[di >> 3] & (1 << (di & 7)):
                    wx = dst_x0 + (dx - src_x0)
                    si = di * 2
                    diw = (dst_row + wx) * 2
                    window[diw] = sbuf[si]
                    window[diw + 1] = sbuf[si + 1]


def build_parallax_layers(tile, tw, th):
    """In-place inpaint of tile plus a list of stamps. Runs once at pack load."""
    grass_y = GRASS_Y if th >= GRASS_Y else th
    mask = _object_mask(tile, tw, th, grass_y)
    comps = _components(mask, tw, grass_y)
    parts = []
    for c in comps:
        parts.extend(_split_necks(c, tw, grass_y))
    stamps = []
    all_pts = []
    for pts in parts:
        if len(pts) < 8:
            continue
        factor = _classify(pts, tw, grass_y)
        stamps.extend(_make_stamp(tile, tw, pts, factor))
        all_pts.extend(pts)
    _inpaint(tile, tw, th, all_pts, grass_y)
    return stamps


def compose_parallax_window(tile, tw, th, scroll, stamps, window, vw):
    _copy_rows(tile, tw, 0, HORIZON_Y, scroll * PARALLAX_CLOUDS, window, vw, 0)
    _copy_rows(tile, tw, HORIZON_Y, GRASS_Y, scroll * PARALLAX_WATER, window, vw, HORIZON_Y)
    _copy_rows(tile, tw, GRASS_Y, th, scroll * PARALLAX_GROUND, window, vw, GRASS_Y)
    for st in stamps:
        _blit_stamp(window, vw, tw, st, scroll)


def _norm_off(value, tw):
    """int(value) mod tw, matching _copy_rows / _blit_stamp."""
    off = int(value) % tw
    if off < 0:
        off += tw
    return off


def _mono_us():
    fn = getattr(time, "ticks_us", None)
    if fn is not None:
        return fn()
    return int(time.perf_counter() * 1000000)


def _us_diff(later, earlier):
    fn = getattr(time, "ticks_diff", None)
    if fn is not None:
        return fn(later, earlier)
    return later - earlier


def _ms_diff(later, earlier):
    fn = getattr(time, "ticks_diff", None)
    if fn is not None:
        return fn(later, earlier)
    return later - earlier


def _avg_tenths_ms(total_us, n):
    if n <= 0 or total_us <= 0:
        return 0
    return (total_us * 10 + n * 500) // (n * 1000)


def _fmt_tenths(tenths):
    if tenths < 0:
        tenths = 0
    return "%d.%d" % (tenths // 10, tenths % 10)


# Field rings and sparkles that can be on screen together. The underlay
# budget uses the max frame size times these counts.
_UNDER_RINGS = 8
_UNDER_SPARKS = 12


def _underlay_budget(pack):
    """Bytes for backdrop pixels saved under sprites before a restamp."""
    def max_bytes(frames):
        best = 0
        if not frames:
            return 0
        for fr in frames:
            n = fr.w * fr.h * 2
            if n > best:
                best = n
        return best

    sonic = 0
    for key in ("idle", "idle_trigger", "run_slow", "run_medium", "run_fast"):
        sonic = max(sonic, max_bytes(pack.cycles.get(key)))
    rings = max_bytes(pack.cycles.get("rings"))
    spark = max_bytes(pack.cycles.get("rings_sparkle"))
    extra = 0
    bar = pack.by_name.get("hud_bar")
    hud = pack.by_name.get("hud_ring")
    if bar is not None:
        extra += bar.w * bar.h * 2
    if hud is not None:
        extra += hud.w * hud.h * 2
    return sonic + extra + _UNDER_RINGS * rings + _UNDER_SPARKS * spark + 64


def _copy_partial_row(tile, tw, src_y, off, window, x0, x1, vw=W):
    """Copy window columns [x0, x1) of one row from the scrolled tile.

    Matches the pixels _copy_rows would have written for those columns.
    """
    if x1 <= x0:
        return
    if x0 < 0:
        x0 = 0
    if x1 > vw:
        x1 = vw
    if x1 <= x0:
        return
    dst_row = src_y * vw
    src_row = src_y * tw
    x = x0
    while x < x1:
        tile_x = off + x
        while tile_x >= tw:
            tile_x -= tw
        run = x1 - x
        room = tw - tile_x
        if run > room:
            run = room
        di = (dst_row + x) * 2
        si = (src_row + tile_x) * 2
        n = run * 2
        window[di:di + n] = tile[si:si + n]
        x += run


def _clip_box(x, y, w, h, vw, vh):
    if x < 0:
        w += x
        x = 0
    if y < 0:
        h += y
        y = 0
    if x + w > vw:
        w = vw - x
    if y + h > vh:
        h = vh - y
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def _rect_intersect(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0 = ax if ax > bx else bx
    y0 = ay if ay > by else by
    x1 = ax + aw
    bx1 = bx + bw
    if bx1 < x1:
        x1 = bx1
    y1 = ay + ah
    by1 = by + bh
    if by1 < y1:
        y1 = by1
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _union_x_aligned(a, b):
    """Union two rects that share y and h and overlap or touch in x."""
    if a[1] != b[1] or a[3] != b[3]:
        return None
    ax, ay, aw, ah = a
    bx, _by, bw, _bh = b
    if ax > bx:
        ax, aw, bx, bw = bx, bw, ax, aw
    if bx > ax + aw:
        return None
    right = bx + bw
    if ax + aw > right:
        right = ax + aw
    return (ax, ay, right - ax, ah)


def _merge_x_rects(rects):
    if len(rects) <= 1:
        return rects
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(rects):
            j = i + 1
            while j < len(rects):
                union = _union_x_aligned(rects[i], rects[j])
                if union is not None:
                    rects[i] = union
                    rects.pop(j)
                    changed = True
                else:
                    j += 1
            i += 1
    return rects


def _subtract_y(x, y, w, h, cut0, cut1):
    """Pieces of the rect outside the half-open y range [cut0, cut1)."""
    y1 = y + h
    if h <= 0 or cut1 <= y or cut0 >= y1:
        return [(x, y, w, h)]
    out = []
    if cut0 > y:
        top = cut0 if cut0 < y1 else y1
        if top > y:
            out.append((x, y, w, top - y))
    if cut1 < y1:
        bot = cut1 if cut1 > y else y
        if y1 > bot:
            out.append((x, bot, w, y1 - bot))
    return out


def _stamp_screen_rects(ox, oy, sw, sh, off, tw, vw, vh):
    """Visible screen boxes for a stamp scrolled by off. Same cands as _blit_stamp."""
    sx = ox - off
    found = []
    k = 0
    while k < 3:
        if k == 0:
            cand = sx
        elif k == 1:
            cand = sx - tw
        else:
            cand = sx + tw
        k += 1
        if cand + sw <= 0 or cand >= vw:
            continue
        box = _clip_box(cand, oy, sw, sh, vw, vh)
        if box is not None:
            found.append(box)
    return found


def _blit_stamp_clip(window, vw, tw, stamp, scroll, clip):
    """Stamp pixels that land inside clip. Matches _blit_stamp inside that box."""
    factor, ox, oy, sw, sh, sbuf, mask = stamp
    cx, cy, cw, ch = clip
    if cw <= 0 or ch <= 0 or sw <= 0 or sh <= 0:
        return
    cright = cx + cw
    cbot = cy + ch
    off = int(scroll * factor) % tw
    if off < 0:
        off += tw
    sx = ox - off
    k = 0
    while k < 3:
        if k == 0:
            cand = sx
        elif k == 1:
            cand = sx - tw
        else:
            cand = sx + tw
        k += 1
        if cand + sw <= 0 or cand >= vw:
            continue
        src_x0 = 0
        dst_x0 = cand
        if dst_x0 < 0:
            src_x0 = -dst_x0
            dst_x0 = 0
        src_x1 = sw
        if cand + sw > vw:
            src_x1 = vw - cand
        if src_x1 <= src_x0:
            continue
        if dst_x0 < cx:
            shift = cx - dst_x0
            src_x0 += shift
            dst_x0 = cx
        dest_end = dst_x0 + (src_x1 - src_x0)
        if dest_end > cright:
            src_x1 -= dest_end - cright
        if src_x1 <= src_x0:
            continue
        dy0 = 0
        if oy < cy:
            dy0 = cy - oy
        dy1 = sh
        if oy + dy1 > cbot:
            dy1 = cbot - oy
        if dy0 < 0:
            dy0 = 0
        if dy1 > sh:
            dy1 = sh
        if dy0 >= dy1:
            continue
        dy = dy0
        while dy < dy1:
            wy = oy + dy
            row_i = dy * sw
            dst_row = wy * vw
            dx = src_x0
            while dx < src_x1:
                di = row_i + dx
                if mask[di >> 3] & (1 << (di & 7)):
                    wx = dst_x0 + (dx - src_x0)
                    si = di * 2
                    diw = (dst_row + wx) * 2
                    window[diw] = sbuf[si]
                    window[diw + 1] = sbuf[si + 1]
                dx += 1
            dy += 1


# Sprite frames baked once at pack load. The mask bit for pixel i is
# bit (i & 7) of byte i >> 3, LSB = lowest x on the row. Transparent
# index 0 stays clear so a stamp never paints a solid box.
_BAKE_REQUIRED = (
    "idle",
    "idle_trigger",
    "run_slow",
    "run_medium",
    "run_fast",
    "rings",
)
_BAKE_NAMED = ("hud_ring", "hud_bar")
_STAMP_KIND = None
_stamp_viper = None


def _bake_frame_list(pack):
    """Sonic cycles, HUD, field rings, and sparkles. None if a required set is missing."""
    frames = []
    for key in _BAKE_REQUIRED:
        cyc = pack.cycles.get(key)
        if not cyc:
            return None
        frames.extend(cyc)
    for name in _BAKE_NAMED:
        fr = pack.by_name.get(name)
        if fr is None:
            return None
        frames.append(fr)
    spark = pack.cycles.get("rings_sparkle")
    if spark:
        frames.extend(spark)
    return frames


def prerender_sprite_into(pack, frame, rgb, mask):
    """Write one frame's LE RGB565 and 1-bit mask into caller-owned buffers."""
    w = frame.w
    h = frame.h
    pal_id = frame.pal_id
    if pal_id < 0 or pal_id >= len(pack.palettes_565):
        pal_id = 0
    pal565 = pack.palettes_565[pal_id]
    ncolors = len(pal565)
    data = pack.data
    base = pack.pix_off + frame.data_offset
    bpp4 = pack.bpp == 4
    row_bytes = ((w + 1) // 2) if bpp4 else w
    for y in range(h):
        row = base + y * row_bytes
        dst = y * w
        for x in range(w):
            if bpp4:
                b = data[row + (x >> 1)]
                idx = (b >> 4) if (x & 1) == 0 else (b & 0x0F)
            else:
                idx = data[row + x]
            if idx == 0 or idx >= ncolors:
                continue
            di = dst + x
            mask[di >> 3] = mask[di >> 3] | (1 << (di & 7))
            v = pal565[idx]
            o = di * 2
            rgb[o] = v & 0xFF
            rgb[o + 1] = (v >> 8) & 0xFF


def build_sprite_arena(pack, frames):
    """One allocation: every baked frame, then a scratch strip for Sonic tint."""
    sizes = []
    total = 0
    max_rgb = 0
    for fr in frames:
        rgb_len = fr.w * fr.h * 2
        mask_len = (fr.w * fr.h + 7) // 8
        if rgb_len > max_rgb:
            max_rgb = rgb_len
        sizes.append((fr, total, total + rgb_len, rgb_len, mask_len))
        total += rgb_len + mask_len
    scratch_off = total
    total += max_rgb
    arena = bytearray(total)
    info = {}
    for fr, rgb_off, mask_off, rgb_len, mask_len in sizes:
        rgb = memoryview(arena)[rgb_off:rgb_off + rgb_len]
        mask = memoryview(arena)[mask_off:mask_off + mask_len]
        prerender_sprite_into(pack, fr, rgb, mask)
        info[fr.name] = (rgb_off, mask_off)
    return arena, info, scratch_off, max_rgb


def stamp_sprite_rgb565(window, vw, vh, rgb, mask, sw, sh, dest_x, dest_y):
    """Copy opaque baked pixels into the playfield window. Clips to the window."""
    if sw <= 0 or sh <= 0 or vw <= 0 or vh <= 0:
        return
    if dest_y >= 0:
        y0 = 0
    else:
        y0 = 0 - dest_y
    y1 = sh
    lim = vh - dest_y
    if y1 > lim:
        y1 = lim
    if y0 >= y1:
        return
    if dest_x >= 0:
        x0 = 0
    else:
        x0 = 0 - dest_x
    x1 = sw
    limx = vw - dest_x
    if x1 > limx:
        x1 = limx
    if x0 >= x1:
        return
    for y in range(y0, y1):
        wy = dest_y + y
        sy = y * sw
        row_base = wy * vw
        for x in range(x0, x1):
            di = sy + x
            if mask[di >> 3] & (1 << (di & 7)):
                si = di * 2
                wi = (row_base + dest_x + x) * 2
                window[wi] = rgb[si]
                window[wi + 1] = rgb[si + 1]


def _viper_stamp_ok():
    """Tiny clip + high-bit check. A mismatch keeps the Python stamp."""
    if _stamp_viper is None:
        return False
    sw = 8
    sh = 2
    rgb = bytearray(sw * sh * 2)
    mask = bytearray((sw * sh + 7) // 8)
    for i in range(sw * sh):
        if (i & 1) == 0:
            continue
        mask[i >> 3] = mask[i >> 3] | (1 << (i & 7))
        rgb[i * 2] = (0xF0 + i) & 0xFF
        rgb[i * 2 + 1] = (0x80 + i * 3) & 0xFF
    vw = 6
    vh = 4
    try:
        for dest_x, dest_y in ((0, 0), (-2, -1), (4, 1), (9, 0)):
            a = bytearray(vw * vh * 2)
            b = bytearray(vw * vh * 2)
            for i in range(len(a)):
                a[i] = 0x11
                b[i] = 0x11
            _stamp_viper(a, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)
            stamp_sprite_rgb565(b, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)
            if a != b:
                return False
    except Exception:
        return False
    return True


def _ensure_stamp():
    global _STAMP_KIND
    if _STAMP_KIND is not None:
        return _STAMP_KIND
    if _stamp_viper is not None and _viper_stamp_ok():
        _STAMP_KIND = "viper"
    else:
        _STAMP_KIND = "py"
    return _STAMP_KIND


def stamp_baked(window, vw, vh, rgb, mask, sw, sh, dest_x, dest_y):
    kind = _STAMP_KIND
    if kind is None:
        kind = _ensure_stamp()
    if kind == "viper":
        _stamp_viper(window, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)
    else:
        stamp_sprite_rgb565(window, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)


def tint_sonic_scratch(pack, frame, arena, scratch_off, amount):
    """Recolor one Sonic frame into the arena scratch. Base cache stays put.

    Non-blue indices keep the packed RGB565 (the same value fillRect would
    round-trip). Blue indices use the breathing mix, then 565.
    """
    w = frame.w
    h = frame.h
    pal_id = frame.pal_id
    if pal_id < 0 or pal_id >= len(pack.palettes):
        pal_id = 0
    pal888 = pack.palettes[pal_id]
    pal565 = pack.palettes_565[pal_id]
    tinted = tint_blue_palette(pal888, amount)
    pairs = []
    ncolors = len(pal565)
    for i in range(ncolors):
        if i == 0:
            pairs.append((0, 0))
            continue
        if tinted[i] == pal888[i]:
            v = pal565[i]
        else:
            v = _rgb888_to_565(tinted[i])
        pairs.append((v & 0xFF, (v >> 8) & 0xFF))
    scratch = memoryview(arena)[scratch_off:scratch_off + (w * h * 2)]
    data = pack.data
    base = pack.pix_off + frame.data_offset
    bpp4 = pack.bpp == 4
    row_bytes = ((w + 1) // 2) if bpp4 else w
    di = 0
    for y in range(h):
        row = base + y * row_bytes
        for x in range(w):
            if bpp4:
                b = data[row + (x >> 1)]
                idx = (b >> 4) if (x & 1) == 0 else (b & 0x0F)
            else:
                idx = data[row + x]
            if idx <= 0 or idx >= ncolors:
                scratch[di] = 0
                scratch[di + 1] = 0
            else:
                lo, hi = pairs[idx]
                scratch[di] = lo
                scratch[di + 1] = hi
            di += 2
    return scratch


try:
    import micropython

    @micropython.viper
    def _stamp_viper(window, vw: int, vh: int, rgb, mask, sw: int, sh: int, dest_x: int, dest_y: int):
        wp = ptr8(window)
        rp = ptr8(rgb)
        mp = ptr8(mask)
        y0 = 0
        if dest_y < 0:
            y0 = 0 - dest_y
        y1 = sh
        lim = vh - dest_y
        if y1 > lim:
            y1 = lim
        if y0 >= y1:
            return
        x0 = 0
        if dest_x < 0:
            x0 = 0 - dest_x
        x1 = sw
        limx = vw - dest_x
        if x1 > limx:
            x1 = limx
        if x0 >= x1:
            return
        y = y0
        while y < y1:
            wy = dest_y + y
            row_base = wy * vw
            sy = y * sw
            x = x0
            while x < x1:
                di = sy + x
                if mp[di >> 3] & (1 << (di & 7)):
                    si = di * 2
                    wx = dest_x + x
                    wi = (row_base + wx) * 2
                    wp[wi] = rp[si]
                    wp[wi + 1] = rp[si + 1]
                x += 1
            y += 1
except Exception:
    _stamp_viper = None


def _parse_pixidx(data):
    """Return a PIX6 pack, or None. Frame size is not a reason to refuse art."""
    if not data or len(data) < 32 or data[:4] != PIX6_MAGIC:
        return None
    try:
        return PixIdx(data)
    except Exception as e:
        print("pixidx parse failed:", e)
        return None


def _read_local_pixidx():
    try:
        with open(PIXIDX_NAME, "rb") as f:
            return f.read()
    except Exception:
        return None


def try_load_pixidx():
    """Load a flashed pixidx.bin.

    Any PIX6 file is kept, including official Sega artwork. Hash, byte
    size, and sprite dimensions do not reject it. The hosted pack is
    not downloaded over a file that parses.
    """
    data = _read_local_pixidx()
    if not data:
        return None
    return _parse_pixidx(data)


def try_fetch_pixidx():
    """Download pixidx.bin from the public OTA host if Wi-Fi is already up.

    Does not touch secrets.py. Relies on ota_client having joined the hotspot,
    or on whatever STA is already connected. Safe to call when the file is
    not yet published — the request fails and V2 primitives keep drawing.
    A file already on the device is never replaced, so flashed artwork
    stays even when this build does not understand it yet. The hosted
    bytes still have to match the published digest and size before they
    are saved. Sprite dimensions are not checked.
    """
    if _read_local_pixidx() is not None:
        return None
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



# Mid-ground gold rings in the 180 px period.
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


class ChaoBuddy:
    """Chao from the pack. Hover at head height while cruising below 80 km/h."""

    def __init__(self):
        self.state = "idle"
        self.kind = 0
        self.x = -16.0
        self.y = float(SONIC_HEAD_Y)
        self.accum = 0
        self.frame = 0
        self.follow_ms = 0
        self.cooldown = 2500
        self._rng = 7

    def _rand(self, n):
        self._rng = (1103515245 * self._rng + 12345) & 0x7FFFFFFF
        return self._rng % n

    def tick(self, speed, dt_ms):
        cruise = speed >= 1.0 and speed < INF_SPEED
        self.accum += dt_ms
        while self.accum >= CHAO_FRAME_MS:
            self.accum -= CHAO_FRAME_MS
            self.frame ^= 1
        self.y = float(SONIC_HEAD_Y)
        if self.state == "idle":
            if self.cooldown > 0:
                self.cooldown -= dt_ms
            elif cruise and self.cooldown <= 0:
                self.kind = self._rand(5)
                self.state = "join"
                self.x = -16.0
                self.follow_ms = 4000 + self._rand(4000)
        elif not cruise and self.state in ("join", "follow"):
            self.state = "leave"
        if self.state == "join":
            self.x += dt_ms * 0.045
            if self.x >= CHAO_FOLLOW_X:
                self.x = float(CHAO_FOLLOW_X)
                self.state = "follow"
        elif self.state == "follow":
            self.follow_ms -= dt_ms
            if self.follow_ms <= 0:
                self.state = "leave"
        elif self.state == "leave":
            self.x += dt_ms * 0.08
            if self.x > W + 16:
                self.state = "idle"
                self.cooldown = 5000 + self._rand(5000)

    def frame_for(self, pack):
        if pack is None or self.state == "idle":
            return None
        name = CHAO_KINDS[self.kind % 5]
        frames = pack.cycles.get("chao_" + name)
        if not frames:
            frames = pack.cycles.get("chao")
        if not frames:
            return None
        return frames[self.frame % len(frames)]


class Sprite:
    """Composited chibi Sonic: separate pieces, swapped by speed, retimed by phase."""

    def __init__(self, pack=None):
        self.scroll = 0.0
        self.phase = 0.0
        self.ground_y = GAUGE_Y - 2
        self.body_col = COL_SONIC
        self.boost = BoostFX()
        self.chao = ChaoBuddy()
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
        self.ocean_rgb565 = None
        self.ocean_tw = 0
        self.ocean_th = 0
        self.ocean_window = None
        self.ocean_stamps = []
        self._parallax = False
        self._ocean_push = True
        self._arena = None
        self._bake_at = {}
        self._scratch_off = 0
        self._scratch_len = 0
        self._sprite_bake = False
        self._playfield_stamped = False
        # Retained playfield. Bands and scenery update only where scroll
        # actually moved; sprite underlays let animation change without a
        # full compose. _prepare_ocean(force=False) still returns before
        # any of this is touched.
        self._retain = False
        self._under_buf = None
        self._under_spans = []
        self._under_used = 0
        self._sprites_on = False
        self._sig_ok = False
        self._sig_ring_i = -1
        self._sig_anim_tag = None
        self._sig_anim_i = -1
        self._sig_scroll_i = -999999
        self._sig_amount = None
        self._sig_spark = []
        self._dirty = bytearray(0)
        self._band_off = [0, 0, 0]
        self._band_new = [0, 0, 0]
        self._stamp_off = []
        self._stamp_new = []
        self._flat_off = -1
        self._pf_valid = False
        self._tim_n = 0
        self._tim_compose = 0
        self._tim_stamp = 0
        self._tim_push = 0
        self._tim_hold = 0
        self._tim_t0 = 0
        self._sync_ground()

    def _sync_ground(self):
        pack = self.pack
        if pack is None:
            self._ground_top = GAUGE_Y - 2
            self._surface = GAUGE_Y - 2
            self.ground_y = self._surface
            self.ocean_rgb565 = None
            self.ocean_window = None
            self.ocean_stamps = []
            self._parallax = False
            self._ocean_push = True
            self._drop_sprite_cache()
            return
        # Locked after-preview feet dest, independent of the ground tile.
        self._surface = SONIC_FEET_DEST_Y
        self._ground_top = GAUGE_Y - 15
        self.ground_y = self._surface
        self._prepare_ocean(force=True)
        self._prepare_sprites(force=True)

    def _prepare_ocean(self, force=False):
        """Pre-render the 384x103 ocean tile into RGB565 once per pack load."""
        if (
            not force
            and self.ocean_rgb565 is not None
            and self.ocean_window is not None
            and self.ocean_tw > 0
            and self.ocean_th > 0
        ):
            return
        self._pf_valid = False
        self._sprites_on = False
        self._sig_ok = False
        self.ocean_rgb565 = None
        self.ocean_window = None
        self.ocean_stamps = []
        self._parallax = False
        self.ocean_tw = 0
        self.ocean_th = 0
        self._ocean_push = True
        pack = self.pack
        if pack is None:
            return
        ocean = pack.by_name.get("ocean")
        if ocean is None or ocean.w <= 0 or ocean.h <= 0:
            return
        try:
            buf, tw, th = prerender_ocean_rgb565(pack, ocean)
            window = bytearray(W * th * 2)
        except MemoryError:
            print("ocean rgb565 alloc failed")
            return
        except Exception as e:
            print("ocean rgb565 prerender failed:", e)
            return
        stamps = []
        parallax = False
        try:
            stamps = build_parallax_layers(buf, tw, th)
            parallax = True
            print("ocean parallax stamps", len(stamps))
        except MemoryError:
            print("ocean parallax alloc failed, using 1.0 scroll")
            try:
                buf, tw, th = prerender_ocean_rgb565(pack, ocean)
            except Exception:
                pass
        except Exception as e:
            print("ocean parallax failed:", e)
            try:
                buf, tw, th = prerender_ocean_rgb565(pack, ocean)
            except Exception:
                pass
        self.ocean_rgb565 = buf
        self.ocean_tw = tw
        self.ocean_th = th
        self.ocean_window = window
        self.ocean_stamps = stamps
        self._parallax = parallax
        self._bind_parallax_state()
        print("ocean rgb565", len(buf), "window", len(window))

    def _bind_parallax_state(self):
        """Offset trackers for the retained window. Not a per-frame rebuild."""
        th = self.ocean_th if self.ocean_th else 0
        self._dirty = bytearray(th)
        self._band_off = [0, 0, 0]
        self._band_new = [0, 0, 0]
        n = len(self.ocean_stamps) if self.ocean_stamps else 0
        self._stamp_off = [0] * n
        self._stamp_new = [0] * n
        self._flat_off = -1
        self._pf_valid = False
        self._sprites_on = False
        self._sig_ok = False

    def _drop_sprite_cache(self):
        self._arena = None
        self._bake_at = {}
        self._scratch_off = 0
        self._scratch_len = 0
        self._sprite_bake = False
        self._playfield_stamped = False
        self._under_buf = None
        self._under_used = 0
        del self._under_spans[:]
        self._sprites_on = False
        self._sig_ok = False
        self._retain = False
        self._pf_valid = False

    def _prepare_sprites(self, force=False):
        """Bake Sonic, HUD, and field rings once per pack load. Not per frame."""
        if not force and self._sprite_bake and self._arena is not None:
            return
        self._drop_sprite_cache()
        pack = self.pack
        if pack is None or self.ocean_window is None:
            return
        frames = _bake_frame_list(pack)
        if not frames:
            return
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        try:
            arena, info, scratch_off, scratch_len = build_sprite_arena(pack, frames)
        except MemoryError:
            print("sprite cache alloc failed")
            self._drop_sprite_cache()
            return
        except Exception as e:
            print("sprite cache failed:", e)
            self._drop_sprite_cache()
            return
        self._arena = arena
        self._bake_at = info
        self._scratch_off = scratch_off
        self._scratch_len = scratch_len
        self._sprite_bake = True
        kind = _ensure_stamp()
        print("sprite cache", len(arena), "frames", len(info), kind)
        self._alloc_underlay(pack)

    def _alloc_underlay(self, pack):
        try:
            self._under_buf = bytearray(_underlay_budget(pack))
        except MemoryError:
            self._under_buf = None
            self._retain = False
            print("sprite underlay alloc failed")
            return
        self._under_used = 0
        del self._under_spans[:]
        self._retain = True

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
        self.chao.tick(speed, dt_ms)
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

    def _draw_hud_digits(self, lcd, bx, by):
        dx, dy = bx + 22, by + 4
        n = int(self.rings) % 1000
        draw_digit(lcd, dx + 14, dy, n % 10)
        if n >= 10:
            draw_digit(lcd, dx + 7, dy, (n // 10) % 10)
        if n >= 100:
            draw_digit(lcd, dx, dy, (n // 100) % 10)

    def _draw_ring_hud(self, lcd):
        # Compact original ring + digits. Kept in the playfield, not in the gauges.
        # Offset from the CAN-fault dot in the upper-left corner.
        pack = self.pack
        if pack is not None:
            bx, by = HUD_BAR_POS
            # Ring and bar were already stamped into the pushed playfield.
            if not self._playfield_stamped:
                pal = pack.pal(1)
                bar = pack.by_name.get("hud_bar")
                ring = pack.by_name.get("hud_ring")
                if bar is not None:
                    blit_frame(lcd, pack, bar, bx, by, pal, 0, GAUGE_Y, False)
                if ring is not None:
                    blit_frame(lcd, pack, ring, HUD_RING_POS[0], HUD_RING_POS[1], pal, 0, GAUGE_Y, False)
            self._draw_hud_digits(lcd, bx, by)
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
            # Ocean and sprite caches are prepared once at pack load
            # (_sync_ground), not on every frame.
            self._draw_sheet_scene(lcd, speed, clt, now)
        else:
            self._playfield_stamped = False
            self._draw_background(lcd)
            tip_y = SONIC_MID_Y
            self._draw_sonic(lcd, speed, tps, clt)
            self._draw_artwork_overlay(lcd, speed, tip_y)
        self._draw_ring_hud(lcd)

    def _field_ring_xs(self, off):
        period = RING_PERIOD
        found = []
        for wx in FIELD_RING_X:
            x = (wx - off) % period
            if x > W + 40:
                x -= period
            if -40 < x < W + 40:
                found.append(x)
        return found

    def _blit_playfield_sprites(self, lcd, pal_s, pal_c, off):
        pack = self.pack
        ring_frames = pack.cycles.get("rings")
        if ring_frames:
            fr = ring_frames[self.ring_i % len(ring_frames)]
            ry = FIELD_RING_Y
            for x in self._field_ring_xs(off):
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

    def _blit_packed(self, lcd, fr, dest_x, dest_y, use_origin=True):
        pack = self.pack
        if pack is None or fr is None:
            return False
        blit_frame(
            lcd, pack, fr, dest_x, dest_y, pack.pal(fr.pal_id), 0, GAUGE_Y, use_origin
        )
        return True

    def _blit_cycle_index(self, lcd, key, index, dest_x, dest_y):
        if index is None or index < 0:
            return False
        frames = self._cycle_frames(key)
        if not frames:
            return False
        return self._blit_packed(lcd, frames[index % len(frames)], dest_x, dest_y, True)

    def _draw_mouth(self, lcd, speed):
        """Packed official mouth. Origin sits on the muzzle. No frame, no draw."""
        if not FACE_ENABLED or self.pack is None:
            return
        mode = mouth_mode(speed, self.scroll)
        name = MOUTH_FRAMES.get(mode)
        if name is None:
            return
        fr = self.pack.by_name.get(name)
        if fr is None and mode == "locked_open":
            fr = self.pack.by_name.get("mouth_locked_open")
        self._blit_packed(lcd, fr, SONIC_X + 22, SONIC_HEAD_Y + 8, True)

    def _draw_chao(self, lcd):
        fr = self.chao.frame_for(self.pack)
        if fr is None:
            return
        y = int(self.chao.y)
        if self.chao.frame:
            y -= 2
        self._blit_packed(lcd, fr, int(self.chao.x), y, True)

    def _draw_boost_layers(self, lcd, tip_x, tip_y):
        """Packed aura / shockwave / barrier when the pack has them."""
        if not self._blit_cycle_index(lcd, "aura", self.boost.aura_i, tip_x, tip_y):
            self.boost.draw_behind(lcd, tip_x, tip_y)
        sw_tip = SW_TIP_X
        if not self._blit_cycle_index(lcd, "sw", self.boost.sw_i, sw_tip, tip_y):
            if self.boost.sw_i >= 0:
                draw_shockwave(lcd, sw_tip, tip_y, self.boost.sw_i)
        if not self._blit_cycle_index(lcd, "bar", self.boost.bar_i, sw_tip, tip_y):
            if self.boost.bar_i >= 0:
                draw_barrier(lcd, sw_tip, tip_y, self.boost.bar_i)

    def _draw_artwork_overlay(self, lcd, speed, tip_y):
        self._draw_chao(lcd)
        self._draw_mouth(lcd, speed)
        self._draw_boost_layers(lcd, AURA_TIP_X, tip_y)

    def _can_retain(self):
        return (
            self._retain
            and self._under_buf is not None
            and self._sprite_bake
            and self.ocean_rgb565 is not None
            and self.ocean_window is not None
        )

    def _disable_retain(self):
        """Stay on the full V11 compose. The picture does not change."""
        self._retain = False
        self._under_buf = None
        self._under_used = 0
        del self._under_spans[:]
        self._sprites_on = False
        self._sig_ok = False
        self._pf_valid = False

    def _load_new_offs(self, scroll):
        tw = self.ocean_tw
        self._band_new[0] = _norm_off(scroll * PARALLAX_CLOUDS, tw)
        self._band_new[1] = _norm_off(scroll * PARALLAX_WATER, tw)
        self._band_new[2] = _norm_off(scroll * PARALLAX_GROUND, tw)
        stamps = self.ocean_stamps
        n = len(stamps)
        if len(self._stamp_new) != n or len(self._stamp_off) != n:
            self._stamp_off = [0] * n
            self._stamp_new = [0] * n
            self._pf_valid = False
        i = 0
        while i < n:
            self._stamp_new[i] = _norm_off(scroll * stamps[i][0], tw)
            i += 1

    def _offs_unchanged(self):
        oldb = self._band_off
        newb = self._band_new
        if oldb[0] != newb[0] or oldb[1] != newb[1] or oldb[2] != newb[2]:
            return False
        old = self._stamp_off
        new = self._stamp_new
        n = len(new)
        if len(old) != n:
            return False
        i = 0
        while i < n:
            if old[i] != new[i]:
                return False
            i += 1
        return True

    def _commit_offs(self):
        oldb = self._band_off
        newb = self._band_new
        oldb[0] = newb[0]
        oldb[1] = newb[1]
        oldb[2] = newb[2]
        old = self._stamp_off
        new = self._stamp_new
        n = len(new)
        i = 0
        while i < n:
            old[i] = new[i]
            i += 1
        self._flat_off = newb[2]

    def _compose_full(self, scroll):
        compose_parallax_window(
            self.ocean_rgb565,
            self.ocean_tw,
            self.ocean_th,
            scroll,
            self.ocean_stamps,
            self.ocean_window,
            W,
        )
        self._commit_offs()
        self._pf_valid = True
        self._sprites_on = False
        self._under_used = 0
        del self._under_spans[:]

    def _collect_dirty_clips(self):
        """Rectangles whose backdrop pixels are not already correct."""
        clips = []
        band_clips = []
        th = self.ocean_th
        vw = W
        spans = (
            (0, HORIZON_Y, 0),
            (HORIZON_Y, GRASS_Y, 1),
            (GRASS_Y, th, 2),
        )
        oldb = self._band_off
        newb = self._band_new
        for y0, y1, idx in spans:
            if y1 <= y0 or oldb[idx] == newb[idx]:
                continue
            if y0 < 0:
                y0 = 0
            if y0 >= th:
                continue
            if y1 > th:
                y1 = th
            if y1 <= y0:
                continue
            box = (0, y0, vw, y1 - y0)
            clips.append(box)
            band_clips.append(box)
        stamps = self.ocean_stamps
        old = self._stamp_off
        new = self._stamp_new
        tw = self.ocean_tw
        i = 0
        n = len(stamps)
        while i < n:
            if old[i] != new[i]:
                st = stamps[i]
                rects = []
                for off in (old[i], new[i]):
                    rects.extend(
                        _stamp_screen_rects(st[1], st[2], st[3], st[4], off, tw, vw, th)
                    )
                rects = _merge_x_rects(rects)
                for box in rects:
                    pieces = [box]
                    for bx, by, bw, bh in band_clips:
                        if bw < vw or bx > 0:
                            continue
                        nxt = []
                        for px, py, pw, ph in pieces:
                            nxt.extend(_subtract_y(px, py, pw, ph, by, by + bh))
                        pieces = nxt
                    for piece in pieces:
                        if piece[2] > 0 and piece[3] > 0:
                            clips.append(piece)
            i += 1
        return clips

    def _clips_cover_all(self, clips):
        dirty = self._dirty
        th = self.ocean_th
        if th <= 0 or len(dirty) < th:
            return False
        i = 0
        while i < th:
            dirty[i] = 0
            i += 1
        for x, y, w, h in clips:
            if x > 0 or x + w < W:
                continue
            y0 = y
            y1 = y + h
            if y0 < 0:
                y0 = 0
            if y1 > th:
                y1 = th
            while y0 < y1:
                dirty[y0] = 1
                y0 += 1
        i = 0
        while i < th:
            if not dirty[i]:
                return False
            i += 1
        return True

    def _restore_rect_from_tile(self, x, y, w, h):
        box = _clip_box(x, y, w, h, W, self.ocean_th)
        if box is None:
            return
        x, y, w, h = box
        tile = self.ocean_rgb565
        window = self.ocean_window
        tw = self.ocean_tw
        th = self.ocean_th
        bands = self._band_new
        yb = y
        yend = y + h
        while yb < yend:
            if yb < HORIZON_Y:
                band_end = HORIZON_Y
                off = bands[0]
            elif yb < GRASS_Y:
                band_end = GRASS_Y
                off = bands[1]
            else:
                band_end = th
                off = bands[2]
            if band_end > yend:
                band_end = yend
            if band_end > th:
                band_end = th
            if band_end <= yb:
                break
            while yb < band_end:
                _copy_partial_row(tile, tw, yb, off, window, x, x + w, W)
                yb += 1

    def _patch_dirty(self, clips, scroll):
        for x, y, w, h in clips:
            self._restore_rect_from_tile(x, y, w, h)
        vw = W
        tw = self.ocean_tw
        th = self.ocean_th
        window = self.ocean_window
        stamps = self.ocean_stamps
        new = self._stamp_new
        i = 0
        n = len(stamps)
        while i < n:
            st = stamps[i]
            srects = _stamp_screen_rects(
                st[1], st[2], st[3], st[4], new[i], tw, vw, th
            )
            for sr in srects:
                for clip in clips:
                    inter = _rect_intersect(sr, clip)
                    if inter is not None:
                        _blit_stamp_clip(window, vw, tw, st, scroll, inter)
            i += 1

    def _update_flat(self, scroll):
        off = _norm_off(scroll, self.ocean_tw)
        if self._pf_valid and off == self._flat_off:
            return False
        if self._pf_valid and self._sprites_on:
            self._restore_underlays()
        copy_ocean_window(
            self.ocean_rgb565,
            self.ocean_tw,
            self.ocean_th,
            off,
            self.ocean_window,
            W,
        )
        self._flat_off = off
        self._pf_valid = True
        self._sprites_on = False
        self._under_used = 0
        del self._under_spans[:]
        return True

    def _update_backdrop(self, scroll):
        """Bring ocean_window back to a clean V11 backdrop for this scroll.

        Returns True when any band or scenery stamp was rewritten.
        """
        if not self._parallax:
            return self._update_flat(scroll)
        self._load_new_offs(scroll)
        if self._pf_valid and self._offs_unchanged():
            return False
        if self._pf_valid and self._sprites_on:
            self._restore_underlays()
        if not self._pf_valid:
            self._compose_full(scroll)
            return True
        clips = self._collect_dirty_clips()
        if not clips:
            self._commit_offs()
            self._sprites_on = False
            self._under_used = 0
            del self._under_spans[:]
            return True
        if self._clips_cover_all(clips):
            self._compose_full(scroll)
            return True
        self._patch_dirty(clips, scroll)
        self._commit_offs()
        self._pf_valid = True
        self._sprites_on = False
        self._under_used = 0
        del self._under_spans[:]
        return True

    def _sprite_unchanged(self, amount):
        if not self._sprites_on or not self._sig_ok:
            return False
        if self._sig_ring_i != self.ring_i:
            return False
        if self._sig_anim_i != self.anim_i or self._sig_anim_tag != self.anim_tag:
            return False
        if self._sig_scroll_i != int(self.scroll):
            return False
        if self._sig_amount != amount:
            return False
        sp = self.sparkles
        prev = self._sig_spark
        n = len(sp)
        if n != len(prev):
            return False
        i = 0
        while i < n:
            item = sp[i]
            p = prev[i]
            if item[0] != p[0] or item[1] != p[1] or item[2] != p[2]:
                return False
            i += 1
        return True

    def _remember_sprites(self, amount):
        self._sig_ring_i = self.ring_i
        self._sig_anim_tag = self.anim_tag
        self._sig_anim_i = self.anim_i
        self._sig_scroll_i = int(self.scroll)
        self._sig_amount = amount
        prev = self._sig_spark
        del prev[:]
        for item in self.sparkles:
            prev.append((item[0], item[1], item[2]))
        self._sig_ok = True

    def _playfield_vh(self):
        vh = self.ocean_th
        if vh > GAUGE_Y:
            vh = GAUGE_Y
        return vh

    def _save_underlay(self, x, y, w, h, vw, vh):
        box = _clip_box(x, y, w, h, vw, vh)
        if box is None:
            return
        x, y, w, h = box
        nbytes = w * 2
        total = nbytes * h
        off = self._under_used
        buf = self._under_buf
        if buf is None or off + total > len(buf):
            raise MemoryError("underlay")
        win = self.ocean_window
        di = off
        row = 0
        while row < h:
            src = ((y + row) * vw + x) * 2
            buf[di:di + nbytes] = win[src:src + nbytes]
            di += nbytes
            row += 1
        self._under_spans.append((x, y, w, h, off))
        self._under_used = off + total

    def _restore_underlays(self):
        spans = self._under_spans
        buf = self._under_buf
        win = self.ocean_window
        vw = W
        i = len(spans) - 1
        while i >= 0:
            x, y, w, h, off = spans[i]
            if buf is not None and w > 0 and h > 0:
                di = off
                nbytes = w * 2
                row = 0
                while row < h:
                    dst = ((y + row) * vw + x) * 2
                    win[dst:dst + nbytes] = buf[di:di + nbytes]
                    di += nbytes
                    row += 1
            i -= 1
        self._under_used = 0
        del spans[:]
        self._sprites_on = False

    def _update_sprites(self, amount):
        if self._sprite_unchanged(amount):
            return False
        if self._sprites_on:
            self._restore_underlays()
        else:
            self._under_used = 0
            del self._under_spans[:]
        self._stamp_playfield_sprites(amount, True)
        self._remember_sprites(amount)
        self._sprites_on = True
        return True

    def _stamp_frame(self, window, vw, vh, fr, dest_x, dest_y, use_origin, amount, save_under=False):
        at = self._bake_at.get(fr.name)
        if at is None:
            raise KeyError(fr.name)
        rgb_off, mask_off = at
        n = fr.w * fr.h
        rgb = memoryview(self._arena)[rgb_off:rgb_off + (n * 2)]
        mask = memoryview(self._arena)[mask_off:mask_off + ((n + 7) // 8)]
        if amount > 0:
            rgb = tint_sonic_scratch(
                self.pack, fr, self._arena, self._scratch_off, amount
            )
        if use_origin:
            x0 = dest_x - fr.origin_x
            y0 = dest_y - fr.origin_y
        else:
            x0 = dest_x
            y0 = dest_y
        x0 = int(x0)
        y0 = int(y0)
        if save_under:
            self._save_underlay(x0, y0, fr.w, fr.h, vw, vh)
        stamp_baked(window, vw, vh, rgb, mask, fr.w, fr.h, x0, y0)

    def _stamp_playfield_sprites(self, amount, save_under=False):
        pack = self.pack
        window = self.ocean_window
        vw = W
        vh = self._playfield_vh()
        off = int(self.scroll)
        ring_frames = pack.cycles.get("rings")
        if ring_frames:
            fr = ring_frames[self.ring_i % len(ring_frames)]
            ry = FIELD_RING_Y
            for x in self._field_ring_xs(off):
                self._stamp_frame(window, vw, vh, fr, x, ry, True, 0, save_under)
        spark_frames = pack.cycles.get("rings_sparkle")
        if spark_frames:
            for item in self.sparkles:
                x, y, i, _acc = item
                if i < len(spark_frames):
                    self._stamp_frame(
                        window, vw, vh, spark_frames[i], int(x), int(y), True, 0, save_under
                    )
        frames = self._cycle_frames(self.anim_tag)
        if frames:
            fr = frames[self.anim_i % len(frames)]
            self._stamp_frame(
                window, vw, vh, fr, SONIC_FEET_DEST_X, SONIC_FEET_DEST_Y, True, amount, save_under
            )
        bar = pack.by_name.get("hud_bar")
        if bar is not None:
            self._stamp_frame(
                window, vw, vh, bar, HUD_BAR_POS[0], HUD_BAR_POS[1], False, 0, save_under
            )
        ring = pack.by_name.get("hud_ring")
        if ring is not None:
            self._stamp_frame(
                window, vw, vh, ring, HUD_RING_POS[0], HUD_RING_POS[1], False, 0, save_under
            )

    def _note_timing(self, compose_us, stamp_us, push_us, held):
        if compose_us < 0:
            compose_us = 0
        if stamp_us < 0:
            stamp_us = 0
        if push_us < 0:
            push_us = 0
        if self._tim_t0 == 0:
            nowfn = getattr(time, "ticks_ms", None)
            if nowfn is not None:
                self._tim_t0 = nowfn()
            else:
                self._tim_t0 = int(time.perf_counter() * 1000)
        self._tim_n += 1
        self._tim_compose += compose_us
        self._tim_stamp += stamp_us
        self._tim_push += push_us
        if held:
            self._tim_hold += 1

    def report_timing(self, now):
        """One serial line per 2 seconds. Does not rebuild caches."""
        n = self._tim_n
        if n <= 0 or self._tim_t0 == 0:
            return
        elapsed = _ms_diff(now, self._tim_t0)
        if elapsed < 2000:
            return
        gc_us = 0
        try:
            import gc
            g0 = _mono_us()
            gc.collect()
            gc_us = _us_diff(_mono_us(), g0)
            if gc_us < 0:
                gc_us = 0
        except Exception:
            gc_us = 0
        total_us = self._tim_compose + self._tim_stamp + self._tim_push
        fps_tenths = 0
        if elapsed > 0:
            fps_tenths = (n * 10000) // elapsed
        print(
            "timing n=%d compose %s stamp %s push %s gc %s total %s fps %s hold %d"
            % (
                n,
                _fmt_tenths(_avg_tenths_ms(self._tim_compose, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_stamp, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_push, n)),
                _fmt_tenths(_avg_tenths_ms(gc_us, 1)),
                _fmt_tenths(_avg_tenths_ms(total_us, n)),
                _fmt_tenths(fps_tenths),
                self._tim_hold,
            )
        )
        self._tim_n = 0
        self._tim_compose = 0
        self._tim_stamp = 0
        self._tim_push = 0
        self._tim_hold = 0
        self._tim_t0 = now

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
        self._playfield_stamped = False
        # Backdrop already includes sky, water, scenery and the grass/dirt band.
        # Scroll 1:1 with the mid-ground rings so the after still stays aligned.
        # Ocean and sprite caches stay as prepared at pack load.
        window_ready = False
        stamped = False
        used_retain = False
        compose_us = 0
        stamp_us = 0
        push_us = 0
        held = 0
        t_enter = _mono_us()
        ocean_ready = (
            self.ocean_rgb565 is not None
            and self._ocean_push
            and self.ocean_window is not None
        )
        if ocean_ready and self._can_retain():
            try:
                changed = self._update_backdrop(self.scroll)
                t1 = _mono_us()
                self._update_sprites(amount)
                t2 = _mono_us()
                compose_us = _us_diff(t1, t_enter)
                stamp_us = _us_diff(t2, t1)
                held = 0 if changed else 1
                window_ready = True
                stamped = True
                used_retain = True
            except MemoryError:
                self._disable_retain()
                used_retain = False
                t_enter = _mono_us()
            except Exception as e:
                # Torn window: do not push it. fillRect takes over, and the
                # bake flag stays clear.
                self._drop_sprite_cache()
                window_ready = False
                stamped = False
                used_retain = True
                stamp_us = _us_diff(_mono_us(), t_enter)
                print("sprite stamp failed:", e)
        if (
            ocean_ready
            and not used_retain
            and self._ocean_push
            and self.ocean_window is not None
            and self.ocean_rgb565 is not None
        ):
            t0 = _mono_us()
            if self._parallax:
                compose_parallax_window(
                    self.ocean_rgb565,
                    self.ocean_tw,
                    self.ocean_th,
                    self.scroll,
                    self.ocean_stamps,
                    self.ocean_window,
                    W,
                )
            else:
                copy_ocean_window(
                    self.ocean_rgb565,
                    self.ocean_tw,
                    self.ocean_th,
                    off,
                    self.ocean_window,
                    W,
                )
            compose_us = _us_diff(_mono_us(), t0)
            window_ready = True
            self._pf_valid = False
            self._sprites_on = False
        if window_ready and self._sprite_bake and not stamped:
            try:
                t0 = _mono_us()
                self._stamp_playfield_sprites(amount, False)
                stamp_us = _us_diff(_mono_us(), t0)
                stamped = True
            except Exception as e:
                # Remember the failure and use fillRect from here on.
                self._drop_sprite_cache()
                window_ready = False
                stamped = False
                print("sprite stamp failed:", e)
        if window_ready:
            t0 = _mono_us()
            ok = push_rgb565_window(lcd, self.ocean_window, W, self.ocean_th)
            push_us = _us_diff(_mono_us(), t0)
            if not ok:
                self._ocean_push = False
                self._drop_sprite_cache()
                window_ready = False
                stamped = False
            elif stamped:
                self._playfield_stamped = True
        if ocean_ready:
            self._note_timing(compose_us, stamp_us, push_us, held)
        if not self._playfield_stamped:
            if not window_ready:
                if ocean is not None:
                    blit_tiled_x(lcd, pack, ocean, 0, off, pal_b, 0, GAUGE_Y)
            self._blit_playfield_sprites(lcd, pal_s, pal_c, off)
        # Aura / shockwave / Chao / mouth on top of Sonic, after the playfield push.
        # Packed frames are used when the file contains them.
        self._draw_artwork_overlay(lcd, speed, AURA_TIP_Y)

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


class ButtonWatch:
    """Poll the face button on every call. Double-press starts the bench sweep.

    M5.update() is done here so a mid-draw poll still sees a fresh click,
    and wasClicked is always read in the same call that updates it.
    """

    def __init__(self, btn, on_double=None):
        self.btn = btn
        self.on_double = on_double
        self.last_click_ms = -1000
        self.pending_click = False

    def poll(self, now=None):
        if now is None:
            now = time.ticks_ms()
        M5.update()
        btn = self.btn
        if btn is None:
            return False
        tapped = False
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
                if self.pending_click and time.ticks_diff(now, self.last_click_ms) <= DBL_TAP_MS:
                    tapped = True
                    self.pending_click = False
                else:
                    self.pending_click = True
                    self.last_click_ms = now
        if self.pending_click and time.ticks_diff(now, self.last_click_ms) > DBL_TAP_MS:
            self.pending_click = False
        if tapped and self.on_double is not None:
            self.on_double(now)
        return tapped


class Dashboard:
    def __init__(self, lcd, pack=None, button=None):
        self.lcd, self._use_push = make_canvas(lcd)
        self.sprite = Sprite(pack)
        self.button = button
        self._last_fill = {g[0]: -1 for g in GAUGES}
        self._fault_shown = None
        self._last_ms = time.ticks_ms()

    def _poll_button(self, now):
        if self.button is not None:
            self.button.poll(now)

    def tick(self, values, fault, now):
        self._poll_button(now)
        dt = time.ticks_diff(now, self._last_ms)
        if dt < 0 or dt > 500:
            dt = REFRESH_MS
        self._last_ms = now
        speed = values.get("spd") or 0.0
        self.sprite.advance(speed, dt)

        lcd = self.lcd
        lcd.fillScreen(COL_BG)
        self._poll_button(now)
        self.sprite.draw(lcd, values, now)
        self._poll_button(now)
        self._draw_gauges(values)
        self._draw_fault(fault)
        if self._use_push:
            try:
                lcd.push(0, 0)
            except Exception:
                self._use_push = False
        self._poll_button(now)
        self.sprite.report_timing(now)

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
    sweep = Sweep()
    button = ButtonWatch(find_btn(), sweep.start)
    ui = Dashboard(lcd, pack, button=button)
    try:
        import ota_client
    except ImportError:
        ota_client = None
    _last_fetch_ms = -PIXIDX_FETCH_EVERY_MS

    values = {"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0, "brk": 0.0}
    last_rx_ms = 0
    last_draw_ms = 0

    bus = None
    try:
        bus = init_can()
    except Exception:
        bus = None

    while True:
        now = time.ticks_ms()
        # Poll every iteration, not only when a frame has finished.
        button.poll(now)

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
            # Keep retrying while no usable local pack is loaded.
            # A flashed PIX6 file, including official artwork, is left alone.
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
