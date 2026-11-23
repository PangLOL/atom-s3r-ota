"""GE8 Fit F-CAN gauges + engine-sheet Sonic for M5Stack Atom S3R + Atomic CAN Base.

V15: the V14 packed picture, composited so a scroll does not restamp
sprites that did not change. Same pre-scaled PIX6 art (Sonic 39x36,
playfield 128x103, rings 14x14). pixidx.bin is unchanged. At pack load the
384x103 ocean tile is pre-rendered into an RGB565 buffer and split into
parallax layers: sky/clouds at 0.25, water and islands at 0.6, ground and
near palms at 1.0. The packed art has no mountain band, so 0.4 is unused.
Scenery stamps (loop island, palms, bushes) blit through the same kind of
viper loop Sonic and the HUD already use. A viper self-test failure stays
on the V12 Python blit, so the pixels do not change. That self-test runs
while the caches are built, not inside the first timed frame.
The ocean window stays clean: no sprites. When a band's scroll offset
moves by less than a screen, that window is shifted and only the new
columns plus scenery that did not ride along are patched. A second
128-wide playfield is what gets pushed. It is shifted the same way, then
only sprites whose pixels are no longer correct are copied back from the
clean backdrop and stamped. The HUD bar and ring icon stay while the sky
under them is still. Sonic is restamped when his frame, his tint, or the
backdrop under him changes. Rings and sparkles update in their own rects.
Unchanged offsets still skip the backdrop. The pixels match a full
compose plus the same stamps.
Each frame still pushes one 128x103 playfield image. When that push lands,
the full-screen clear and the second canvas flush are skipped: gauges
repaint y 103-127 themselves. The every-two-seconds line keeps the V14
fields and adds hud, sonic, rings, shift, and full. total stays
compose+stamp+push so it compares with V12 and V14. If the USB CDC cannot
take the line, the print is skipped and the counters stay, so a host that
is not reading cannot stall the loop. The face button (BtnA / Btn /
BtnPWR only) is polled through compose, blits, flushes, and OTA checks.
The OTA client stays, on its existing schedule; its HTTP fetch cannot
block forever. A machine.WDT is armed after the first heavy alloc, fed
from the loop, and not used as a routine reboot. This file is version 15.
It is not the published Pages payload.
Chao, official mouths, boost rips, and hockey-stop sheets are not packed.
Aura, shockwave, sound barrier, and full-speed sparks are V2 primitives
drawn on top of Sonic. The native path saturating-adds them in RGB565.
FACE_ENABLED stays off.
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

One press of the face button starts a bench sweep: speed 0-100-80-0 with
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
# One motion step. A gap longer than this used to fast-forward the scroll
# (the old clamp was 500 ms, so a 134 ms collect still jumped). The loop
# does not wait out REFRESH_MS. 16 ms is a 60 fps ceiling so a native
# frame can run at 30-60 fps. It is not a 12-15 fps motion cap.
FRAME_MIN_MS = 16
# Present on the ring quantum so each paint advances the rings by one
# step. 16 ms would alias against this 33 ms cycle (fast, then slow).
# The loop waits out the remainder instead of drawing again early.
# C gc_alloc collects by itself when bytes allocated since the last
# collection reach gc.threshold. The stock value fired about twice a
# second inside a present. Automatic collection stays off. This ceiling
# is only the safety net if that call is missing, so a full collect
# stays rare. On the flashed Atom S3R VM, gc.collect() with no argument
# is one slice of about GC_SLICE_US and then returns. Later bytecode and
# the ESP32 event poll continue that same cycle, at least 12 ms apart.
# An allocation that finds no free block, or this threshold, finishes
# the rest of the cycle inside the call. That finish is a full stall.
GC_AUTO_BYTES = 3072 * 1024
# After the present, one slice runs only when the 33 ms frame still has
# this much leftover. The margin covers the call and a slice that runs
# a little past the 3 ms cap. A tighter frame skips. One call, no loop,
# and no deferred cycle.
GC_SLICE_US = 3000
GC_SLICE_MARGIN_US = 2000
# Longer than one slice: the call finished a cycle (threshold or no
# free block). Logged without reading the heap.
GC_SLICE_STALL_US = 20000
# A full 128x103 RGB565 push is about 26 KB. 800 ms is well past a slow
# SPI transfer and still shorter than the 30 s watchdog. The driver call
# itself has no timeout; this bound is the one the handoff waits on.
PUSH_TIMEOUT_MS = 800
# CS/gap/stage lines. They blocked on USB for most of a frame. Off keeps
# boot banners, errors, push-slow, push-fail, the 1 Hz frames line, and
# the two-second timing line.
FRAME_TRACE = False
# Atom S3R 0.85" ST7735: SCK GPIO15, MOSI GPIO21, DC GPIO42, CS GPIO14,
# RST GPIO48. The ST7735 TE pin is not wired on this board, and there is
# no BUSY pin. CS low is the driver selecting the panel. These registers
# are read only; the pin mux is left with the LCD driver.
_LCD_CS = 14
_LCD_DC = 42
_LCD_SCK = 15
_GPIO_IN = 0x6000403C
_GPIO_IN1 = 0x60004040
INF_SPEED = 80.0
SWEAT_CLT = 90.0
APP_VERSION = 16
APP_VERSION_NAME = "2026.11.21-v16"
# Long enough that a main.py / pixidx write is not reset, short enough that
# a stuck idle loop comes back without a power cycle. Not a routine reboot.
WDT_TIMEOUT_MS = 30000
# Caps a hung version.json / main.py / pixidx GET. The poll interval stays
# in ota_client (90s). Button and the WDT are serviced while the GET runs.
OTA_HTTP_TIMEOUT_MS = 3000
# How often this loop calls ota_client.poll. The client still decides the
# 90s fetch. Calling it on every frame was on the present path.
OTA_POLL_MS = 1000
BREATH_MS = 1600
# Retired as the aura gate. Scroll, the mouth, and the bench speed
# curve still follow vehicle speed on their own.
BOOST_SPEED = 100.0
BOOST_END_MS = 2000
# Honda GE8 L15A7 i-VTEC high-lift intake cam, about 5400 rpm at wide
# open throttle. The CAN frame is id 0x17C and we do not decode a
# solenoid bit, so the aura follows rpm only. Light throttle at high
# rpm still starts it. OFF sits under ON so a noisy 5400 edge does not
# flicker the sequence or skip the end shockwave.
VTEC_ON_RPM = 5400.0
VTEC_OFF_RPM = 5100.0
FX_FRAME_MS = 1000 // 15  # 15 fps, same as S3Vec run_fast
# Hockey stop. Brake bit plus a hard decel, then a 3-frame pose.
# Enter only while brk is held, decel stays at or above 10 km/h per
# second, and speed is still above 25 km/h, for HOCKEY_ARM_MS. Leave
# when the brake releases or decel falls under 6. The pack has no
# hockey frames; the pose waits for original art facing right.
HOCKEY_MIN_KMH = 25.0
HOCKEY_ON_DECEL = 10.0
HOCKEY_OFF_DECEL = 6.0
HOCKEY_ARM_MS = 180
HOCKEY_FRAMES = 3
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
PIXIDX_SHA256 = "f33a0418a01743d405be8cee372a3ad0b8c3d72efbc8e2f0f95720468cb2c1c0"
PIXIDX_SIZE = 216653
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
BAR_TIP_X = 68            # 8 px left of the shockwave tip
BAR_TIP_Y = SONIC_MID_Y   # 72
SPARK_FRONT_X = 22
SPARK_MID_Y = 88
SPARK_FRAMES = 4
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
# Stable place keys. Formatting "r%d" on every present was garbage.
_RING_PLACE_KEYS = tuple("r%d" % i for i in range(len(FIELD_RING_X)))
_SPARK_PLACE_KEYS = tuple("k%d" % i for i in range(12))
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
        self.has_runs = (flags & 2) != 0
        self.colors = colors
        self.pix_off = pix_off
        self.pix_size = _u32(data, 28)
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
        self.pair_bytes = []
        for pal565 in self.palettes_565:
            raw = bytearray(len(pal565) * 2)
            i = 0
            while i < len(pal565):
                v = pal565[i]
                raw[i * 2] = v & 0xFF
                raw[i * 2 + 1] = (v >> 8) & 0xFF
                i += 1
            self.pair_bytes.append(raw)
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
            fr.nruns = 0
            fr.runs = b""
            self.frames.append(fr)
            self.by_name[name] = fr
        if self.has_runs:
            at = self.pix_off + self.pix_size
            for fr in self.frames:
                if at + 2 > len(data):
                    break
                nruns = _u16(data, at)
                at += 2
                nbytes = nruns * 5
                if at + nbytes > len(data):
                    break
                fr.nruns = nruns
                fr.runs = memoryview(data)[at:at + nbytes]
                at += nbytes
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



def _gpio_mem():
    try:
        import machine
        return getattr(machine, "mem32", None)
    except Exception:
        return None


def _gpio_level(pin):
    """Read a GPIO without constructing machine.Pin, which would remux it."""
    try:
        mem = _gpio_mem()
        if mem is None:
            return -1
        if pin < 32:
            return (mem[_GPIO_IN] >> pin) & 1
        return (mem[_GPIO_IN1] >> (pin - 32)) & 1
    except Exception:
        return -1


def _bus_levels():
    return (_gpio_level(_LCD_CS), _gpio_level(_LCD_DC), _gpio_level(_LCD_SCK))


# ESP32-S3 GPIO pin-config and edge-status registers. These are not the
# IO mux. Writing them does not take SCK/MOSI/DC/CS/RST away from the LCD.
# INT_TYPE is bits 9:7. INT_ENA is bits 17:13. Any-edge is type 3.
# STATUS latches an edge while the detector is enabled, including across a
# driver call that never returns to Python. A 1 ms machine.Timer callback
# does not: it runs only when the VM is free, so it samples idle levels.
_GPIO_PIN0 = 0x60004074
_GPIO_STATUS = 0x60004044
_GPIO_STATUS_W1TC = 0x6000404C
_GPIO_STATUS1 = 0x60004050
_GPIO_STATUS1_W1TC = 0x60004058
_INT_TYPE_MASK = 0x380
_INT_TYPE_LSB = 7
_INT_ENA_MASK = 0x3E000
_INT_ENA_LSB = 13
_INT_ANYEDGE = 3

# edges: CS, DC, SCK. -1 means the latch could not be armed.
# saved: (pin, original PIN register word) for the pins armed right now.
_BUS = {"edges": (-1, -1, -1), "saved": None}


def _status_clear(mem, pin):
    if pin < 32:
        mem[_GPIO_STATUS_W1TC] = 1 << pin
    else:
        mem[_GPIO_STATUS1_W1TC] = 1 << (pin - 32)


def _status_bit(mem, pin):
    if pin < 32:
        return (mem[_GPIO_STATUS] >> pin) & 1
    return (mem[_GPIO_STATUS1] >> (pin - 32)) & 1


def _edge_arm_one(mem, pin, enable_cpu):
    """Any-edge detector. Returns the previous PIN register word.

    enable_cpu turns on the CPU0 enable bit when it was clear, which is
    what makes the status latch record the edge. SCK does not get that
    bit: any-edge on the clock would flood an already-installed GPIO ISR.
    The IO mux function is left untouched.
    """
    reg = _GPIO_PIN0 + (pin * 4)
    cur = mem[reg] & 0xFFFFFFFF
    nxt = (cur & ~_INT_TYPE_MASK) | ((_INT_ANYEDGE << _INT_TYPE_LSB) & _INT_TYPE_MASK)
    if enable_cpu and (cur & _INT_ENA_MASK) == 0:
        nxt = (nxt & ~_INT_ENA_MASK) | (1 << _INT_ENA_LSB)
    if nxt != cur:
        mem[reg] = nxt
    _status_clear(mem, pin)
    return cur


def _edge_restore(mem, saved):
    if mem is None or not saved:
        return
    for pin, word in saved:
        try:
            mem[_GPIO_PIN0 + (pin * 4)] = word
            _status_clear(mem, pin)
        except Exception:
            pass


def _edge_prepare(mem):
    """Arm CS and DC with the latch enabled, and SCK without the CPU bit."""
    saved = []
    try:
        for pin, ena in ((_LCD_CS, True), (_LCD_DC, True), (_LCD_SCK, False)):
            saved.append((pin, _edge_arm_one(mem, pin, ena)))
        _BUS["saved"] = saved
        return True
    except Exception:
        _edge_restore(mem, saved)
        _BUS["saved"] = None
        return False


def _edge_finish(mem):
    saved = _BUS["saved"]
    if mem is None or not saved:
        _BUS["edges"] = (-1, -1, -1)
        _BUS["saved"] = None
        return
    try:
        _BUS["edges"] = (
            _status_bit(mem, _LCD_CS),
            _status_bit(mem, _LCD_DC),
            _status_bit(mem, _LCD_SCK),
        )
    except Exception:
        _BUS["edges"] = (-1, -1, -1)
    _edge_restore(mem, saved)
    _BUS["saved"] = None


def bus_sample_saw_low(samples):
    """1 if any VM-visible CS sample was low. The old timer only had these."""
    for level in samples:
        if level == 0:
            return 1
    return 0


def bus_edge_seen(levels):
    """1 if the waveform changed while armed. This is what the latch stores."""
    prev = None
    for level in levels:
        if prev is not None and level != prev:
            return 1
        prev = level
    return 0


def _edge_text(value):
    if value < 0:
        return "na"
    return "%d" % value


def _bus_print(before, lo):
    after = _bus_levels()
    edges = _BUS["edges"]
    print(
        "bus cs %d/%d dc %d/%d sck %d/%d lo %d edge cs %s dc %s sck %s"
        % (
            before[0], after[0], before[1], after[1], before[2], after[2], lo,
            _edge_text(edges[0]), _edge_text(edges[1]), _edge_text(edges[2]),
        )
    )


def _exc_name(err):
    name = type(err).__name__
    text = str(err)
    if text:
        if len(text) > 32:
            text = text[:32]
        return name + ":" + text
    return name


def _call_panel(fn):
    """Run one driver call on this thread. Record how long it took.

    The call is not preempted: a C transfer that never returns cannot be
    cut short from Python. PUSH_TIMEOUT_MS is the point at which a finished
    call is reported as slow, and the budget the next method is still
    allowed to use. The edge latch is armed only around fn itself.
    """
    global _PANEL_ERR, _PANEL_MS, _PANEL_US, _PANEL_BEFORE, _PANEL_LO
    before = _PANEL_IDLE
    lo = 0
    mem = None
    if FRAME_TRACE:
        before = _bus_levels()
        mem = _gpio_mem()
        if mem is not None:
            _edge_prepare(mem)
        else:
            _BUS["edges"] = (-1, -1, -1)
    try:
        t_ms = time.ticks_ms()
    except Exception:
        t_ms = None
    t_us = _mono_us()
    err = None
    try:
        fn()
    except Exception as e:
        err = e
    raw_us = _us_diff(_mono_us(), t_us)
    if raw_us < 0:
        raw_us = 0
    ms = 0
    if t_ms is not None:
        try:
            ms = time.ticks_diff(time.ticks_ms(), t_ms)
        except Exception:
            ms = 0
        if ms < 0:
            ms = 0
    if FRAME_TRACE:
        if mem is not None:
            _edge_finish(mem)
        after_cs = _gpio_level(_LCD_CS)
        lo = 1 if (before[0] == 0 or after_cs == 0) else 0
    _PANEL_ERR = err
    _PANEL_MS = ms
    _PANEL_US = raw_us
    _PANEL_BEFORE = before
    _PANEL_LO = lo


# Reused across pushes. A new tuple here would be garbage every present.
_PANEL_IDLE = (0, 0, 0)
_PANEL_ERR = None
_PANEL_MS = 0
_PANEL_US = 0
_PANEL_BEFORE = _PANEL_IDLE
_PANEL_LO = 0
# Driver that succeeded last time. The steady path calls it directly
# instead of building a methods list and a closure every present.
_PUSH_HOW = None
_PUSH_LCD = None
_PUSH_WIN = None
_PUSH_VW = 0
_PUSH_TH = 0
_PUSH_LABEL = None
_PANEL_FAIL = ""


_STRIP = None


def _strip_buf(n):
    global _STRIP
    if _STRIP is None or len(_STRIP) < n:
        _STRIP = bytearray(n)
    return _STRIP


def _push_strips(lcd, window, vw, th, how):
    """16-row copies out of a buffer the blit is no longer writing."""
    step = 16
    raw = _strip_buf(step * vw * 2)
    y = 0
    while y < th:
        h = th - y
        if h > step:
            h = step
        n = h * vw * 2
        off = y * vw * 2
        raw[:n] = window[off:off + n]
        if how == "image":
            lcd.pushImage(0, y, vw, h, memoryview(raw)[:n])
        else:
            # drawRawBuf's len is a pixel count, not a byte count.
            lcd.drawRawBuf(raw, 0, y, vw, h, vw * h, True)
        y += h


# Native blit and drawRawBuf both run on this thread and do not release the
# GIL. yield on the gap line is this fact, not a pin measurement.
_NATIVE_YIELDS = 0
_FLOW = {
    "present": 0,
    "gauge": 0,
    "loop": 0,
    "gc": 0,
    "adv": 0,
    "paint": 0,
    "mem": -1,
    "gap_printed": 0,
}
_PUSH_PARTS = {"pre": 0, "raw": 0, "post": 0, "how": ""}


def _flow_add(key, us):
    if us < 0:
        us = 0
    _FLOW[key] = _FLOW[key] + us


def _mem_free():
    try:
        import gc
        return gc.mem_free()
    except Exception:
        return -1


def _print_gap():
    """Time since the previous present-ok, printed once before push-in.

    since  total microseconds from present-ok to this push
    gauge  fillRect gauges after that present
    loop   main-loop time outside Dashboard.tick
    gc     gc.collect on the two-second timing cadence
    adv    sprite.advance before this frame's paint
    paint  compose + stamp + fx + digits + between + copy of this frame
    other  since minus those pieces (serial blocking and VM pauses)
    gcmem  change in gc.mem_free since present-ok (negative means allocated)
    yield  0: the native path does not release the GIL
    since is -1 until the first present-ok.
    """
    if _FLOW["gap_printed"]:
        return
    _FLOW["gap_printed"] = 1
    since = -1
    if _FLOW["present"]:
        since = _us_diff(_mono_us(), _FLOW["present"])
        if since < 0:
            since = 0
    gauge = _FLOW["gauge"]
    loop = _FLOW["loop"]
    gc_us = _FLOW["gc"]
    adv = _FLOW["adv"]
    paint = _FLOW["paint"]
    other = -1
    if since >= 0:
        other = since - gauge - loop - gc_us - adv - paint
        if other < 0:
            other = 0
    mem = _mem_free()
    prev = _FLOW["mem"]
    if mem < 0 or prev < 0:
        gcmem = -1
    else:
        gcmem = mem - prev
    print(
        "gap us since %d gauge %d loop %d gc %d adv %d paint %d other %d gcmem %d yield %d"
        % (since, gauge, loop, gc_us, adv, paint, other, gcmem, _NATIVE_YIELDS)
    )
    _FLOW["gauge"] = 0
    _FLOW["loop"] = 0
    _FLOW["gc"] = 0
    _FLOW["adv"] = 0
    _FLOW["paint"] = 0


def push_rgb565_window(lcd, window, vw, th):
    _brk_enter("push")
    try:
        return _push_rgb565_window(lcd, window, vw, th)
    finally:
        _brk_exit()


def _invoke_push(lcd, window, vw, th, how):
    """One driver call. No closure: the same function serves every present."""
    if how == "image":
        lcd.pushImage(0, 0, vw, th, window)
        return
    if how == "raw":
        # swap=True: the buffer is host-endian RGB565. len is a pixel count.
        lcd.drawRawBuf(window, 0, 0, vw, th, vw * th, True)
        return
    if how == "strip":
        _push_strips(lcd, window, vw, th, "image")
        return
    if how == "rawstrip":
        _push_strips(lcd, window, vw, th, "raw")
        return
    if how == "pix":
        if hasattr(lcd, "setAddrWindow"):
            lcd.setAddrWindow(0, 0, vw - 1, th - 1)
        lcd.writePixels(window)
        return
    raise RuntimeError("push")


def _push_bound():
    """Stable callable for _call_panel. Arguments live in the _PUSH_* slots."""
    _invoke_push(_PUSH_LCD, _PUSH_WIN, _PUSH_VW, _PUSH_TH, _PUSH_LABEL)


def _push_candidates(lcd):
    """Driver order. Built only until one way sticks, not every present."""
    found = []
    if hasattr(lcd, "pushImage"):
        found.append("image")
    if hasattr(lcd, "drawRawBuf"):
        found.append("raw")
    if hasattr(lcd, "pushImage"):
        found.append("strip")
    if hasattr(lcd, "drawRawBuf"):
        found.append("rawstrip")
    if hasattr(lcd, "writePixels"):
        found.append("pix")
    return found


def _push_attempt(lcd, window, vw, th, how, t_enter):
    """Try one driver. 1 accepted, 0 failed, -1 failed and the budget is spent.

    A slow success is still 1. The failure text is left in _PANEL_FAIL.
    """
    global _PUSH_HOW, _PUSH_LCD, _PUSH_WIN, _PUSH_VW, _PUSH_TH, _PUSH_LABEL
    global _PANEL_FAIL
    _PUSH_LCD = lcd
    _PUSH_WIN = window
    _PUSH_VW = vw
    _PUSH_TH = th
    _PUSH_LABEL = how
    _call_panel(_push_bound)
    err = _PANEL_ERR
    ms = _PANEL_MS
    raw_us = _PANEL_US
    before = _PANEL_BEFORE
    lo = _PANEL_LO
    t_mid = _mono_us()
    pre = _us_diff(t_mid, t_enter) - raw_us
    if pre < 0:
        pre = 0
    parts = _PUSH_PARTS
    if err is None:
        _service()
        if FRAME_TRACE:
            _bus_print(before, lo)
            print("push-ms %d %s" % (ms, how))
        if ms >= PUSH_TIMEOUT_MS:
            print("push-slow %d" % ms)
        post = _us_diff(_mono_us(), t_mid)
        if post < 0:
            post = 0
        parts["pre"] = pre
        parts["raw"] = raw_us
        parts["post"] = post
        parts["how"] = how
        _PUSH_HOW = how
        return 1
    _PANEL_FAIL = how + ":" + _exc_name(err)
    if FRAME_TRACE:
        _bus_print(before, lo)
    post = _us_diff(_mono_us(), t_mid)
    if post < 0:
        post = 0
    parts["pre"] = parts["pre"] + pre
    parts["raw"] = parts["raw"] + raw_us
    parts["post"] = parts["post"] + post
    parts["how"] = how
    if ms >= PUSH_TIMEOUT_MS:
        print("push-slow %d" % ms)
        # The driver already used the long budget. Do not start another
        # transfer in the same frame.
        return -1
    return 0


def _push_rgb565_window(lcd, window, vw, th):
    """Hand a finished RGB565 buffer to the panel.

    The caller copies into this buffer only after compose, stamp, fx and
    digits have returned. pushImage is the older binding. Current UiFlow2
    exports drawRawBuf instead, and that len argument is the number of
    pixels. present-ok is not printed here. After one driver succeeds,
    later presents call that driver only.
    """
    global _PUSH_HOW
    if vw <= 0 or th <= 0:
        return False
    t_enter = _mono_us()
    if FRAME_TRACE:
        _print_gap()
        try:
            _note_c("push_in")
        except Exception:
            pass
    parts = _PUSH_PARTS
    parts["pre"] = 0
    parts["raw"] = 0
    parts["post"] = 0
    parts["how"] = ""
    how = _PUSH_HOW
    failed_how = None
    fails = None
    if how:
        status = _push_attempt(lcd, window, vw, th, how, t_enter)
        if status == 1:
            return True
        fails = [_PANEL_FAIL]
        _PUSH_HOW = None
        if status < 0:
            print("push-fail " + fails[0])
            return False
        # Try the other drivers once. Skip the name that just failed.
        failed_how = how
        t_enter = _mono_us()
    methods = _push_candidates(lcd)
    if not methods:
        if fails:
            print("push-fail " + " ".join(fails))
        else:
            if FRAME_TRACE:
                before = _bus_levels()
                _BUS["edges"] = (-1, -1, -1)
                _bus_print(before, 1 if before[0] == 0 else 0)
            print("push-fail none")
            parts["pre"] = _us_diff(_mono_us(), t_enter)
        return False
    if fails is None:
        fails = []
    tried = False
    for label in methods:
        if label == failed_how:
            continue
        tried = True
        status = _push_attempt(lcd, window, vw, th, label, t_enter)
        if status == 1:
            return True
        fails.append(_PANEL_FAIL)
        if status < 0:
            break
        t_enter = _mono_us()
    if fails:
        print("push-fail " + " ".join(fails))
    elif not tried:
        if FRAME_TRACE:
            before = _bus_levels()
            _BUS["edges"] = (-1, -1, -1)
            _bus_print(before, 1 if before[0] == 0 else 0)
        print("push-fail none")
        parts["pre"] = _us_diff(_mono_us(), t_enter)
    return False


def _565_at(buf, tw, x, y):
    i = (y * tw + x) * 2
    return buf[i] | (buf[i + 1] << 8)


def _set_565(buf, tw, x, y, val):
    i = (y * tw + x) * 2
    buf[i] = val & 0xFF
    buf[i + 1] = (val >> 8) & 0xFF


def stamp_disk565(buf, vw, vh, cx, cy, r, color):
    """Filled disk in a little-endian RGB565 buffer. Clipped to the buffer.

    Pixels with dx*dx + dy*dy <= r*r are covered. The playfield push then
    carries the mark, so a later panel draw is not required.
    """
    if buf is None or vw <= 0 or vh <= 0 or r < 1:
        return
    c = _rgb888_to_565(color)
    lo = c & 0xFF
    hi = (c >> 8) & 0xFF
    cx = int(cx)
    cy = int(cy)
    r = int(r)
    rr = r * r
    y0 = cy - r
    y1 = cy + r
    x0 = cx - r
    x1 = cx + r
    if y0 < 0:
        y0 = 0
    if y1 >= vh:
        y1 = vh - 1
    if x0 < 0:
        x0 = 0
    if x1 >= vw:
        x1 = vw - 1
    y = y0
    while y <= y1:
        dy = y - cy
        dy2 = dy * dy
        x = x0
        row = (y * vw) * 2
        while x <= x1:
            dx = x - cx
            if dx * dx + dy2 <= rr:
                i = row + x * 2
                buf[i] = lo
                buf[i + 1] = hi
            x += 1
        y += 1


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
        spans = _opaque_spans(mask, sw, sh)
        stamps.append((factor, x0, y0, sw, sh, sbuf, mask, spans))
    return stamps


def _opaque_spans(mask, sw, sh):
    """Horizontal opaque runs. 4 bytes each: x u16 LE, y, len. Built once."""
    out = bytearray()
    y = 0
    while y < sh:
        row = y * sw
        x = 0
        while x < sw:
            di = row + x
            if (mask[di >> 3] & (1 << (di & 7))) == 0:
                x += 1
                continue
            x1 = x + 1
            limit = x + 255
            if limit > sw:
                limit = sw
            while x1 < limit:
                di2 = row + x1
                if (mask[di2 >> 3] & (1 << (di2 & 7))) == 0:
                    break
                x1 += 1
            ln = x1 - x
            out.append(x & 0xFF)
            out.append((x >> 8) & 0xFF)
            out.append(y & 0xFF)
            out.append(ln & 0xFF)
            x = x1
        y += 1
    return out


# Face-button hook and the idle watchdog. Both stay None until main() arms
# them, so a host pixel compare never touches the panel or the WDT.
_BTN_HOOK = None
_WDT = None
_HTTP_STATE = {"busy": False, "done": False}
_SOCKET_TIMEOUT_S = None
_SOCKET_PATCHED = False
_ROW_SCRATCH = bytearray(W * 2)
_SCENERY_KIND = None
_blit_stamp_viper = None
_blit_stamp_clip_viper = None
# 0 unknown, 1 poll has reported writable, 2 this port has no poll.
_STDOUT_POLL = 0


def _stdout_can_write():
    """True when a short print should not block on a full USB CDC buffer.

    MicroPython's stream poll is ioctl(3, 4): MP_STREAM_POLL / POLL_WR.
    A missing ioctl keeps the V14 print. A zero result is trusted only
    after one poll has come back writable, so a port that always returns
    0 still prints. Counters stay put when this returns False.
    """
    global _STDOUT_POLL
    _brk_enter("cdc")
    try:
        return _stdout_can_write_body()
    finally:
        _brk_exit()


def _stdout_can_write_body():
    global _STDOUT_POLL
    if _STDOUT_POLL == 2:
        return True
    try:
        import sys
        out = sys.stdout
        ioctl = getattr(out, "ioctl", None)
        if ioctl is None:
            buf = getattr(out, "buffer", None)
            ioctl = getattr(buf, "ioctl", None) if buf is not None else None
        if ioctl is None:
            _STDOUT_POLL = 2
            return True
        got = ioctl(3, 4)
    except Exception:
        _STDOUT_POLL = 2
        return True
    ready = False
    try:
        if got is not None and (int(got) & 4) != 0:
            ready = True
    except Exception:
        _STDOUT_POLL = 2
        return True
    if ready:
        _STDOUT_POLL = 1
        return True
    if _STDOUT_POLL == 1:
        return False
    return True


def _feed_wdt():
    _brk_enter("wdt")
    try:
        wdt = _WDT
        if wdt is not None:
            try:
                wdt.feed()
            except Exception:
                pass
    finally:
        _brk_exit()


def _service():
    """Feed the watchdog, and poll the face button if it is due.

    ButtonWatch ignores calls closer than FRAME_MIN_MS, so a paint can
    still call this without stacking M5.update on the frame.
    """
    _brk_enter("svc")
    try:
        _feed_wdt()
        hook = _BTN_HOOK
        if hook is not None:
            try:
                hook()
            except Exception:
                pass
    finally:
        _brk_exit()


def _init_wdt():
    """Arm a long watchdog after boot allocs. A failed port just leaves it off."""
    global _WDT
    if _WDT is not None:
        return
    try:
        import machine
    except ImportError:
        return
    ctor = getattr(machine, "WDT", None)
    if ctor is None:
        return
    for timeout in (WDT_TIMEOUT_MS, 15000, 8000, 5000):
        try:
            _WDT = ctor(timeout=timeout)
            print("wdt", timeout)
            return
        except Exception:
            _WDT = None


def _arm_socket_timeout(seconds):
    """New sockets inherit this timeout. getaddrinfo may still block; see OTA."""
    global _SOCKET_TIMEOUT_S, _SOCKET_PATCHED
    _SOCKET_TIMEOUT_S = seconds
    if _SOCKET_PATCHED:
        return
    mods = []
    for name in ("usocket", "socket"):
        try:
            mods.append(__import__(name))
        except ImportError:
            pass
    def wrap(orig):
        def socket_fn(*args, **kwargs):
            sock = orig(*args, **kwargs)
            timeout = _SOCKET_TIMEOUT_S
            if timeout is not None:
                try:
                    sock.settimeout(timeout)
                except Exception:
                    pass
            return sock
        return socket_fn
    for mod in mods:
        if hasattr(mod, "socket"):
            try:
                mod.socket = wrap(mod.socket)
            except Exception:
                pass
        try:
            mod.setdefaulttimeout(seconds)
        except Exception:
            pass
    _SOCKET_PATCHED = True


def _call_bounded(fn, timeout_ms):
    """Run fn while the face button stays live. (True, value) or (False, err).

    The socket timeout is what unblocks a hung GET. The waiter also returns
    so the gauge loop cannot sit inside one check. A second check does not
    start while the first thread is still inside the socket.
    """
    st = _HTTP_STATE
    if st["busy"]:
        _service()
        if st["done"]:
            st["busy"] = False
            st["done"] = False
        return False, "busy"
    seconds = (timeout_ms + 200) / 1000.0
    if seconds < 0.2:
        seconds = 0.2
    _arm_socket_timeout(seconds)
    st["busy"] = True
    st["done"] = False
    box = []

    def work():
        try:
            box.append(fn())
        except Exception as e:
            box.append(e)
        st["done"] = True

    started = False
    try:
        import _thread
        _thread.start_new_thread(work, ())
        started = True
    except Exception:
        started = False
    if not started:
        try:
            value = fn()
        except Exception as e:
            st["busy"] = False
            st["done"] = False
            _service()
            return False, "http " + str(e)
        st["busy"] = False
        st["done"] = False
        _service()
        return True, value
    wait_ms = timeout_ms + 800
    t0 = time.ticks_ms()
    while not st["done"]:
        _service()
        if time.ticks_diff(time.ticks_ms(), t0) >= wait_ms:
            # Leave busy set. The thread clears done when the socket returns.
            return False, "timeout"
        try:
            time.sleep_ms(20)
        except Exception:
            time.sleep(0.02)
    st["busy"] = False
    st["done"] = False
    _service()
    if not box:
        return False, "empty"
    item = box[0]
    if isinstance(item, BaseException):
        return False, "http " + str(item)
    return True, item


def _install_ota_fetch_timeout(mod):
    """Bound ota_client HTTP without changing its 90s poll or update rules."""
    if mod is None or getattr(mod, "_atom_http_timeout", False):
        return
    orig = getattr(mod, "_http_get", None)
    if orig is not None:
        def _http_get(url):
            ok, val = _call_bounded(lambda: orig(url), OTA_HTTP_TIMEOUT_MS)
            if not ok:
                return None, val
            return val
        mod._http_get = _http_get
    orig_apply = getattr(mod, "apply_update", None)
    if orig_apply is not None:
        def apply_update(doc, version_url):
            # Feed across the file swap. The GET itself is already bounded.
            _service()
            try:
                return orig_apply(doc, version_url)
            finally:
                _service()
        mod.apply_update = apply_update
    mod._atom_http_timeout = True


def _blit_stamp_py(window, vw, tw, stamp, scroll):
    """V12 scenery blit. The viper path has to match these pixels."""
    factor, ox, oy, sw, sh, sbuf, mask = stamp[:7]
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
        row = 0
        for dy in range(sh):
            if (row & 7) == 0:
                _service()
            row += 1
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


def _blit_stamp(window, vw, tw, stamp, scroll):
    _service()
    kind = _SCENERY_KIND
    if kind is None:
        kind = _ensure_scenery()
    if kind == "viper":
        try:
            factor, ox, oy, sw, sh, sbuf, mask = stamp[:7]
            off = _norm_off(scroll * factor, tw)
            _blit_stamp_viper(window, vw, tw, sbuf, mask, sw, sh, ox, oy, off)
            return
        except Exception:
            _drop_scenery_viper()
    _blit_stamp_py(window, vw, tw, stamp, scroll)


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
    _service()
    _copy_rows(tile, tw, 0, HORIZON_Y, scroll * PARALLAX_CLOUDS, window, vw, 0)
    _copy_rows(tile, tw, HORIZON_Y, GRASS_Y, scroll * PARALLAX_WATER, window, vw, HORIZON_Y)
    _copy_rows(tile, tw, GRASS_Y, th, scroll * PARALLAX_GROUND, window, vw, GRASS_Y)
    for st in stamps:
        _blit_stamp(window, vw, tw, st, scroll)
    _service()


def _norm_off(value, tw):
    """int(value) mod tw, matching _copy_rows / _blit_stamp."""
    off = int(value) % tw
    if off < 0:
        off += tw
    return off


def _signed_delta(old, new, tw):
    """Smallest scroll step from old to new, in (-tw/2, tw/2]."""
    d = (new - old) % tw
    if d > (tw // 2):
        d -= tw
    return d


def _shift_window_band(window, vw, tile, tw, y0, y1, delta, off_new):
    """Slide band rows by delta and paint the columns that entered the screen.

    delta > 0 moves existing pixels left (scroll offset increased). The new
    columns are the backdrop at off_new, same pixels _copy_rows would write.
    """
    if delta == 0 or y1 <= y0 or vw <= 0:
        return
    scratch = _ROW_SCRATCH
    y = y0
    if delta > 0:
        keep = vw - delta
        n = keep * 2
        src = delta * 2
        x0 = vw - delta
        x1 = vw
        while y < y1:
            if ((y - y0) & 7) == 0:
                _service()
            row = y * vw * 2
            scratch[0:n] = window[row + src:row + src + n]
            window[row:row + n] = scratch[0:n]
            _copy_partial_row(tile, tw, y, off_new, window, x0, x1, vw)
            y += 1
        return
    e = 0 - delta
    keep = vw - e
    n = keep * 2
    dst = e * 2
    while y < y1:
        if ((y - y0) & 7) == 0:
            _service()
        row = y * vw * 2
        scratch[0:n] = window[row:row + n]
        window[row + dst:row + dst + n] = scratch[0:n]
        _copy_partial_row(tile, tw, y, off_new, window, 0, e, vw)
        y += 1


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


# Once-a-second breakdown of the present loop. The `timing` line stays
# compose+stamp+push. These buckets are the calls that line does not
# cover, plus the paint pieces inside that total, so `left` is the part
# of the frame period that no probe named. avg/max are milliseconds.
# A full CDC skips the print and keeps the counters.
_BRK_ORDER = (
    "sleep", "wdt", "frames", "serial", "cdc", "m5", "poll",
    "can", "sweep", "auto", "ota", "pix", "adv", "plan",
    "compose", "sonic", "hud", "rings", "fx", "dig", "svc",
    "copy", "fault", "push", "fill", "flush", "gauge", "gc", "timing",
    "draw", "tick", "pace",
)
_BRK_SUM = {}
_BRK_MAX = {}
_BRK_FR = {}
for _brk_name in _BRK_ORDER:
    _BRK_SUM[_brk_name] = 0
    _BRK_MAX[_brk_name] = 0
    _BRK_FR[_brk_name] = 0
_BRK_SUM["left"] = 0
_BRK_SUM["over"] = 0
_BRK_MAX["left"] = 0
_BRK_MAX["over"] = 0
_BRK_ST = [None] * 16
_BRK_STN = 0
_BRK_NAME = None
_BRK_AT = 0
_BRK_N = 0
_BRK_WALL = 0
_BRK_WALL_MAX = 0
_BRK_T0 = 0
# The timing line that already arrives intact on this USB CDC is 139
# characters. A longer brk print was cut at the front and stalled a frame.
_BRK_CDC = 139
# Once a second this line blocked USB for 15–19 ms and pulled a 30 fps
# window down to 29. Every two seconds, and not on the timing frame.
_BRK_PRINT_MS = 2000
_DIAG_SKIP_BRK = False
_BRK_PAGE = 0
_BRK_ALT_I = 0
# Page 0, in the order they are added. wall and left go first so a long
# value cannot push them off the end.
_BRK_PAGE0 = (
    "wall", "left", "sleep", "m5", "serial", "compose",
    "draw", "push", "copy", "over",
)
# Page 1 always carries the budget check, then the other buckets.
_BRK_PAGE1 = ("wall", "left", "over", "copy")
_BRK_REST = (
    "tick", "svc", "gauge", "sonic", "hud", "rings", "plan", "adv",
    "wdt", "frames", "cdc", "poll", "can", "sweep", "auto", "ota", "pix",
    "fx", "dig", "fault", "fill", "flush", "gc", "timing", "pace",
)


def _brk_add(name, us):
    if us < 0:
        us = 0
    if name not in _BRK_FR:
        return
    _BRK_FR[name] = _BRK_FR[name] + us


def _brk_enter(name):
    """Pause the open bucket and start `name`. Nested calls stay exclusive."""
    global _BRK_NAME, _BRK_AT, _BRK_STN
    now = _mono_us()
    cur = _BRK_NAME
    if cur is not None:
        _brk_add(cur, _us_diff(now, _BRK_AT))
        if _BRK_STN < len(_BRK_ST):
            _BRK_ST[_BRK_STN] = cur
            _BRK_STN += 1
    _BRK_NAME = name
    _BRK_AT = now


def _brk_exit():
    global _BRK_NAME, _BRK_AT, _BRK_STN
    now = _mono_us()
    cur = _BRK_NAME
    dur = 0
    if cur is not None:
        dur = _us_diff(now, _BRK_AT)
        if dur < 0:
            dur = 0
        _brk_add(cur, dur)
    if _BRK_STN > 0:
        _BRK_STN -= 1
        _BRK_NAME = _BRK_ST[_BRK_STN]
        _BRK_ST[_BRK_STN] = None
        _BRK_AT = now
    else:
        _BRK_NAME = None
    if cur is not None and dur >= _PAUSE_HITCH_US:
        _pause_on_slow(cur, dur)


def _brk_reset_sums():
    global _BRK_N, _BRK_WALL, _BRK_WALL_MAX
    for name in _BRK_SUM:
        _BRK_SUM[name] = 0
        _BRK_MAX[name] = 0
    _BRK_N = 0
    _BRK_WALL = 0
    _BRK_WALL_MAX = 0


def _brk_reset_all():
    global _BRK_NAME, _BRK_AT, _BRK_STN, _BRK_T0, _BRK_PAGE, _BRK_ALT_I
    _brk_reset_sums()
    for name in _BRK_FR:
        _BRK_FR[name] = 0
    i = 0
    while i < _BRK_STN:
        _BRK_ST[i] = None
        i += 1
    _BRK_STN = 0
    _BRK_NAME = None
    _BRK_AT = 0
    _BRK_T0 = 0
    _BRK_PAGE = 0
    _BRK_ALT_I = 0


def _brk_commit(wall):
    """Close one frame. `left` is wall minus the named buckets."""
    global _BRK_N, _BRK_WALL, _BRK_WALL_MAX
    if wall < 0:
        wall = 0
    total = 0
    for name in _BRK_ORDER:
        us = _BRK_FR[name]
        _BRK_FR[name] = 0
        if us <= 0:
            continue
        total += us
        _BRK_SUM[name] = _BRK_SUM[name] + us
        if us > _BRK_MAX[name]:
            _BRK_MAX[name] = us
    left = wall - total
    if left < 0:
        over = -left
        left = 0
    else:
        over = 0
    _BRK_SUM["left"] = _BRK_SUM["left"] + left
    _BRK_SUM["over"] = _BRK_SUM["over"] + over
    if left > _BRK_MAX["left"]:
        _BRK_MAX["left"] = left
    if over > _BRK_MAX["over"]:
        _BRK_MAX["over"] = over
    if wall > _BRK_WALL_MAX:
        _BRK_WALL_MAX = wall
    _BRK_WALL += wall
    _BRK_N += 1
    if left >= _PAUSE_HITCH_US:
        _pause_on_slow("left", left)


def _brk_field(name, total, n, peak):
    return "%s %s/%s" % (
        name,
        _fmt_tenths(_avg_tenths_ms(total, n)),
        _fmt_tenths(_avg_tenths_ms(peak, 1)),
    )


def _brk_line():
    """Full counter line. The USB print uses `_brk_cdc_line` instead.

    Each field is average/max milliseconds. `wall` is the loop period.
    `left` was not inside any named call. `over` means the named calls
    summed past the period.
    """
    n = _BRK_N
    parts = ["brk n=%d" % n, _brk_field("wall", _BRK_WALL, n, _BRK_WALL_MAX)]
    for name in _BRK_ORDER:
        parts.append(_brk_field(name, _BRK_SUM[name], n, _BRK_MAX[name]))
    parts.append(_brk_field("left", _BRK_SUM["left"], n, _BRK_MAX["left"]))
    parts.append(_brk_field("over", _BRK_SUM["over"], n, _BRK_MAX["over"]))
    return " ".join(parts)


def _brk_text(name, n):
    if name == "wall":
        return _brk_field("wall", _BRK_WALL, n, _BRK_WALL_MAX)
    return _brk_field(name, _BRK_SUM.get(name, 0), n, _BRK_MAX.get(name, 0))


def _brk_pack(names, n, limit):
    """Join fields until one more would pass the CDC length."""
    s = "brk n=%d" % n
    for name in names:
        nxt = s + " " + _brk_text(name, n)
        if len(nxt) > limit:
            break
        s = nxt
    return s


def _brk_cdc_line():
    """One short line. Pages alternate so an 80 s listen still ranks buckets.

    Page 0: wall left sleep m5 serial compose draw push copy over
    Page 1: wall left over copy, then the other non-zero buckets.
    Both stay within the timing-line length. Zero buckets on page 1 are
    left off. A full set of non-zero buckets continues on the next page 1.
    """
    global _BRK_PAGE, _BRK_ALT_I
    n = _BRK_N
    if n <= 0:
        n = 1
    if _BRK_PAGE == 0:
        _BRK_PAGE = 1
        return _brk_pack(_BRK_PAGE0, n, _BRK_CDC)
    _BRK_PAGE = 0
    s = _brk_pack(_BRK_PAGE1, n, _BRK_CDC)
    rest = []
    for name in _BRK_REST:
        if _BRK_SUM.get(name, 0) > 0 or _BRK_MAX.get(name, 0) > 0:
            rest.append(name)
    if not rest:
        _BRK_ALT_I = 0
        return s
    i = _BRK_ALT_I
    if i >= len(rest):
        i = 0
    taken = 0
    k = 0
    while k < len(rest):
        piece = _brk_text(rest[(i + k) % len(rest)], n)
        nxt = s + " " + piece
        if len(nxt) > _BRK_CDC:
            break
        s = nxt
        taken += 1
        k += 1
    if taken == 0:
        _BRK_ALT_I = (i + 1) % len(rest)
    elif taken >= len(rest):
        _BRK_ALT_I = 0
    else:
        _BRK_ALT_I = (i + taken) % len(rest)
    return s


def _brk_maybe_print(now):
    """One short line every two seconds, and only when USB CDC can take it.

    Skipped on a frame that just printed timing, so the two long lines
    do not stack into one 15 ms USB stall.
    """
    global _BRK_T0, _DIAG_SKIP_BRK
    if _BRK_T0 == 0:
        _BRK_T0 = now
        return
    if _ms_diff(now, _BRK_T0) < _BRK_PRINT_MS:
        return
    if _DIAG_SKIP_BRK:
        _DIAG_SKIP_BRK = False
        _BRK_T0 = now
        return
    if _BRK_N <= 0:
        return
    if not _stdout_can_write():
        return
    print(_brk_cdc_line())
    _brk_reset_sums()
    _BRK_T0 = now
    _pause_maybe_print()


# A bucket at least this long is a hitch, not steady draw or push work.
# Logged only. It does not itself collect.
_PAUSE_HITCH_US = 80000
_PAUSE_CAP = 8
_PAUSE_CAUSE = [None] * _PAUSE_CAP
_PAUSE_WHERE = [None] * _PAUSE_CAP
_PAUSE_US = [0] * _PAUSE_CAP
_PAUSE_A = [0] * _PAUSE_CAP
_PAUSE_B = [0] * _PAUSE_CAP
_PAUSE_N = 0
_PAUSE_DROP = 0
# Extra fields for a gcwake record: free heap and the ticks_us at the decision.
_PAUSE_C = [0] * _PAUSE_CAP
_PAUSE_D = [0] * _PAUSE_CAP


def _pause_push(cause, where, dur, a, b):
    """Queue one pause. The print happens later, and only if CDC is free."""
    global _PAUSE_N, _PAUSE_DROP
    if _PAUSE_N >= _PAUSE_CAP:
        _PAUSE_DROP += 1
        return
    i = _PAUSE_N
    _PAUSE_CAUSE[i] = cause
    _PAUSE_WHERE[i] = where
    _PAUSE_US[i] = dur
    _PAUSE_A[i] = a
    _PAUSE_B[i] = b
    _PAUSE_C[i] = 0
    _PAUSE_D[i] = 0
    _PAUSE_N = i + 1


def _pause_k(n):
    if n is None or n < 0:
        return "na"
    if n >= 1000:
        return "%dk" % (n // 1000)
    return "%d" % n


def _pause_ms(us):
    if us < 0:
        us = 0
    return _fmt_tenths(_avg_tenths_ms(us, 1))


def _pause_on_slow(where, dur):
    """A long span. Logged only. Does not walk the heap.

    The milliseconds are the whole span. They are not a clock around the
    collector. An explicit gc.collect in this same span is already queued.
    gc.mem_free() scans every block. On the sliced-VM listen that scan
    was the hitch beside the free= field (stall left, stall sonic) and
    was charged to the parent bucket. free stays unknown.
    """
    if (
        _PAUSE_N > 0
        and _PAUSE_CAUSE[_PAUSE_N - 1] == "collect"
        and _PAUSE_WHERE[_PAUSE_N - 1] == where
    ):
        return
    _pause_push("stall", where, dur, -1, -1)


def _pause_piece(i):
    cause = _PAUSE_CAUSE[i]
    where = _PAUSE_WHERE[i]
    dur = _PAUSE_US[i]
    a = _PAUSE_A[i]
    b = _PAUSE_B[i]
    if cause == "thresh":
        if b < 0:
            age = "none"
        else:
            age = "%ds" % (b // 1000)
        return "thresh %s %sms free %s age %s" % (where, _pause_ms(dur), _pause_k(a), age)
    if cause == "collect":
        return "collect %s %sms %s>%s" % (where, _pause_ms(dur), _pause_k(a), _pause_k(b))
    if cause == "gcthresh":
        return "gcthresh %db" % a
    if cause == "gcauto":
        return "gcauto %s" % ("off" if a == 0 else "on")
    if cause == "gcdefer":
        # a is the bound, in milliseconds. One line when a due collect waits.
        return "gcdefer %dms" % a
    if cause == "gcwait":
        # dur is how long the wait actually ran before the bound expired.
        return "gcwait %dms" % dur
    if cause == "gcwake":
        # where is no, yes, or run. dur is the microsecond span.
        # a is the threshold, b is gc.mem_alloc, C is free, D is t0.
        return "gcwake %s t0 %d us %d th %d alloc %d free %d" % (
            where, _PAUSE_D[i], dur, a, b, _PAUSE_C[i]
        )
    if cause == "autogc":
        return "autogc %s %sms %s>%s" % (where, _pause_ms(dur), _pause_k(a), _pause_k(b))
    if cause == "spark":
        # dur is how many sparkle slots were not a 4-list. No heap walk.
        return "spark drop %d" % dur
    return "stall %s %sms free %s was %s" % (where, _pause_ms(dur), _pause_k(a), _pause_k(b))


def _pause_shift(keep_from):
    global _PAUSE_N
    j = 0
    i = keep_from
    while i < _PAUSE_N:
        _PAUSE_CAUSE[j] = _PAUSE_CAUSE[i]
        _PAUSE_WHERE[j] = _PAUSE_WHERE[i]
        _PAUSE_US[j] = _PAUSE_US[i]
        _PAUSE_A[j] = _PAUSE_A[i]
        _PAUSE_B[j] = _PAUSE_B[i]
        _PAUSE_C[j] = _PAUSE_C[i]
        _PAUSE_D[j] = _PAUSE_D[i]
        j += 1
        i += 1
    _PAUSE_N = j


def _pause_line():
    """One CDC-sized line, or None. Unprinted events stay queued."""
    global _PAUSE_DROP
    if _PAUSE_N <= 0 and _PAUSE_DROP <= 0:
        return None
    parts = []
    used = 6
    i = 0
    while i < _PAUSE_N:
        piece = _pause_piece(i)
        add = len(piece) + (0 if not parts else 1)
        if used + add > _BRK_CDC:
            break
        parts.append(piece)
        used += add
        i += 1
    if _PAUSE_DROP > 0:
        tail = "drop %d" % _PAUSE_DROP
        add = len(tail) + (0 if not parts else 1)
        if used + add <= _BRK_CDC:
            parts.append(tail)
            _PAUSE_DROP = 0
    if not parts:
        return None
    if i > 0:
        _pause_shift(i)
    return "pause " + " ".join(parts)


def _pause_maybe_print():
    """After the brk line. A full CDC keeps the queue and changes nothing else.

    Up to four short lines, so a burst of stalls is not dropped. Each line
    stays inside the 139 character cap.
    """
    n = 0
    while n < 4:
        if _PAUSE_N <= 0 and _PAUSE_DROP <= 0:
            return
        if not _stdout_can_write():
            return
        line = _pause_line()
        if not line:
            return
        print(line)
        n += 1


def _install_pause_gc():
    """Time every gc.collect. Set the auto threshold, then log the live value."""
    try:
        import gc
    except ImportError:
        return
    orig = getattr(gc, "collect", None)
    if orig is not None and not getattr(orig, "_pause_wrapped", False):
        def _wrapped():
            # No mem_alloc / mem_free. Both walk the heap, and a slice
            # returns before the sweep has freed anything. A short call
            # is one slice and is not logged. A long call finished the
            # cycle; the line records the time and not a heap sample.
            t0 = _mono_us()
            try:
                return orig()
            finally:
                dur = _us_diff(_mono_us(), t0)
                if dur < 0:
                    dur = 0
                if dur >= GC_SLICE_STALL_US:
                    where = _BRK_NAME
                    if not where:
                        where = "boot"
                    _pause_push("collect", where, dur, -1, -1)
        _wrapped._pause_wrapped = True
        try:
            gc.collect = _wrapped
        except Exception:
            pass
    fn = getattr(gc, "threshold", None)
    if fn is None:
        return
    try:
        fn(GC_AUTO_BYTES)
    except Exception:
        # The write failed, so the previous threshold is unchanged.
        # The read below still runs, and that live value is what we log.
        pass
    val = None
    try:
        val = fn()
    except Exception:
        val = None
    if isinstance(val, tuple):
        if not val:
            val = None
        else:
            val = val[0]
    try:
        val = int(val)
    except Exception:
        val = None
    if val is None:
        # Do not claim GC_AUTO_BYTES. -1 means the live value could not be read.
        _pause_push("gcthresh", "gc", 0, -1, 0)
    else:
        _pause_push("gcthresh", "gc", 0, val, 0)
    # Stop C from calling gc_collect inside gc_alloc. An explicit collect
    # still runs, and only after a finished present.
    dis = getattr(gc, "disable", None)
    off = False
    if dis is not None:
        try:
            dis()
            off = True
        except Exception:
            off = False
    isen = getattr(gc, "isenabled", None)
    if isen is not None:
        try:
            off = not bool(isen())
        except Exception:
            pass
    _pause_push("gcauto", "gc", 0, 0 if off else 1, 0)


_install_pause_gc()


def gc_slice_room(spent_us, quantum_us=None):
    """True when one slice fits in the leftover of this frame.

    spent_us is microseconds from the frame start to just after the
    present. quantum_us defaults to the 33 ms ring quantum. A slice is
    about GC_SLICE_US. It runs only when the leftover is at least that
    plus GC_SLICE_MARGIN_US. A negative spent, a frame that already
    used the quantum, or a smaller leftover skips. This does not read
    the heap.
    """
    if quantum_us is None:
        quantum_us = _pace_us()
    if spent_us < 0 or quantum_us <= 0:
        return False
    if spent_us >= quantum_us:
        return False
    return (quantum_us - spent_us) >= (GC_SLICE_US + GC_SLICE_MARGIN_US)


def _gc_after_present(spent_us):
    """One sliced collect after the present, when this frame has room.

    The panel already has this frame. When the leftover of the 33 ms
    budget covers one slice plus a small margin, this calls collect
    once with no arguments and returns. On the flashed Atom S3R VM that
    call is one slice of about 3 ms. Later bytecode and the event poll
    continue that cycle, at least 12 ms apart. A late frame skips.
    This does not loop, and it does not wait out a deferral and then
    start a cycle. It does not read the heap: a free or alloc sample
    walks every block, and the sweep may not have finished.
    """
    if not gc_slice_room(spent_us):
        return
    _brk_enter("gc")
    try:
        try:
            import gc
            gc.collect()
        except Exception:
            pass
    finally:
        _brk_exit()


def draw_due(now, next_draw):
    """True when the next present is due. None starts immediately."""
    if next_draw is None:
        return True
    return time.ticks_diff(now, next_draw) >= 0


def next_draw_deadline(now):
    """The following present, one ring quantum after this sample."""
    try:
        return time.ticks_add(now, RING_FPS_MS)
    except Exception:
        return now + RING_FPS_MS


def _pace_us():
    """Ring quantum in microseconds. ticks_ms rounds this to the 10 ms tick."""
    return RING_FPS_MS * 1000


def _ticks_add(now, delta):
    try:
        return time.ticks_add(now, delta)
    except Exception:
        return now + delta


def pace_after(started, finished, quantum=None):
    """Deadline, in the same ticks as started and finished.

    Pass microsecond samples. The deadline is finished plus the leftover
    microseconds, not a millisecond remainder that sleep_ms rounds up to
    the next 10 ms tick. None means the paint already used the quantum.
    """
    if quantum is None:
        quantum = _pace_us()
    spent = _us_diff(finished, started)
    if spent < 0 or spent >= quantum:
        return None
    return _ticks_add(finished, quantum - spent)


def pace_remain(started, finished, quantum=None):
    """Microseconds to sleep after finished. 0 when the frame was late."""
    deadline = pace_after(started, finished, quantum)
    if deadline is None:
        return 0
    remain = _us_diff(deadline, finished)
    if remain < 0:
        return 0
    return remain


def _sleep_us(us):
    """One sleep. A 10 ms RTOS tick makes sleep_ms round a short leftover up."""
    if us <= 0:
        return
    try:
        time.sleep_us(us)
        return
    except Exception:
        pass
    try:
        time.sleep(us / 1000000.0)
    except Exception:
        pass


def live_can(bus, last_rx_ms, now):
    """True when a CAN frame has arrived inside the fault watchdog."""
    if bus is None or not last_rx_ms:
        return False
    gap = time.ticks_diff(now, last_rx_ms)
    if gap < 0:
        return False
    return gap <= WATCHDOG_MS


# Off-vehicle only. 10 s after the first present, then 20 s after each
# sweep returns to rest. Never while a sweep is running or CAN is live.
AUTO_SWEEP_BOOT_MS = 10000
AUTO_SWEEP_IDLE_MS = 20000


def auto_sweep_due(now, present_t0, active, can_live, boot_armed, idle_since):
    """Return (start, boot_armed, idle_since).

    present_t0 is the first present, not boot. active suppresses a start.
    can_live suppresses every auto start. boot_armed is the first wait.
    After any sweep, idle_since counts 20 s of rest.
    """
    if active:
        return False, False, None
    if present_t0 is None or can_live:
        return False, boot_armed, idle_since
    if boot_armed:
        if time.ticks_diff(now, present_t0) >= AUTO_SWEEP_BOOT_MS:
            return True, False, None
        return False, True, idle_since
    if idle_since is None:
        idle_since = now
    if time.ticks_diff(now, idle_since) >= AUTO_SWEEP_IDLE_MS:
        return True, False, None
    return False, False, idle_since


def anim_dt(dt):
    """Milliseconds of scroll, boost, and cycle to apply for one frame.

    A stall longer than REFRESH_MS does not catch up. The picture holds,
    then motion continues from the same speed instead of jumping ahead.
    """
    if dt < 0 or dt > REFRESH_MS:
        return REFRESH_MS
    return dt


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


def _blit_stamp_clip_py(window, vw, tw, stamp, scroll, clip):
    """Stamp pixels that land inside clip. Matches _blit_stamp_py inside that box."""
    factor, ox, oy, sw, sh, sbuf, mask = stamp[:7]
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
            if (dy & 7) == 0:
                _service()
            dy += 1


def _blit_stamp_clip(window, vw, tw, stamp, scroll, clip):
    _service()
    kind = _SCENERY_KIND
    if kind is None:
        kind = _ensure_scenery()
    if kind == "viper":
        try:
            factor, ox, oy, sw, sh, sbuf, mask = stamp[:7]
            cx, cy, cw, ch = clip
            off = _norm_off(scroll * factor, tw)
            _blit_stamp_clip_viper(
                window, vw, tw, sbuf, mask, sw, sh, ox, oy, off, cx, cy, cw, ch
            )
            return
        except Exception:
            _drop_scenery_viper()
    _blit_stamp_clip_py(window, vw, tw, stamp, scroll, clip)


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


# Native C hot path. None on a host, or when gh_render.mpy will not import.
# The Python compose stays as the fallback and as the pixel reference.
_GH = None
_GH_KIND = "py"


def _load_native():
    global _GH, _GH_KIND
    try:
        import gh_render as mod
    except Exception:
        _GH = None
        _GH_KIND = "py"
        return
    _GH = mod
    _GH_KIND = "native"


def _native_self_test():
    """Tiny compose, stamp, tint expand, and digit against the Python loops.

    A miss drops the module. This runs while the caches are built, not
    inside a timed frame.
    """
    gh = _GH
    if gh is None:
        return False
    try:
        sw = 8
        sh = 2
        rgb = bytearray(sw * sh * 2)
        mask = bytearray((sw * sh + 7) // 8)
        i = 0
        while i < sw * sh:
            if (i & 1) != 0:
                mask[i >> 3] = mask[i >> 3] | (1 << (i & 7))
                rgb[i * 2] = (0xF0 + i) & 0xFF
                rgb[i * 2 + 1] = (0x80 + i * 3) & 0xFF
            i += 1
        vw = 6
        vh = 4
        for dest_x, dest_y in ((0, 0), (-2, -1), (4, 1), (9, 0)):
            a = bytearray(vw * vh * 2)
            b = bytearray(vw * vh * 2)
            j = 0
            while j < len(a):
                a[j] = 0x11
                b[j] = 0x11
                j += 1
            gh.stamp(a, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)
            stamp_sprite_rgb565(b, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)
            if a != b:
                return False
        # Wider than the 128-wide window so a wrapped row stays inside the tile.
        tw = 160
        th = 8
        tile = bytearray(tw * th * 2)
        j = 0
        while j < len(tile):
            tile[j] = (j * 3) & 0xFF
            tile[j + 1] = (j * 5) & 0xFF
            j += 2
        sw = 10
        sh = 4
        sbuf = bytearray(sw * sh * 2)
        mask = bytearray((sw * sh + 7) // 8)
        i = 0
        while i < sw * sh:
            if (i % 3) != 0:
                mask[i >> 3] = mask[i >> 3] | (1 << (i & 7))
                sbuf[i * 2] = (0xA0 + i) & 0xFF
                sbuf[i * 2 + 1] = (0x10 + i * 5) & 0xFF
            i += 1
        stamp = (1.0, 20, 1, sw, sh, sbuf, mask)
        # compose() always writes a 128-wide window, same as the panel.
        window_w = 128
        py = bytearray(window_w * th * 2)
        nat = bytearray(window_w * th * 2)
        off_c, off_w, off_g = 100, 140, 150
        _copy_rows(tile, tw, 0, 3, off_c, py, window_w, 0)
        _copy_rows(tile, tw, 3, 5, off_w, py, window_w, 3)
        _copy_rows(tile, tw, 5, th, off_g, py, window_w, 5)
        _blit_stamp_py(py, window_w, tw, stamp, off_g)
        gh.compose(tile, tw, th, off_c, off_w, off_g, 3, 5, [stamp], [off_g], nat)
        if py != nat:
            return False
        src = bytearray(b"\x10\x20\x01\x00")
        pairs = bytearray(b"\x00\x00\x11\x22\x33\x44")
        dst = bytearray(4 * 2)
        ref = bytearray(4 * 2)
        gh.expand(src, 2, 4, 4, 1, pairs, 3, dst)
        # 4bpp row: pixels 1, 0, 2, 0 from bytes 0x10, 0x20? 
        # x0 nibble high of byte0 = 1, x1 low = 0, x2 high of byte1 = 2, x3 low = 0.
        ref[0] = 0x11
        ref[1] = 0x22
        ref[4] = 0x33
        ref[5] = 0x44
        if dst != ref:
            return False
        digit = bytearray(48 * 16 * 2)
        gh.digits(digit, 48, 16, 0, 2, 7)
        if digit == bytearray(len(digit)):
            return False
    except Exception:
        return False
    return True


def _drop_native():
    global _GH, _GH_KIND
    _GH = None
    _GH_KIND = "py"


def _note_c(name):
    gh = _GH
    if gh is None:
        return
    fn = getattr(gh, name, None)
    if fn is None:
        return
    fn()


def _note_presented():
    """present-ok then refresh ok, after the panel call has returned."""
    global _presented_n, _presented_total
    _presented_n += 1
    _presented_total += 1
    if FRAME_TRACE:
        _note_c("refresh_ok")
    # The gap clock starts after those prints, so their serial stall shows
    # up in the next frame's `other`, not inside raw.
    _FLOW["present"] = _mono_us()
    # gc.mem_free scans every GC block. On the PR #13 listen that scan was
    # the whole per-frame `draw` bucket (~32 ms, then ~65 ms after a
    # split-heap area doubled the scan) and it is what dropped the panel
    # from ~17 fps to ~10 fps. The gap line is the only reader, and it
    # runs only while FRAME_TRACE is on.
    if FRAME_TRACE:
        _FLOW["mem"] = _mem_free()
    _FLOW["gap_printed"] = 0
    _FLOW["gauge"] = 0
    _FLOW["loop"] = 0
    _FLOW["gc"] = 0
    _FLOW["adv"] = 0
    _FLOW["paint"] = 0


_presented_n = 0
_presented_total = 0
_presented_t0 = 0


def _report_presented(now):
    """One line per second: playfield frames the panel accepted in that second."""
    global _presented_n, _presented_t0
    _brk_enter("frames")
    try:
        if _presented_t0 == 0:
            _presented_t0 = now
            return
        if _ms_diff(now, _presented_t0) < 1000:
            return
        # A full USB CDC must not stall the present. Retry on a later pass.
        if not _stdout_can_write():
            return
        _brk_enter("serial")
        try:
            print("frames %d total %d" % (_presented_n, _presented_total))
        finally:
            _brk_exit()
        _presented_n = 0
        _presented_t0 = now
    finally:
        _brk_exit()


_load_native()


def _tint_pair_bytes(pack, frame, amount):
    """LE RGB565 pairs for one frame. Index 0 stays transparent (0, 0).

    Non-blue indices keep the packed RGB565. Blue indices use the breathing
    mix, then the same 565 rounding as the ocean bake.
    """
    pal_id = frame.pal_id
    if pal_id < 0 or pal_id >= len(pack.palettes):
        pal_id = 0
    pal888 = pack.palettes[pal_id]
    pal565 = pack.palettes_565[pal_id]
    tinted = tint_blue_palette(pal888, amount)
    ncolors = len(pal565)
    out = bytearray(ncolors * 2)
    i = 0
    while i < ncolors:
        if i == 0:
            i += 1
            continue
        if tinted[i] == pal888[i]:
            v = pal565[i]
        else:
            v = _rgb888_to_565(tinted[i])
        out[i * 2] = v & 0xFF
        out[i * 2 + 1] = (v >> 8) & 0xFF
        i += 1
    return out


def tint_sonic_scratch(pack, frame, arena, scratch_off, amount):
    """Recolor one Sonic frame into the arena scratch. Base cache stays put."""
    w = frame.w
    h = frame.h
    pairs = _tint_pair_bytes(pack, frame, amount)
    ncolors = len(pairs) // 2
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
                scratch[di] = pairs[idx * 2]
                scratch[di + 1] = pairs[idx * 2 + 1]
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


def _drop_scenery_viper():
    global _SCENERY_KIND
    _SCENERY_KIND = "py"


def _viper_scenery_ok():
    """Full and clipped scenery blits against the Python loops. A miss stays on Python."""
    if _blit_stamp_viper is None or _blit_stamp_clip_viper is None:
        return False
    tw = 12
    vw = 16
    sw = 10
    sh = 4
    sbuf = bytearray(sw * sh * 2)
    mask = bytearray((sw * sh + 7) // 8)
    i = 0
    while i < sw * sh:
        if (i % 3) != 0:
            mask[i >> 3] = mask[i >> 3] | (1 << (i & 7))
            sbuf[i * 2] = (0xA0 + i) & 0xFF
            sbuf[i * 2 + 1] = (0x10 + i * 5) & 0xFF
        i += 1
    stamp = (1.0, 0, 1, sw, sh, sbuf, mask)
    vh = 8
    cases = (
        (0, None),
        (2, None),
        (5, (1, 0, 6, 3)),
        (0, (0, 1, 16, 2)),
        (11, (14, 2, 4, 5)),
        (3, (-4, -2, 8, 3)),
        (0, (20, 0, 4, 4)),
    )
    try:
        for off, clip in cases:
            a = bytearray(vw * vh * 2)
            b = bytearray(vw * vh * 2)
            fill = 0x3C
            j = 0
            while j < len(a):
                a[j] = fill
                b[j] = fill
                j += 1
            # factor is 1.0, so the Python blit mods scroll by tw. Keep off < tw.
            wrapped = off % tw
            _blit_stamp_viper(a, vw, tw, sbuf, mask, sw, sh, 0, 1, wrapped)
            _blit_stamp_py(b, vw, tw, stamp, wrapped)
            if a != b:
                return False
            if clip is not None:
                j = 0
                while j < len(a):
                    a[j] = fill
                    b[j] = fill
                    j += 1
                cx, cy, cw, ch = clip
                _blit_stamp_clip_viper(
                    a, vw, tw, sbuf, mask, sw, sh, 0, 1, wrapped, cx, cy, cw, ch
                )
                _blit_stamp_clip_py(b, vw, tw, stamp, wrapped, clip)
                if a != b:
                    return False
    except Exception:
        return False
    return True


def _ensure_scenery():
    global _SCENERY_KIND
    if _SCENERY_KIND is not None:
        return _SCENERY_KIND
    if _blit_stamp_viper is not None and _viper_scenery_ok():
        _SCENERY_KIND = "viper"
    else:
        _SCENERY_KIND = "py"
    return _SCENERY_KIND


try:
    import micropython

    @micropython.viper
    def _blit_stamp_viper(window, vw: int, tw: int, sbuf, mask, sw: int, sh: int, ox: int, oy: int, off: int):
        wp = ptr8(window)
        sp = ptr8(sbuf)
        mp = ptr8(mask)
        if sw <= 0 or sh <= 0 or vw <= 0:
            return
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
            if cand + sw > 0 and cand < vw:
                src_x0 = 0
                dst_x0 = cand
                if dst_x0 < 0:
                    src_x0 = 0 - dst_x0
                    dst_x0 = 0
                src_x1 = sw
                if cand + sw > vw:
                    src_x1 = vw - cand
                if src_x1 > src_x0:
                    dy = 0
                    while dy < sh:
                        wy = oy + dy
                        row_i = dy * sw
                        dst_row = wy * vw
                        dx = src_x0
                        while dx < src_x1:
                            di = row_i + dx
                            if mp[di >> 3] & (1 << (di & 7)):
                                wx = dst_x0 + (dx - src_x0)
                                si = di * 2
                                wi = (dst_row + wx) * 2
                                wp[wi] = sp[si]
                                wp[wi + 1] = sp[si + 1]
                            dx += 1
                        dy += 1

    @micropython.viper
    def _blit_stamp_clip_viper(
        window, vw: int, tw: int, sbuf, mask, sw: int, sh: int,
        ox: int, oy: int, off: int, cx: int, cy: int, cw: int, ch: int,
    ):
        wp = ptr8(window)
        sp = ptr8(sbuf)
        mp = ptr8(mask)
        if cw <= 0 or ch <= 0 or sw <= 0 or sh <= 0 or vw <= 0:
            return
        cright = cx + cw
        cbot = cy + ch
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
            if cand + sw > 0 and cand < vw:
                src_x0 = 0
                dst_x0 = cand
                if dst_x0 < 0:
                    src_x0 = 0 - dst_x0
                    dst_x0 = 0
                src_x1 = sw
                if cand + sw > vw:
                    src_x1 = vw - cand
                if src_x1 > src_x0:
                    if dst_x0 < cx:
                        shift = cx - dst_x0
                        src_x0 += shift
                        dst_x0 = cx
                    dest_end = dst_x0 + (src_x1 - src_x0)
                    if dest_end > cright:
                        src_x1 -= dest_end - cright
                    if src_x1 > src_x0:
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
                        if dy0 < dy1:
                            dy = dy0
                            while dy < dy1:
                                wy = oy + dy
                                row_i = dy * sw
                                dst_row = wy * vw
                                dx = src_x0
                                while dx < src_x1:
                                    di = row_i + dx
                                    if mp[di >> 3] & (1 << (di & 7)):
                                        wx = dst_x0 + (dx - src_x0)
                                        si = di * 2
                                        wi = (dst_row + wx) * 2
                                        wp[wi] = sp[si]
                                        wp[wi + 1] = sp[si + 1]
                                    dx += 1
                                dy += 1
except Exception:
    _blit_stamp_viper = None
    _blit_stamp_clip_viper = None


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
    def _read_body():
        try:
            resp = requests.get(PIXIDX_URL, timeout=OTA_HTTP_TIMEOUT_MS / 1000.0)
        except TypeError:
            resp = requests.get(PIXIDX_URL)
        try:
            status = getattr(resp, "status_code", None)
            if status is None:
                status = getattr(resp, "status", 200)
            if int(status) != 200:
                return None
            return resp.content
        finally:
            try:
                resp.close()
            except Exception:
                pass

    ok, data = _call_bounded(_read_body, OTA_HTTP_TIMEOUT_MS)
    if not ok or not data:
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
        _service()
        with open(PIXIDX_NAME, "wb") as f:
            i = 0
            n = len(data)
            while i < n:
                nxt = i + 1024
                if nxt > n:
                    nxt = n
                f.write(data[i:nxt])
                i = nxt
                # Feed between chunks so the watchdog cannot fire mid-write.
                _service()
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
    """Speed 0 → 120 → 80 → 0, with matching RPM / throttle / coolant.

    The climb and the 120 km/h hold take rpm through VTEC_ON_RPM. The
    settle stage is already under VTEC_OFF_RPM, so the aura drops out
    and plays the end sequence on the way back to rest. Speed, throttle,
    and coolant staging are unchanged.
    """

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
            # 5400 rpm at 109.5 km/h, 5840 at the 120 km/h peak.
            rpm = 800.0 + 42.0 * speed
            clt = 78.0 + 0.24 * speed
        elif kind == "boost_hold":
            tps = 220.0
            rpm = 800.0 + 42.0 * speed
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
            "brk": 0.0,
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
    """Original sound barrier. Four frames: grow, peak, near streak, far streak."""
    if frame < 0 or frame > 3:
        return
    # Same shapes as the old frames 0, 2, 3, and 5.
    if frame <= 1:
        h = 6 if frame == 0 else 18
        x = tip_x + (2 if frame == 0 else 4)
        _chevron_column(lcd, x, tip_y, h, 4, COL_AURA_W, steps=3)
        _chevron_column(lcd, x - 4, tip_y, max(4, h - 3), 3, COL_AURA_I, steps=3)
    else:
        k = 1 if frame == 2 else 3
        x = tip_x + 6 + k * 8
        h = 22 - k * 4
        _chevron_column(lcd, x, tip_y, max(4, h), 3, COL_AURA_I, steps=3)
        fill_triangle(lcd, x + 2, tip_y - h, x + 8 + k, tip_y - h + 2, x + 2, tip_y - h + 6, COL_AURA_W)
        fill_triangle(lcd, x + 2, tip_y + h, x + 8 + k, tip_y + h - 2, x + 2, tip_y + h - 6, COL_AURA_M)


def draw_sparks(lcd, front_x, mid_y, frame):
    """Original full-speed sparks. Short streaks trail left from the front.

    Not a blue boost trail, and not copied from a ripped sheet. The panel
    fallback replaces pixels. The native path saturating-adds them.
    """
    if frame < 0:
        return
    shards = (
        ((-1, -1, 5, COL_AURA_W), (-4, 3, 4, COL_AURA_I), (-7, -4, 3, COL_AURA_W), (-3, 6, 3, COL_AURA_I)),
        ((-2, 1, 6, COL_AURA_I), (-5, -3, 4, COL_AURA_W), (-8, 4, 3, COL_AURA_I), (-3, -6, 3, COL_AURA_W)),
        ((-1, 2, 5, COL_AURA_W), (-6, -1, 5, COL_AURA_I), (-4, 5, 3, COL_AURA_W), (-9, -3, 3, COL_AURA_I)),
        ((-2, -2, 4, COL_AURA_I), (-5, 2, 6, COL_AURA_W), (-8, -5, 3, COL_AURA_I), (-3, 5, 4, COL_AURA_W)),
    )
    for dx, dy, length, col in shards[frame % SPARK_FRAMES]:
        fill_triangle(
            lcd,
            front_x + dx, mid_y + dy,
            front_x + dx - length, mid_y + dy - 1,
            front_x + dx - 2, mid_y + dy + 2,
            col,
        )


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
        self.spark_i = -1
        self.spark_on = False
        self.start_sw_done = False
        self.ticks = 0

    def reset(self):
        """End the boost sequence. The spark index stays with _sync_spark."""
        self.initiated = False
        self.phase = "idle"
        self.accum = 0
        self.below_ms = 0
        self.aura_i = -1
        self.sw_i = -1
        self.bar_i = -1
        self.start_sw_done = False
        self.ticks = 0

    def _sync_spark(self, speed):
        if speed >= SPEED_FAST:
            if self.spark_i < 0:
                self.spark_i = 0
            self.spark_on = True
        else:
            self.spark_i = -1
            self.spark_on = False

    def tick(self, speed, dt_ms, rpm=None):
        # `speed` stays for callers. The aura follows rpm, not km/h.
        # Sparks follow full-speed running, on the same 15 fps clock.
        if rpm is None:
            rpm = 0.0
        if speed is None:
            speed = 0.0
        self._sync_spark(speed)
        if self._vtec_on(rpm):
            self.below_ms = 0
            if not self.initiated or self.phase in ("end_sw", "barrier", "idle"):
                # (Re)initiate: aura frame 0, shockwave once on top.
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

        if self.phase == "idle" and self.spark_i < 0:
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

    def _vtec_on(self, rpm):
        """True while the run should stay up.

        Engage at VTEC_ON_RPM. Once the run is up, hold it down to
        VTEC_OFF_RPM so the edge does not flicker. The end shockwave
        and the barrier are not held here; a later crossing of
        VTEC_ON_RPM can re-initiate.
        """
        if rpm >= VTEC_ON_RPM:
            return True
        if self.phase == "run" and rpm >= VTEC_OFF_RPM:
            return True
        return False

    def _step_frame(self):
        self.ticks += 1
        if self.spark_on and self.spark_i >= 0:
            self.spark_i = (self.spark_i + 1) % SPARK_FRAMES
        if self.phase == "run":
            # All four aura frames, for the whole boost, at 15 fps.
            self.aura_i = (self.aura_i + 1) & 3
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
            if self.bar_i < 3:
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
            draw_barrier(lcd, BAR_TIP_X, BAR_TIP_Y, self.bar_i)
        if self.spark_i >= 0:
            draw_sparks(lcd, SPARK_FRONT_X, SPARK_MID_Y, self.spark_i)


class Sprite:
    """Composited chibi Sonic: separate pieces, swapped by speed, retimed by phase."""

    def __init__(self, pack=None):
        self.scroll = 0.0
        self._scroll_period = float(PERIOD)
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
        self.hockey = False
        self.hockey_i = -1
        self._hockey_arm = 0
        self._hockey_accum = 0
        self._spd_prev = None
        self.decel = 0.0
        self.ring_i = 0
        self.ring_accum = 0
        self.sparkles = []  # [x, y, i, accum]
        self._surface = GAUGE_Y - 2
        self._ground_top = GAUGE_Y - 2
        self.ocean_rgb565 = None
        self.ocean_tw = 0
        self.ocean_th = 0
        self.ocean_window = None
        # Clean backdrop lives in ocean_window. Sprites are stamped only
        # into play_window, which is the buffer that gets pushed.
        self.play_window = None
        self.present_buf = None
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
        self._band_mode = [0, 0, 0]
        self._band_delta = [0, 0, 0]
        self._stamp_off = []
        self._stamp_new = []
        self._flat_off = -1
        self._pf_valid = False
        self._play_valid = False
        self._placed = []
        self._planned = []
        self._place_pool = {}
        self._paint_us = [0, 0, 0, 0, 0, 0, 0]
        self._off_c = 0
        self._off_w = 0
        self._off_g = 0
        self._bd_mode = 0
        self._bd_clips = []
        self._part_hud = 0
        self._part_sonic = 0
        self._part_rings = 0
        self._tim_n = 0
        self._tim_compose = 0
        self._tim_stamp = 0
        self._tim_push = 0
        self._tim_fill = 0
        self._tim_flush = 0
        self._tim_hold = 0
        self._tim_hud = 0
        self._tim_sonic = 0
        self._tim_rings = 0
        self._tim_shift = 0
        self._tim_full = 0
        self._tim_t0 = 0
        self._fx_in_buffer = False
        self._corner_pushed = False
        self._dot_on_play = False
        self._native_ready = False
        self._nat_c = -1
        self._nat_w = -1
        self._nat_g = -1
        self._nat_flat = -1
        self._nat_parallax = False
        self._nat_stamp = []
        self._nat_tag = None
        self._nat_anim = -1
        self._nat_ring_i = -1
        self._nat_amount = None
        self._nat_rings = -1
        self._nat_aura = -999
        self._nat_sw = -999
        self._nat_bar = -999
        self._nat_spk = -999
        self._nat_spark = []
        self._off_buf = []
        self._sync_ground()

    def _sync_ground(self):
        pack = self.pack
        if pack is None:
            self._scroll_period = float(PERIOD)
            self._ground_top = GAUGE_Y - 2
            self._surface = GAUGE_Y - 2
            self.ground_y = self._surface
            self.ocean_rgb565 = None
            self.ocean_window = None
            self.play_window = None
            self.present_buf = None
            self.ocean_stamps = []
            self._parallax = False
            self._ocean_push = True
            self._drop_sprite_cache()
            return
        # Locked after-preview feet dest, independent of the ground tile.
        self._scroll_period = float(RING_PERIOD)
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
        self._play_valid = False
        self._sprites_on = False
        self._sig_ok = False
        del self._placed[:]
        self.ocean_rgb565 = None
        self.ocean_window = None
        self.play_window = None
        self.present_buf = None
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
        play = None
        try:
            play = bytearray(len(window))
        except MemoryError:
            print("playfield alloc failed")
        self.play_window = play
        self.present_buf = None
        if play is not None:
            try:
                self.present_buf = bytearray(len(play))
                print("present buf %d" % len(self.present_buf))
            except MemoryError:
                print("present buf alloc failed")
                self.present_buf = None
        self._play_valid = False
        del self._placed[:]
        self._bind_parallax_state()
        # Viper self-tests belong to cache prepare. The first timed frame
        # must not pay for them inside compose or stamp.
        try:
            _ensure_scenery()
        except Exception as e:
            print("scenery check failed:", e)
        try:
            _ensure_stamp()
        except Exception as e:
            print("stamp check failed:", e)
        print("ocean rgb565", len(buf), "window", len(window), "play", 0 if play is None else len(play))

    def _bind_parallax_state(self):
        """Offset trackers for the retained window. Not a per-frame rebuild."""
        th = self.ocean_th if self.ocean_th else 0
        self._dirty = bytearray(th)
        self._band_off = [0, 0, 0]
        self._band_new = [0, 0, 0]
        self._band_mode = [0, 0, 0]
        self._band_delta = [0, 0, 0]
        n = len(self.ocean_stamps) if self.ocean_stamps else 0
        self._stamp_off = [0] * n
        self._stamp_new = [0] * n
        self._flat_off = -1
        self._pf_valid = False
        self._play_valid = False
        del self._placed[:]
        del self._bd_clips[:]
        self._bd_mode = 0
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
        self._play_valid = False
        self._native_ready = False
        del self._placed[:]

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
        if _GH is not None:
            if _native_self_test():
                build = getattr(_GH, "BUILD", "on")
                print("native render", build)
            else:
                print("native self-test failed")
                _drop_native()
        else:
            print("native render off")
        if self.play_window is not None:
            # Clean backdrop + playfield replaces the underlay save/restore.
            self._under_buf = None
            self._under_used = 0
            del self._under_spans[:]
            self._retain = True
            return
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

    def advance(self, speed, dt_ms, rpm=None, brk=None):
        if speed is None:
            speed = 0.0
        if rpm is None:
            rpm = 0.0
        if brk is None:
            brk = 0.0
        period = self._scroll_period
        self.scroll = (self.scroll + speed * dt_ms * 0.012) % period
        if speed < 1.0:
            self.phase = 0.0
        else:
            self.phase = (self.phase + dt_ms * (0.002 + 0.00035 * speed)) % 4.0
        self._step_hockey(speed, dt_ms, brk)
        self.boost.tick(speed, dt_ms, rpm)
        self._step_anim(speed, dt_ms)
        self._collect_rings()
        self._step_sparkles(dt_ms)

    def _step_hockey(self, speed, dt_ms, brk):
        """Arm a 3-frame stop when the brake and the decel both stay hard.

        The pose faces the way the run faces. Nothing is drawn until a
        `hockey` cycle exists in the pack, and that cycle has to be
        original art facing right. Ripped sheets stay off the device.
        """
        prev = self._spd_prev
        self._spd_prev = speed
        if prev is None or dt_ms <= 0:
            decel = 0.0
        else:
            decel = (prev - speed) * (1000.0 / float(dt_ms))
        self.decel = decel
        braking = brk >= 0.5
        if self.hockey:
            if (not braking) or decel < HOCKEY_OFF_DECEL:
                self.hockey = False
                self.hockey_i = -1
                self._hockey_arm = 0
                self._hockey_accum = 0
                return
            self._hockey_accum += dt_ms
            if self._hockey_accum >= FX_FRAME_MS:
                self._hockey_accum -= FX_FRAME_MS
                if self._hockey_accum > FX_FRAME_MS:
                    self._hockey_accum = FX_FRAME_MS
                if self.hockey_i < HOCKEY_FRAMES - 1:
                    self.hockey_i += 1
            return
        if braking and decel >= HOCKEY_ON_DECEL and speed > HOCKEY_MIN_KMH:
            self._hockey_arm += dt_ms
            if self._hockey_arm >= HOCKEY_ARM_MS:
                self.hockey = True
                self.hockey_i = 0
                self._hockey_accum = 0
        else:
            self._hockey_arm = 0

    def _cycle_frames(self, tag):
        pack = self.pack
        if pack is None:
            return None
        return pack.cycles.get(tag)

    def _step_anim(self, speed, dt_ms):
        pack = self.pack
        if pack is None:
            return
        hockey_frames = None
        if self.hockey:
            hockey_frames = self._cycle_frames("hockey")
        if hockey_frames:
            tag = "hockey"
        elif speed < 1.0:
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
        if hockey_frames:
            frames = hockey_frames
        else:
            frames = self._cycle_frames(tag)
        if not frames:
            return
        if tag != self.anim_tag:
            # Show the first frame of the new cycle for this present.
            self.anim_tag = tag
            if tag == "hockey":
                self.anim_i = self.hockey_i if self.hockey_i >= 0 else 0
            else:
                self.anim_i = 0
            self.anim_accum = 0
        elif tag == "hockey":
            self.anim_i = self.hockey_i if self.hockey_i >= 0 else 0
            if self.anim_i >= len(frames):
                self.anim_i = len(frames) - 1
        else:
            # Character frames at 15 fps. A late paint still takes one step.
            self.anim_accum += dt_ms
            if self.anim_accum >= FX_FRAME_MS:
                self.anim_accum -= FX_FRAME_MS
                if self.anim_accum > FX_FRAME_MS:
                    self.anim_accum = FX_FRAME_MS
                self.anim_i += 1
                if self.anim_i >= len(frames):
                    self.anim_i = 0
                    if self.anim_tag == "idle_trigger":
                        self.triggering = False
                        self.anim_tag = "idle"
                        self.idle_wait = 0
        self.ring_i = (self.ring_i + 1) & 7

    def _purge_sparkles(self):
        """Drop a sparkle slot that is not [x, y, frame, acc].

        Every writer stores a 4-list. A slot that is an int instead makes
        the native paint raise, and the fallback blit then reboots the
        board. Those slots are removed. The pause line is the only record.
        """
        sp = self.sparkles
        if not isinstance(sp, list):
            self.sparkles = []
            _pause_push("spark", "gc", 1, 0, 0)
            return
        w = 0
        i = 0
        n = len(sp)
        bad = 0
        while i < n:
            item = sp[i]
            keep = False
            if isinstance(item, list) and len(item) >= 4:
                try:
                    item[0] + item[1] + item[2] + item[3]
                    keep = True
                except Exception:
                    keep = False
            if keep:
                if w != i:
                    sp[w] = item
                w += 1
            else:
                bad += 1
            i += 1
        if w != n:
            del sp[w:]
        if bad:
            _pause_push("spark", "gc", bad, 0, 0)

    def _step_sparkles(self, dt_ms):
        self._purge_sparkles()
        sp = self.sparkles
        if not sp:
            return
        pack = self.pack
        frames = self._cycle_frames("rings_sparkle") if pack is not None else None
        n = len(frames) if frames else 0
        w = 0
        i = 0
        nsp = len(sp)
        while i < nsp:
            item = sp[i]
            acc = item[3] + dt_ms
            frame = item[2]
            while acc >= RING_FPS_MS:
                acc -= RING_FPS_MS
                frame += 1
            if n and frame < n:
                item[2] = frame
                item[3] = acc
                if w != i:
                    sp[w] = item
                w += 1
            i += 1
        if w != nsp:
            del sp[w:]


    def _collect_rings(self):
        # A ring counts when its scrolled centre crosses Sonic's front.
        off = int(self.scroll)
        mask = 0
        if self.pack is not None:
            mid = SONIC_FRONT_X
            period = RING_PERIOD
            xs = FIELD_RING_X
            i = 0
            n = len(xs)
            while i < n:
                x = (xs[i] - off) % period
                if x <= mid:
                    mask |= 1 << i
                i += 1
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
                xs = FIELD_RING_X
                i = 0
                n = len(xs)
                while i < n:
                    if (gained >> i) & 1:
                        x = (xs[i] - off) % period
                        self.sparkles.append([x, FIELD_RING_Y, 0, 0])
                    i += 1
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
        # The native frame already stamped the bar, the ring, and the digits.
        if self._fx_in_buffer:
            return
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

    def _stamp_fault(self, buf, fault):
        """Put the CAN-fault dot in the buffer that is about to be pushed.

        The dot is not stored in play_window when a separate present buffer
        exists, so a later held frame does not keep a stale mark. Stamping
        the live playfield itself forces the next frame to recompose.
        """
        _brk_enter("fault")
        try:
            return self._stamp_fault_at(buf, fault)
        finally:
            _brk_exit()

    def _stamp_fault_at(self, buf, fault):
        if buf is None:
            return buf
        if not fault:
            if buf is self.play_window and self._dot_on_play:
                self._native_ready = False
                self._dot_on_play = False
            return buf
        vh = self.ocean_th if self.ocean_th else GAUGE_Y
        stamp_disk565(buf, W, vh, DOT_X, DOT_Y, DOT_R, COL_ERR)
        if buf is self.play_window:
            self._dot_on_play = True
            self._native_ready = False
        return buf

    def draw(self, lcd, values, now=0, fault=False):
        _brk_enter("draw")
        try:
            speed = values.get("spd") or 0.0
            tps = values.get("tps") or 0.0
            clt = values.get("clt") or 0.0
            self._corner_pushed = False
            self.body_col = sonic_body_color(clt, now)
            if self.pack is not None:
                # Ocean and sprite caches are prepared once at pack load
                # (_sync_ground), not on every frame.
                self._draw_sheet_scene(lcd, speed, clt, now, fault)
            else:
                self._playfield_stamped = False
                self._draw_background(lcd)
                tip_y = SONIC_MID_Y
                self._draw_sonic(lcd, speed, tps, clt)
                # Aura is layered on top of Sonic at the locked tip.
                self.boost.draw_behind(lcd, AURA_TIP_X, tip_y)
                self.boost.draw_front(lcd, SW_TIP_X, tip_y)
            self._draw_ring_hud(lcd)
        finally:
            _brk_exit()

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
        self._purge_sparkles()
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

    def _can_retain(self):
        if not (
            self._retain
            and self._sprite_bake
            and self.ocean_rgb565 is not None
            and self.ocean_window is not None
        ):
            return False
        if self.play_window is not None:
            return True
        return self._under_buf is not None

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

    def _band_spans(self):
        th = self.ocean_th
        return (
            (0, HORIZON_Y, 0),
            (HORIZON_Y, GRASS_Y, 1),
            (GRASS_Y, th, 2),
        )

    def _apply_band_scroll(self):
        """Shift a band that only moved a few columns. Large jumps stay full restores.

        mode 0: offset unchanged. mode 1: window rows already shifted, new
        columns painted from the tile. mode 2: caller must restore the band.
        """
        th = self.ocean_th
        tw = self.ocean_tw
        window = self.ocean_window
        tile = self.ocean_rgb565
        for y0, y1, idx in self._band_spans():
            self._band_mode[idx] = 0
            self._band_delta[idx] = 0
            if y1 <= y0:
                continue
            if y0 < 0:
                y0 = 0
            if y1 > th:
                y1 = th
            if y1 <= y0:
                continue
            old = self._band_off[idx]
            new = self._band_new[idx]
            if old == new:
                continue
            d = _signed_delta(old, new, tw)
            self._band_delta[idx] = d
            if d != 0 and d > -W and d < W:
                _shift_window_band(window, W, tile, tw, y0, y1, d, new)
                self._band_mode[idx] = 1
            else:
                self._band_mode[idx] = 2

    def _stamp_carried(self, oy, sh, d_stamp):
        """True when the shift already left this stamp on its new pixels.

        The exposed columns are still clean backdrop. A stamp that crosses a
        band which moved by a different delta, or a band that was not shifted,
        is not carried and has to be erased and redrawn.
        """
        y1 = oy + sh
        saw = False
        for by0, by1, idx in self._band_spans():
            if y1 <= by0 or oy >= by1:
                continue
            saw = True
            mode = self._band_mode[idx]
            if mode == 1:
                if self._band_delta[idx] != d_stamp:
                    return False
            elif mode == 0:
                if d_stamp != 0:
                    return False
            else:
                return False
        return saw

    def _collect_dirty_clips(self):
        """Rectangles whose backdrop pixels are not already correct.

        A shifted band only dirties the columns that entered the screen.
        Stamps that rode along with that shift are restamped there. Stamps
        that did not are restored at the old and new screen boxes.
        """
        clips = []
        th = self.ocean_th
        vw = W
        for y0, y1, idx in self._band_spans():
            mode = self._band_mode[idx]
            if mode == 0 or y1 <= y0:
                continue
            if y0 < 0:
                y0 = 0
            if y1 > th:
                y1 = th
            if y1 <= y0:
                continue
            if mode == 2:
                clips.append((0, y0, vw, y1 - y0))
                continue
            d = self._band_delta[idx]
            if d > 0:
                clips.append((vw - d, y0, d, y1 - y0))
            elif d < 0:
                clips.append((0, y0, 0 - d, y1 - y0))
        stamps = self.ocean_stamps
        old = self._stamp_off
        new = self._stamp_new
        tw = self.ocean_tw
        i = 0
        n = len(stamps)
        while i < n:
            st = stamps[i]
            d_stamp = _signed_delta(old[i], new[i], tw)
            if not self._stamp_carried(st[2], st[4], d_stamp):
                old_rects = _stamp_screen_rects(
                    st[1], st[2], st[3], st[4], old[i], tw, vw, th
                )
                new_rects = _stamp_screen_rects(
                    st[1], st[2], st[3], st[4], new[i], tw, vw, th
                )
                for box in old_rects:
                    clips.append(box)
                for box in new_rects:
                    clips.append(box)
                # A band shift has already slid this stamp's pixels by the
                # band delta. That is not the stamp's own delta, so the
                # pixels now sit outside the pre-shift box. Cover that spot.
                for by0, by1, idx in self._band_spans():
                    if self._band_mode[idx] != 1:
                        continue
                    bd = self._band_delta[idx]
                    if bd == 0 or by1 <= by0:
                        continue
                    band = (0, by0, vw, by1 - by0)
                    for box in old_rects:
                        inter = _rect_intersect(box, band)
                        if inter is None:
                            continue
                        x, y, w, h = inter
                        moved = _clip_box(x - bd, y, w, h, vw, th)
                        if moved is not None:
                            clips.append(moved)
            i += 1
            if (i & 3) == 0:
                _service()
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
            if (i & 3) == 0:
                _service()

    def _clear_band_motion(self):
        """Backdrop did not move. Sprite repair must not reuse an old delta."""
        self._band_mode[0] = 0
        self._band_mode[1] = 0
        self._band_mode[2] = 0
        self._band_delta[0] = 0
        self._band_delta[1] = 0
        self._band_delta[2] = 0

    def _update_flat(self, scroll):
        off = _norm_off(scroll, self.ocean_tw)
        if self._pf_valid and off == self._flat_off:
            return False
        if self.play_window is None and self._pf_valid and self._sprites_on:
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
        """Bring ocean_window back to a clean backdrop for this scroll.

        Sprites are not stored here when play_window exists, so a shift
        does not restore underlays first. Returns True when any band or
        scenery stamp was rewritten. _bd_mode is 0 (held), 1 (shifted),
        or 2 (full compose). _bd_clips are the screen rects patched after
        a shift; a full compose leaves that list empty.
        """
        self._bd_mode = 0
        del self._bd_clips[:]
        if not self._parallax:
            changed = self._update_flat(scroll)
            if changed:
                self._bd_mode = 2
            else:
                self._clear_band_motion()
            return changed
        self._load_new_offs(scroll)
        if self._pf_valid and self._offs_unchanged():
            self._clear_band_motion()
            return False
        if self.play_window is None and self._pf_valid and self._sprites_on:
            self._restore_underlays()
        if not self._pf_valid:
            self._compose_full(scroll)
            self._bd_mode = 2
            return True
        _service()
        self._apply_band_scroll()
        clips = self._collect_dirty_clips()
        if not clips:
            self._commit_offs()
            if self.play_window is None:
                self._sprites_on = False
                self._under_used = 0
                del self._under_spans[:]
            self._bd_mode = 1
            return True
        if self._clips_cover_all(clips):
            self._compose_full(scroll)
            self._bd_mode = 2
            return True
        self._patch_dirty(clips, scroll)
        self._commit_offs()
        self._pf_valid = True
        if self.play_window is None:
            self._sprites_on = False
            self._under_used = 0
            del self._under_spans[:]
        self._bd_mode = 1
        for clip in clips:
            self._bd_clips.append(clip)
        return True

    def _sprite_unchanged(self, amount):
        self._purge_sparkles()
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
        self._purge_sparkles()
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

    def _plan_places(self, amount):
        _brk_enter("plan")
        try:
            return self._plan_places_at(amount)
        finally:
            _brk_exit()

    def _plan_places_at(self, amount):
        """Screen rects in stamp order. Top-left is after the frame origin.

        Order matches _stamp_playfield_sprites: rings, sparkles, Sonic,
        HUD bar, HUD ring. extra is the tint for Sonic and 0 otherwise.
        """
        self._purge_sparkles()
        out = self._planned
        del out[:]
        pack = self.pack
        if pack is None:
            return out
        off = int(self.scroll)
        ring_frames = pack.cycles.get("rings")
        if ring_frames:
            fr = ring_frames[self.ring_i % len(ring_frames)]
            period = RING_PERIOD
            xs = FIELD_RING_X
            keys = _RING_PLACE_KEYS
            idx = 0
            n = len(xs)
            while idx < n:
                x = (xs[idx] - off) % period
                if x > W + 40:
                    x -= period
                if -40 < x < W + 40:
                    x0 = int(x - fr.origin_x)
                    y0 = int(FIELD_RING_Y - fr.origin_y)
                    self._emit_place(out, keys[idx], x0, y0, fr.w, fr.h, fr, 0, "rings")
                idx += 1
        spark_frames = pack.cycles.get("rings_sparkle")
        if spark_frames:
            si = 0
            keys = _SPARK_PLACE_KEYS
            nkeys = len(keys)
            for item in self.sparkles:
                x, y, i, _acc = item
                if i < len(spark_frames):
                    fr = spark_frames[i]
                    x0 = int(int(x) - fr.origin_x)
                    y0 = int(int(y) - fr.origin_y)
                    if si < nkeys:
                        key = keys[si]
                    else:
                        key = "k%d" % si
                    self._emit_place(out, key, x0, y0, fr.w, fr.h, fr, 0, "rings")
                si += 1
        frames = self._cycle_frames(self.anim_tag)
        if frames:
            fr = frames[self.anim_i % len(frames)]
            x0 = int(SONIC_FEET_DEST_X - fr.origin_x)
            y0 = int(SONIC_FEET_DEST_Y - fr.origin_y)
            self._emit_place(out, "sonic", x0, y0, fr.w, fr.h, fr, amount, "sonic")
        bar = pack.by_name.get("hud_bar")
        if bar is not None:
            self._emit_place(
                out, "hud_bar", HUD_BAR_POS[0], HUD_BAR_POS[1], bar.w, bar.h, bar, 0, "hud"
            )
        ring = pack.by_name.get("hud_ring")
        if ring is not None:
            self._emit_place(
                out, "hud_ring", HUD_RING_POS[0], HUD_RING_POS[1], ring.w, ring.h, ring, 0, "hud"
            )
        return out

    def _emit_place(self, out, key, x, y, w, h, fr, extra, kind):
        """Reuse one slot per key. The retain path snapshots before the next plan."""
        pool = self._place_pool
        slot = pool.get(key)
        if slot is None:
            slot = [key, x, y, w, h, fr, extra, kind]
            pool[key] = slot
        else:
            slot[1] = x
            slot[2] = y
            slot[3] = w
            slot[4] = h
            slot[5] = fr
            slot[6] = extra
            slot[7] = kind
        out.append(slot)

    def _snapshot_places(self, places):
        """Copy slot values. The live slots are rewritten on the next plan."""
        saved = []
        for p in places:
            saved.append((p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]))
        return saved

    def _places_same(self, places):
        old = self._placed
        n = len(places)
        if len(old) != n:
            return False
        i = 0
        while i < n:
            a = old[i]
            b = places[i]
            if (
                a[0] != b[0]
                or a[1] != b[1]
                or a[2] != b[2]
                or a[3] != b[3]
                or a[4] != b[4]
                or a[5].name != b[5].name
                or a[6] != b[6]
            ):
                return False
            i += 1
        return True

    def _hits(self, rect, rects):
        for other in rects:
            if _rect_intersect(rect, other) is not None:
                return True
        return False

    def _place_carried(self, old, new, clips):
        """True when the band shift already left this sprite on its new pixels."""
        if old[3] != new[3] or old[4] != new[4]:
            return False
        if old[5].name != new[5].name or old[6] != new[6]:
            return False
        if old[2] != new[2]:
            return False
        d = old[1] - new[1]
        y0 = new[2]
        y1 = y0 + new[4]
        if y1 <= y0:
            return False
        saw = False
        for by0, by1, idx in self._band_spans():
            if y1 <= by0 or y0 >= by1:
                continue
            saw = True
            if d == 0:
                if self._band_mode[idx] != 0:
                    return False
            elif self._band_mode[idx] != 1 or self._band_delta[idx] != d:
                return False
        if not saw:
            return False
        if self._hits((new[1], new[2], new[3], new[4]), clips):
            return False
        return True

    def _post_shift_rects(self, place):
        """Where this sprite's pixels sit after the playfield band shift."""
        x = place[1]
        y = place[2]
        w = place[3]
        h = place[4]
        vh = self.ocean_th
        found = []
        y_end = y + h
        for by0, by1, idx in self._band_spans():
            iy0 = y if y > by0 else by0
            iy1 = y_end if y_end < by1 else by1
            if iy0 < 0:
                iy0 = 0
            if iy1 > vh:
                iy1 = vh
            if iy1 <= iy0 or w <= 0:
                continue
            d = self._band_delta[idx] if self._band_mode[idx] == 1 else 0
            found.append((x - d, iy0, w, iy1 - iy0))
        return found

    def _copy_rect(self, src, dst, rect):
        box = _clip_box(rect[0], rect[1], rect[2], rect[3], W, self.ocean_th)
        if box is None:
            return
        x, y, w, h = box
        if w <= 0 or h <= 0:
            return
        vw = W
        nbytes = w * 2
        row = 0
        while row < h:
            o = ((y + row) * vw + x) * 2
            dst[o:o + nbytes] = src[o:o + nbytes]
            row += 1
            if (row & 7) == 0:
                _service()

    def _copy_full_play(self):
        src = self.ocean_window
        dst = self.play_window
        n = len(src)
        if dst is None or len(dst) != n:
            raise MemoryError("playfield")
        step = 4096
        i = 0
        while i < n:
            j = i + step
            if j > n:
                j = n
            dst[i:j] = src[i:j]
            i = j
            _service()

    def _shift_play_band(self, y0, y1, delta):
        """Same slide as the clean window. New columns come from that window."""
        if delta == 0 or y1 <= y0:
            return
        window = self.play_window
        clean = self.ocean_window
        vw = W
        scratch = _ROW_SCRATCH
        y = y0
        if delta > 0:
            if delta >= vw:
                return
            keep = vw - delta
            n = keep * 2
            src = delta * 2
            ncol = delta * 2
            while y < y1:
                if ((y - y0) & 7) == 0:
                    _service()
                row = y * vw * 2
                scratch[0:n] = window[row + src:row + src + n]
                window[row:row + n] = scratch[0:n]
                di = row + n
                window[di:di + ncol] = clean[di:di + ncol]
                y += 1
            return
        e = 0 - delta
        if e >= vw:
            return
        keep = vw - e
        n = keep * 2
        dst = e * 2
        ncol = e * 2
        while y < y1:
            if ((y - y0) & 7) == 0:
                _service()
            row = y * vw * 2
            scratch[0:n] = window[row:row + n]
            window[row + dst:row + dst + n] = scratch[0:n]
            window[row:row + ncol] = clean[row:row + ncol]
            y += 1

    def _shift_play_bands(self):
        th = self.ocean_th
        for y0, y1, idx in self._band_spans():
            if self._band_mode[idx] != 1:
                continue
            if y0 < 0:
                y0 = 0
            if y1 > th:
                y1 = th
            if y1 <= y0:
                continue
            self._shift_play_band(y0, y1, self._band_delta[idx])

    def _note_stamp_part(self, kind, dt):
        if dt < 0:
            dt = 0
        if kind == "hud":
            self._part_hud += dt
        elif kind == "sonic":
            self._part_sonic += dt
        else:
            self._part_rings += dt

    def _stamp_places(self, places, window, which):
        """Stamp every place, or only keys listed in which. which None stamps all."""
        vw = W
        vh = self._playfield_vh()
        for p in places:
            if which is not None and p[0] not in which:
                continue
            t0 = _mono_us()
            self._stamp_frame(window, vw, vh, p[5], p[1], p[2], False, p[6], False)
            self._note_stamp_part(p[7], _us_diff(_mono_us(), t0))
            _service()

    def _sync_playfield(self, amount):
        """Copy the clean backdrop into play_window and restamp dirty sprites.

        ocean_window has just been updated and holds no sprites. A held
        backdrop with the same sprites leaves the playfield untouched.
        """
        self._part_hud = 0
        self._part_sonic = 0
        self._part_rings = 0
        places = self._plan_places(amount)
        mode = self._bd_mode
        if (not self._play_valid) or mode == 2:
            self._copy_full_play()
            self._stamp_places(places, self.play_window, None)
            self._placed = self._snapshot_places(places)
            self._play_valid = True
            return
        if mode == 0 and self._places_same(places):
            return
        if mode == 1:
            self._shift_play_bands()
            for clip in self._bd_clips:
                self._copy_rect(self.ocean_window, self.play_window, clip)
        repairs = []
        marked = []
        old_map = {}
        for old in self._placed:
            old_map[old[0]] = old
        clips = self._bd_clips
        seen = {}
        for p in places:
            seen[p[0]] = p
            old = old_map.get(p[0])
            if old is not None and self._place_carried(old, p, clips):
                continue
            marked.append(p[0])
            repairs.append((p[1], p[2], p[3], p[4]))
            if old is not None:
                for rect in self._post_shift_rects(old):
                    repairs.append(rect)
        for key in old_map:
            if key not in seen:
                for rect in self._post_shift_rects(old_map[key]):
                    repairs.append(rect)
        grew = True
        while grew:
            grew = False
            for p in places:
                key = p[0]
                if key in marked:
                    continue
                rect = (p[1], p[2], p[3], p[4])
                if self._hits(rect, repairs):
                    marked.append(key)
                    repairs.append(rect)
                    grew = True
        for rect in repairs:
            self._copy_rect(self.ocean_window, self.play_window, rect)
        if marked:
            self._stamp_places(places, self.play_window, marked)
        self._placed = self._snapshot_places(places)
        self._play_valid = True

    def _stamp_playfield_sprites(self, amount, save_under=False, window=None):
        self._purge_sparkles()
        pack = self.pack
        if window is None:
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

    def note_chrome(self, fill_us, flush_us):
        """fillScreen and the full-canvas push. 0 when that work was skipped."""
        if fill_us < 0:
            fill_us = 0
        if flush_us < 0:
            flush_us = 0
        self._tim_fill += fill_us
        self._tim_flush += flush_us

    def report_timing(self, now, speed=0.0):
        """One serial line per 2 seconds. Does not rebuild caches.

        The V14 fields stay in front so old logs still compare. hud, sonic,
        and rings are the stamp split (they sit inside stamp, along with
        the playfield shift and the backdrop copies). shift and full count
        backdrop updates. A USB CDC that has not drained the previous line
        skips the print; the counters stay. gc stays 0 here. A heap walk
        or a collect on this line was inside the frame: on the PR #13
        listen the walk alone was the 55 ms, then 118 ms, timing hitch.
        The one slice, when the frame budget has room, runs after
        ui.tick returns.
        """
        n = self._tim_n
        if n <= 0 or self._tim_t0 == 0:
            return
        elapsed = _ms_diff(now, self._tim_t0)
        if elapsed < 2000:
            return
        # speed is unused. The aura and the collect no longer read it here.
        gc_us = 0
        if not _stdout_can_write():
            return
        _service()
        total_us = self._tim_compose + self._tim_stamp + self._tim_push
        fps_tenths = 0
        if elapsed > 0:
            fps_tenths = (n * 10000) // elapsed
        _brk_enter("serial")
        try:
            print(
            "timing n=%d compose %s stamp %s push %s fill %s flush %s gc %s total %s fps %s hold %d hud %s sonic %s rings %s shift %d full %d"
            % (
                n,
                _fmt_tenths(_avg_tenths_ms(self._tim_compose, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_stamp, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_push, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_fill, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_flush, n)),
                _fmt_tenths(_avg_tenths_ms(gc_us, 1)),
                _fmt_tenths(_avg_tenths_ms(total_us, n)),
                _fmt_tenths(fps_tenths),
                self._tim_hold,
                _fmt_tenths(_avg_tenths_ms(self._tim_hud, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_sonic, n)),
                _fmt_tenths(_avg_tenths_ms(self._tim_rings, n)),
                self._tim_shift,
                self._tim_full,
            )
            )
            global _DIAG_SKIP_BRK
            _DIAG_SKIP_BRK = True
        finally:
            _brk_exit()
        self._tim_n = 0
        self._tim_compose = 0
        self._tim_stamp = 0
        self._tim_push = 0
        self._tim_fill = 0
        self._tim_flush = 0
        self._tim_hold = 0
        self._tim_hud = 0
        self._tim_sonic = 0
        self._tim_rings = 0
        self._tim_shift = 0
        self._tim_full = 0
        self._tim_t0 = now

    def _frame_index(self, fr):
        pack = self.pack
        w = fr.w
        h = fr.h
        bpp4 = pack.bpp == 4
        row_bytes = ((w + 1) // 2) if bpp4 else w
        base = pack.pix_off + fr.data_offset
        src = memoryview(pack.data)[base:base + row_bytes * h]
        return src, row_bytes, 4 if bpp4 else 8

    def _native_views(self, fr, amount):
        """Baked RGB565 and mask. A hot tint is expanded in C from the pairs."""
        at = self._bake_at.get(fr.name)
        if at is None:
            raise KeyError(fr.name)
        rgb_off, mask_off = at
        n = fr.w * fr.h
        mask = memoryview(self._arena)[mask_off:mask_off + ((n + 7) // 8)]
        if amount > 0:
            pairs = _tint_pair_bytes(self.pack, fr, amount)
            scratch = memoryview(self._arena)[self._scratch_off:self._scratch_off + (n * 2)]
            src, stride, bpp = self._frame_index(fr)
            _GH.expand(src, stride, bpp, fr.w, fr.h, pairs, len(pairs) // 2, scratch)
            return scratch, mask
        rgb = memoryview(self._arena)[rgb_off:rgb_off + (n * 2)]
        return rgb, mask

    def _fill_native_offs(self, scroll):
        _brk_enter("plan")
        try:
            return self._fill_native_offs_at(scroll)
        finally:
            _brk_exit()

    def _fill_native_offs_at(self, scroll):
        """Write stamp offsets into _off_buf and _off_c / _off_w / _off_g."""
        tw = self.ocean_tw
        offs = self._off_buf
        del offs[:]
        if not self._parallax:
            self._off_c = _norm_off(scroll, tw)
            self._off_w = 0
            self._off_g = 0
            return
        off_c = _norm_off(scroll * PARALLAX_CLOUDS, tw)
        off_w = _norm_off(scroll * PARALLAX_WATER, tw)
        off_g = _norm_off(scroll * PARALLAX_GROUND, tw)
        stamps = self.ocean_stamps
        i = 0
        n = len(stamps) if stamps else 0
        while i < n:
            offs.append(_norm_off(scroll * stamps[i][0], tw))
            i += 1
        self._off_c = off_c
        self._off_w = off_w
        self._off_g = off_g

    def _native_same(self, off_c, off_w, off_g, amount):
        _brk_enter("plan")
        try:
            return self._native_same_at(off_c, off_w, off_g, amount)
        finally:
            _brk_exit()

    def _native_same_at(self, off_c, off_w, off_g, amount):
        self._purge_sparkles()
        if not self._native_ready:
            return False
        if self._parallax:
            if not self._nat_parallax:
                return False
            if self._nat_c != off_c or self._nat_w != off_w or self._nat_g != off_g:
                return False
            prev = self._nat_stamp
            cur = self._off_buf
            if len(prev) != len(cur):
                return False
            i = 0
            while i < len(cur):
                if prev[i] != cur[i]:
                    return False
                i += 1
        else:
            if self._nat_parallax or self._nat_flat != off_c:
                return False
        if self._nat_tag != self.anim_tag or self._nat_anim != self.anim_i:
            return False
        if self._nat_ring_i != self.ring_i or self._nat_amount != amount:
            return False
        if self._nat_rings != self.rings:
            return False
        boost = self.boost
        if (
            self._nat_aura != boost.aura_i
            or self._nat_sw != boost.sw_i
            or self._nat_bar != boost.bar_i
            or self._nat_spk != boost.spark_i
        ):
            return False
        sp = self.sparkles
        prev_s = self._nat_spark
        if len(sp) != len(prev_s):
            return False
        i = 0
        while i < len(sp):
            item = sp[i]
            p = prev_s[i]
            if item[0] != p[0] or item[1] != p[1] or item[2] != p[2]:
                return False
            i += 1
        return True

    def _remember_native(self, off_c, off_w, off_g, amount):
        self._purge_sparkles()
        self._nat_parallax = bool(self._parallax)
        self._nat_c = off_c
        self._nat_w = off_w
        self._nat_g = off_g
        if self._parallax:
            cur = self._off_buf
            prev = self._nat_stamp
            n = len(cur)
            if len(prev) != n:
                # Stamp count changes when the pack loads, not per present.
                self._nat_stamp = cur[:]
            else:
                i = 0
                while i < n:
                    prev[i] = cur[i]
                    i += 1
        else:
            self._nat_flat = off_c
            del self._nat_stamp[:]
        self._nat_tag = self.anim_tag
        self._nat_anim = self.anim_i
        self._nat_ring_i = self.ring_i
        self._nat_amount = amount
        self._nat_rings = self.rings
        boost = self.boost
        self._nat_aura = boost.aura_i
        self._nat_sw = boost.sw_i
        self._nat_bar = boost.bar_i
        self._nat_spk = boost.spark_i
        prev = self._nat_spark
        del prev[:]
        for item in self.sparkles:
            prev.append((item[0], item[1], item[2]))
        self._native_ready = True

    def _paint_native(self, amount, with_fx, offs_ready=False):
        """Fill play_window. Fills _paint_us: compose, stamp, hud, sonic, rings, fx, digits.

        with_fx draws boost and the ring digits into the same buffer. The
        playfield underneath matches a full V15 compose plus stamp.
        offs_ready means the caller already wrote the scroll offsets.
        """
        gh = _GH
        window = self.play_window
        vw = W
        th = self.ocean_th
        vh = self._playfield_vh()
        tw = self.ocean_tw
        tile = self.ocean_rgb565
        if not offs_ready:
            self._fill_native_offs(self.scroll)
        off_c = self._off_c
        off_w = self._off_w
        off_g = self._off_g
        t0 = _mono_us()
        _brk_enter("compose")
        try:
            if self._parallax:
                gh.compose(
                    tile, tw, th, off_c, off_w, off_g, HORIZON_Y, GRASS_Y,
                    self.ocean_stamps, self._off_buf, window,
                )
            else:
                gh.copy(tile, tw, th, off_c, window, vw)
        finally:
            _brk_exit()
        t1 = _mono_us()
        # No yield here. A service call is a VM safe point, and the sliced
        # collector takes another ~3 ms slice once 12 ms have passed. The
        # tick already polls the button around this paint.
        places = self._plan_places(amount)
        hud_us = 0
        sonic_us = 0
        rings_us = 0
        for p in places:
            kind = p[7]
            if kind != "sonic" and kind != "hud" and kind != "rings":
                kind = "rings"
            ts = _mono_us()
            _brk_enter(kind)
            try:
                fr = p[5]
                if fr.nruns and hasattr(gh, "stamp_runs"):
                    if p[6] > 0:
                        pairs = _tint_pair_bytes(self.pack, fr, p[6])
                    else:
                        pal_id = fr.pal_id
                        if pal_id < 0 or pal_id >= len(self.pack.pair_bytes):
                            pal_id = 0
                        pairs = self.pack.pair_bytes[pal_id]
                    gh.stamp_runs(
                        window, vw, vh, fr.runs, fr.nruns, pairs, len(pairs) // 2, p[1], p[2]
                    )
                else:
                    rgb, mask = self._native_views(fr, p[6])
                    gh.stamp(window, vw, vh, rgb, mask, fr.w, fr.h, p[1], p[2])
            finally:
                _brk_exit()
            dt = _us_diff(_mono_us(), ts)
            if dt < 0:
                dt = 0
            if p[7] == "hud":
                hud_us += dt
            elif p[7] == "sonic":
                sonic_us += dt
            else:
                rings_us += dt
        fx_us = 0
        dig_us = 0
        if with_fx:
            boost = self.boost
            t_fx = _mono_us()
            _brk_enter("fx")
            try:
                gh.fx(
                    window, vw, vh,
                    boost.aura_i, boost.sw_i, boost.bar_i,
                    AURA_TIP_X, AURA_TIP_Y, SW_TIP_X,
                    boost.spark_i, BAR_TIP_X, SPARK_FRONT_X, SPARK_MID_Y,
                )
            finally:
                _brk_exit()
            fx_us = _us_diff(_mono_us(), t_fx)
            if fx_us < 0:
                fx_us = 0
            t_dig = _mono_us()
            _brk_enter("dig")
            try:
                gh.digits(window, vw, vh, HUD_BAR_POS[0], HUD_BAR_POS[1], int(self.rings) % 1000)
            finally:
                _brk_exit()
            dig_us = _us_diff(_mono_us(), t_dig)
            if dig_us < 0:
                dig_us = 0
            hud_us += dig_us
            # Boost sits in the stamp total. Digits are the only part of that
            # overlay counted in hud. The two-second line still uses that
            # combined stamp. The per-frame stage line splits fx and digits.
            stamp_us = _us_diff(_mono_us(), t1)
            if stamp_us < 0:
                stamp_us = 0
        else:
            stamp_us = hud_us + sonic_us + rings_us
        compose_us = _us_diff(t1, t0)
        if compose_us < 0:
            compose_us = 0
        times = self._paint_us
        times[0] = compose_us
        times[1] = stamp_us
        times[2] = hud_us
        times[3] = sonic_us
        times[4] = rings_us
        times[5] = fx_us
        times[6] = dig_us
        return times

    def _present_copy(self, src):
        """Hand the playfield to the push. The driver blocks until SPI is done.

        A 26 KB copy was ~2.3 ms on every cruise frame and still left the
        next compose waiting, because drawRawBuf does not return early.
        The next compose overwrites this buffer only after that return.
        """
        return src

    def _draw_native_frame(self, lcd, amount, fault=False):
        """Compose, stamp, and push through C. False falls back to Python."""
        if (
            _GH is None
            or self.play_window is None
            or self.ocean_rgb565 is None
            or not self._sprite_bake
        ):
            return False
        _FLOW["gap_printed"] = 0
        self._fill_native_offs(self.scroll)
        off_c = self._off_c
        off_w = self._off_w
        off_g = self._off_g
        held = 0
        compose_us = 0
        stamp_us = 0
        sprite_us = 0
        fx_us = 0
        dig_us = 0
        between_us = 0
        self._part_hud = 0
        self._part_sonic = 0
        self._part_rings = 0
        t_paint = _mono_us()
        if self._native_same(off_c, off_w, off_g, amount):
            held = 1
        else:
            times = self._paint_native(amount, True, True)
            compose_us = times[0]
            stamp_us = times[1]
            hud_us = times[2]
            sonic_us = times[3]
            rings_us = times[4]
            fx_us = times[5]
            dig_us = times[6]
            self._remember_native(off_c, off_w, off_g, amount)
            sprite_us = hud_us + sonic_us + rings_us - dig_us
            if sprite_us < 0:
                sprite_us = 0
            between_us = _us_diff(_mono_us(), t_paint) - compose_us - sprite_us - fx_us - dig_us
            if between_us < 0:
                between_us = 0
            self._tim_full += 1
            self._tim_hud += hud_us
            self._tim_sonic += sonic_us
            self._tim_rings += rings_us
            self._part_hud = hud_us
            self._part_sonic = sonic_us
            self._part_rings = rings_us
        t_copy = _mono_us()
        # Blit has returned. Copy into a buffer the next paint cannot touch,
        # then start the panel transfer.
        hand = self._present_copy(self.play_window)
        copy_us = _us_diff(_mono_us(), t_copy)
        if copy_us < 0:
            copy_us = 0
        self._stamp_fault(hand, fault)
        _FLOW["paint"] = compose_us + sprite_us + fx_us + dig_us + between_us + copy_us
        t0 = _mono_us()
        ok = push_rgb565_window(lcd, hand, W, self.ocean_th)
        push_us = _us_diff(_mono_us(), t0)
        if push_us < 0:
            push_us = 0
        parts = _PUSH_PARTS
        if FRAME_TRACE:
            print(
                "stage us compose %d stamp %d fx %d digits %d between %d copy %d pre %d raw %d post %d held %d"
                % (
                    compose_us, sprite_us, fx_us, dig_us, between_us, copy_us,
                    parts["pre"], parts["raw"], parts["post"], held,
                )
            )
        if not ok:
            self._ocean_push = False
            self._native_ready = False
            self._fx_in_buffer = False
            self._playfield_stamped = False
            return False
        self._corner_pushed = True
        _note_presented()
        self._fx_in_buffer = True
        self._playfield_stamped = True
        self._note_timing(compose_us, stamp_us, push_us, held)
        return True

    def _draw_sheet_scene(self, lcd, speed, clt, now, fault=False):
        pack = self.pack
        pal_s = pack.pal(1)
        pal_c = pack.pal(0)
        pal_b = pack.pal(2) if len(pack.palettes) > 2 else pal_s
        amount = sonic_tint_amount(clt, now)
        if amount > 0:
            pal_c = tint_blue_palette(pal_c, amount)
        self._fx_in_buffer = False
        if _GH is not None and self.play_window is not None and self._sprite_bake:
            try:
                if self._draw_native_frame(lcd, amount, fault):
                    return
            except Exception as e:
                print("native frame failed:", e)
                self._native_ready = False
                self._fx_in_buffer = False
                self._play_valid = False
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
        push_buf = self.ocean_window
        t_enter = _mono_us()
        ocean_ready = (
            self.ocean_rgb565 is not None
            and self._ocean_push
            and self.ocean_window is not None
        )
        if ocean_ready and self._can_retain():
            try:
                self._part_hud = 0
                self._part_sonic = 0
                self._part_rings = 0
                self._update_backdrop(self.scroll)
                t1 = _mono_us()
                if self.play_window is not None:
                    self._sync_playfield(amount)
                    push_buf = self.play_window
                else:
                    self._update_sprites(amount)
                t2 = _mono_us()
                compose_us = _us_diff(t1, t_enter)
                stamp_us = _us_diff(t2, t1)
                held = 1 if self._bd_mode == 0 else 0
                if self._bd_mode == 1:
                    self._tim_shift += 1
                elif self._bd_mode == 2:
                    self._tim_full += 1
                self._tim_hud += self._part_hud
                self._tim_sonic += self._part_sonic
                self._tim_rings += self._part_rings
                window_ready = True
                stamped = True
                used_retain = True
            except MemoryError:
                self.play_window = None
                self._play_valid = False
                self._disable_retain()
                used_retain = False
                push_buf = self.ocean_window
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
            self._play_valid = False
            self._sprites_on = False
            push_buf = self.ocean_window
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
            hand = self._present_copy(push_buf)
            self._stamp_fault(hand, fault)
            ok = push_rgb565_window(lcd, hand, W, self.ocean_th)
            push_us = _us_diff(_mono_us(), t0)
            if not ok:
                self._ocean_push = False
                self._drop_sprite_cache()
                window_ready = False
                stamped = False
            else:
                self._corner_pushed = True
                _note_presented()
                if stamped:
                    self._playfield_stamped = True
        if ocean_ready:
            self._note_timing(compose_us, stamp_us, push_us, held)
        if not self._playfield_stamped:
            if not window_ready:
                if ocean is not None:
                    blit_tiled_x(lcd, pack, ocean, 0, off, pal_b, 0, GAUGE_Y)
            self._blit_playfield_sprites(lcd, pal_s, pal_c, off)
        # Aura / shockwave on top of Sonic, after the playfield push.
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


class ButtonWatch:
    """Poll the face button on every call. One press starts the bench sweep.

    M5.update() runs at most once per FRAME_MIN_MS. wasClicked is read in
    that same call. Extra polls during a paint only feed the watchdog.
    BtnA, Btn, and BtnPWR only. This board has no touch screen.
    """

    def __init__(self, btn, on_press=None):
        self.btn = btn
        self.on_press = on_press
        self._last_poll = None

    def poll(self, now=None):
        _brk_enter("poll")
        try:
            if now is None:
                now = time.ticks_ms()
            last = self._last_poll
            if last is not None:
                gap = time.ticks_diff(now, last)
                if 0 <= gap < FRAME_MIN_MS:
                    return False
            self._last_poll = now
            _brk_enter("m5")
            try:
                M5.update()
            finally:
                _brk_exit()
            btn = self.btn
            if btn is None:
                return False
            clicked = False
            if hasattr(btn, "wasClicked"):
                try:
                    clicked = bool(btn.wasClicked())
                except Exception:
                    clicked = False
            if hasattr(btn, "wasDoubleClicked"):
                try:
                    extra = bool(btn.wasDoubleClicked())
                except Exception:
                    extra = False
                if extra and not clicked:
                    clicked = True
            if clicked and self.on_press is not None:
                self.on_press(now)
            return clicked
        finally:
            _brk_exit()


class Dashboard:
    def __init__(self, lcd, pack=None, button=None):
        self.panel = lcd
        self.lcd, self._use_push = make_canvas(lcd)
        self.sprite = Sprite(pack)
        self.button = button
        self._last_fill = {}
        for g in GAUGES:
            # width, color. width -1 means the bar has not been painted.
            # A reused pair: assigning a fresh tuple every change was garbage.
            self._last_fill[g[0]] = [-1, 0]
        self._gauge_bg = False
        self._fault_shown = None
        self._last_ms = time.ticks_ms()

    def _poll_button(self, now):
        if self.button is not None:
            self.button.poll(now)

    def _direct_playfield(self):
        """The playfield image is pushed onto the panel, so a clear would erase it."""
        sp = self.sprite
        return (
            sp.pack is not None
            and sp._ocean_push
            and sp.ocean_window is not None
            and sp.ocean_rgb565 is not None
        )

    def tick(self, values, fault, now):
        _brk_enter("tick")
        try:
            self._tick_body(values, fault, now)
        finally:
            _brk_exit()

    def _tick_body(self, values, fault, now):
        self._poll_button(now)
        dt = anim_dt(time.ticks_diff(now, self._last_ms))
        self._last_ms = now
        speed = values.get("spd") or 0.0
        rpm = values.get("rpm")
        if rpm is None:
            rpm = 0.0
        t_adv = _mono_us()
        _brk_enter("adv")
        try:
            brk = values.get("brk")
            if brk is None:
                brk = 0.0
            self.sprite.advance(speed, dt, rpm, brk)
        finally:
            _brk_exit()
        _flow_add("adv", _us_diff(_mono_us(), t_adv))

        # pushImage already covers y 0-102. Gauges repaint y 103-127.
        # Skipping fillScreen and the canvas push avoids a second full-panel
        # flush and a clear that would wipe the playfield.
        direct = self._direct_playfield()
        surface = self.panel if direct else self.lcd
        fill_us = 0
        flush_us = 0
        if not direct:
            _service()
            t0 = _mono_us()
            _brk_enter("fill")
            try:
                surface.fillScreen(COL_BG)
            finally:
                _brk_exit()
            fill_us = _us_diff(_mono_us(), t0)
            if fill_us < 0:
                fill_us = 0
            self._gauge_bg = False
            for key in self._last_fill:
                self._last_fill[key][0] = -1
            _service()
        noted = self.sprite._tim_n
        self.sprite.draw(surface, values, now, fault)
        self._poll_button(now)
        prev = self.lcd
        self.lcd = surface
        t_gauge = _mono_us()
        _brk_enter("gauge")
        try:
            self._draw_gauges(values)
            if not self.sprite._corner_pushed:
                self._draw_fault(fault)
            else:
                self._fault_shown = fault
        finally:
            self.lcd = prev
            _flow_add("gauge", _us_diff(_mono_us(), t_gauge))
            _brk_exit()
        if (not direct) and self._use_push:
            _service()
            t0 = _mono_us()
            _brk_enter("flush")
            try:
                try:
                    surface.push(0, 0)
                except Exception:
                    self._use_push = False
            finally:
                _brk_exit()
            flush_us = _us_diff(_mono_us(), t0)
            if flush_us < 0:
                flush_us = 0
            _service()
        if self.sprite._tim_n != noted:
            self.sprite.note_chrome(fill_us, flush_us)
        self._poll_button(now)
        _brk_enter("timing")
        try:
            self.sprite.report_timing(now, speed)
        finally:
            _brk_exit()

    def _draw_gauges(self, values):
        """Update the four bars in place.

        The near-black band is painted once. A later frame only repaints a
        bar whose width or color changed. A shrink is the track color on
        the uncovered span, not another full-band clear.
        """
        lcd = self.lcd
        if not self._gauge_bg:
            lcd.fillRect(0, GAUGE_Y, W, GAUGE_H, 0x101012)
            self._gauge_bg = True
            for key in self._last_fill:
                self._last_fill[key][0] = -1
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
            color = COL_ORANGE if mode == "orange" else value_rgb(frac)
            prev = self._last_fill[key]
            old_w = prev[0]
            if old_w == -1:
                lcd.fillRect(x, y, w, h, COL_TRACK)
                if fill_w > 0:
                    lcd.fillRect(x, y, fill_w, h, color)
            else:
                old_color = prev[1]
                if old_w == fill_w and old_color == color:
                    continue
                if color != old_color and fill_w > 0:
                    lcd.fillRect(x, y, fill_w, h, color)
                if fill_w < old_w:
                    lcd.fillRect(x + fill_w, y, old_w - fill_w, h, COL_TRACK)
                elif fill_w > old_w:
                    lcd.fillRect(x + old_w, y, fill_w - old_w, h, color)
            prev[0] = fill_w
            prev[1] = color

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
        if (n & 7) == 0:
            _service()
        if n >= 64:
            break
    return n


def _begin_keep_splash():
    """Start M5 without clearing the panel.

    board_init already called M5.begin before Python, and the UiFlow
    wrapper clears again when clear_display stays at its default. That
    clear is the black hold through the ocean and sprite caches. The
    boot mark stays up until the first playfield push covers it.
    """
    try:
        M5.begin({"clear_display": False})
    except Exception:
        pass


def _show_boot_splash(lcd):
    try:
        import splash_mark
        splash_mark.show(lcd)
    except Exception:
        pass


def main():
    global _BTN_HOOK
    _begin_keep_splash()
    lcd = Lcd
    # Paint again in case this boot did not come through boot.py. The
    # mark then stays through pixidx load and the sprite cache.
    _show_boot_splash(lcd)
    pack = try_load_pixidx()
    sweep = Sweep()
    button = ButtonWatch(find_btn(), sweep.start)
    # Ocean and sprite caches allocate here. Arm the watchdog after that so
    # the first build cannot reset the device.
    ui = Dashboard(lcd, pack, button=button)
    _init_wdt()

    def _face_hook():
        button.poll(time.ticks_ms())

    _BTN_HOOK = _face_hook
    try:
        import ota_client
    except ImportError:
        ota_client = None
    _install_ota_fetch_timeout(ota_client)
    _last_fetch_ms = -PIXIDX_FETCH_EVERY_MS

    values = {"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0, "brk": 0.0}
    last_rx_ms = 0
    next_draw = None
    last_ota_ms = 0
    present_t0 = None
    boot_sweep_armed = True
    sweep_idle_since = None
    loop_mark = _mono_us()

    bus = None
    try:
        bus = init_can()
    except Exception:
        bus = None

    while True:
        iter_us = _mono_us()
        _feed_wdt()
        if next_draw is not None:
            # Sleep the microsecond leftover once. Do not come back and
            # sleep_ms a remainder smaller than the 10 ms tick.
            remain = _us_diff(next_draw, _mono_us())
            if remain > 0:
                _feed_wdt()
                _brk_enter("sleep")
                try:
                    _sleep_us(remain)
                finally:
                    _brk_exit()
        now = time.ticks_ms()
        if present_t0 is None:
            present_t0 = now
        frame_started = _mono_us()
        _report_presented(now)
        button.poll(now)

        if sweep.active:
            _brk_enter("sweep")
            try:
                swept = sweep.step(now)
                if swept is not None:
                    values.update(swept)
                fault = False
            finally:
                _brk_exit()
        else:
            _brk_enter("can")
            try:
                if bus is not None:
                    if pull_frames(bus, values):
                        last_rx_ms = now
                fault = bus is None or last_rx_ms == 0 or time.ticks_diff(now, last_rx_ms) > WATCHDOG_MS
            finally:
                _brk_exit()

        _brk_enter("auto")
        try:
            start_auto, boot_sweep_armed, sweep_idle_since = auto_sweep_due(
                now,
                present_t0,
                sweep.active,
                live_can(bus, last_rx_ms, now),
                boot_sweep_armed,
                sweep_idle_since,
            )
            if start_auto:
                sweep.start(now)
                swept = sweep.step(now)
                if swept is not None:
                    values.update(swept)
                fault = False
                _brk_enter("serial")
                try:
                    print("auto-sweep")
                finally:
                    _brk_exit()
        finally:
            _brk_exit()

        if ota_client is not None and (
            last_ota_ms == 0 or time.ticks_diff(now, last_ota_ms) >= OTA_POLL_MS
        ):
            _brk_enter("ota")
            try:
                last_ota_ms = now
                _service()
                try:
                    ota_client.poll(now)
                except Exception:
                    pass
            finally:
                _brk_exit()

        if ui.sprite.pack is None and time.ticks_diff(now, _last_fetch_ms) >= PIXIDX_FETCH_EVERY_MS:
            # Keep retrying while the current pack is missing or was rejected
            # (a leftover V6 file must not block the download).
            _brk_enter("pix")
            try:
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
            finally:
                _brk_exit()

        _flow_add("loop", _us_diff(_mono_us(), loop_mark))
        ui.tick(values, fault, now)
        # The panel has this frame. One slice only if this 33 ms budget
        # still has room. The pace below sleeps what remains.
        _gc_after_present(_us_diff(_mono_us(), frame_started))
        _brk_enter("pace")
        try:
            next_draw = pace_after(frame_started, _mono_us())
        finally:
            _brk_exit()
        loop_mark = _mono_us()
        _brk_enter("serial")
        try:
            _brk_maybe_print(now)
        finally:
            _brk_exit()
        _brk_commit(_us_diff(_mono_us(), iter_us))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as e:
        try:
            from utility import print_error_msg

            print_error_msg(e)
        except ImportError:
            print("GE8 gauges stopped:", e)
