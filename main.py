"""GE8 Fit F-CAN gauges + faithful Green Hill dashboard for M5Stack Atom S3R.

Build 2026.11.21-c5 (OTA version 4). Same firmware as c4, plus a
centered OTA mark so a phone-Wi-Fi check can confirm this build
is on the Atom. Playfield is a tilemap plus
sprite frames loaded from gfx.bin, so the source stays small enough
to compile on the Atom. Same art as c1. Boost aura on top of Sonic,
redraw/downscale of the locked after preview art (original generated
still + preview composites). Boost aura is drawn on TOP of Sonic at
about 70% opacity, with the pointy tip leading in front of him.
Chao hover at Sonic's head height. Rings spawn in groups of 3-8 with
wide gaps between groups.

Do not change CAN / gauge layout, OTA client, or secrets.
"""

import math
import time

# CPython preview/test compatibility (MicroPython already has these).
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.time() * 1000)
    time.ticks_diff = lambda a, b: a - b

import M5
from M5 import Lcd

# Graphics live in gfx.bin (zlib pack). main.py stays small.


import gc

GFX_PATHS = ("gfx.bin", "/flash/gfx.bin", "/gfx.bin")
TILE_W = 8
TILE_H = 8
LAST_ROW_H = 7
MAP_W = 48
MAP_H = 13
TILE_BYTES = TILE_W * TILE_H * 2
BG_W, BG_H = 384, 103
SONIC_X, SONIC_Y = 16, 55
SONIC_W, SONIC_H = 39, 36
SONIC_HEAD_Y = 63
SONIC_MID_Y = 72
SONIC_FRONT_X = 54
SONIC_FEET_Y = 90
HUD_RING_POS = (4, 3)
HUD_BAR_POS = (14, 6)

SPRITES = {}
TILESET = b""
TILEMAP = b""
GFX_LOADED = False


def _u16le(buf, i):
    return buf[i] | (buf[i + 1] << 8)


def _put16le(buf, i, v):
    buf[i] = v & 0xFF
    buf[i + 1] = (v >> 8) & 0xFF


def _read_u16(buf, i):
    return buf[i] | (buf[i + 1] << 8), i + 2


def _read_u32(buf, i):
    return buf[i] | (buf[i + 1] << 8) | (buf[i + 2] << 16) | (buf[i + 3] << 24), i + 4


def load_gfx(path=None):
    """Decompress gfx.bin into TILESET, TILEMAP, SPRITES. Safe to call once."""
    global TILESET, TILEMAP, SPRITES, TILE_W, TILE_H, LAST_ROW_H, MAP_W, MAP_H
    global TILE_BYTES, BG_W, BG_H, GFX_LOADED
    if GFX_LOADED:
        return
    paths = (path,) if path else GFX_PATHS
    data = None
    used = None
    for p in paths:
        try:
            with open(p, "rb") as f:
                data = f.read()
            used = p
            break
        except OSError:
            continue
    if data is None:
        raise OSError("gfx.bin missing")
    if data[:4] != b"ATMX":
        raise ValueError("bad gfx magic")
    ver = data[4] | (data[5] << 8)
    raw_len = data[6] | (data[7] << 8) | (data[8] << 16) | (data[9] << 24)
    raw = data[10:]
    if ver != 1 or len(raw) != raw_len:
        raise ValueError("bad gfx header")
    i = 0
    TILE_W, i = _read_u16(raw, i)
    TILE_H, i = _read_u16(raw, i)
    LAST_ROW_H, i = _read_u16(raw, i)
    MAP_W, i = _read_u16(raw, i)
    MAP_H, i = _read_u16(raw, i)
    tlen, i = _read_u32(raw, i)
    mlen, i = _read_u32(raw, i)
    TILE_BYTES = TILE_W * TILE_H * 2
    BG_W = MAP_W * TILE_W
    BG_H = (MAP_H - 1) * TILE_H + LAST_ROW_H
    TILESET = raw[i:i + tlen]
    i += tlen
    TILEMAP = raw[i:i + mlen]
    i += mlen
    nspr, i = _read_u16(raw, i)
    SPRITES.clear()
    for _ in range(nspr):
        nlen = raw[i]
        i += 1
        name = bytes(raw[i:i + nlen]).decode("ascii")
        i += nlen
        w, i = _read_u16(raw, i)
        h, i = _read_u16(raw, i)
        rlen, i = _read_u32(raw, i)
        alen, i = _read_u32(raw, i)
        rgb = raw[i:i + rlen]
        i += rlen
        a = raw[i:i + alen]
        i += alen
        SPRITES[name] = {"w": w, "h": h, "rgb": rgb, "a": a}
    GFX_LOADED = True
    del raw
    gc.collect()
    try:
        print("GFX", used, "sprites", nspr, "free", gc.mem_free())
    except Exception:
        pass


def blit_tile(fb, tileset, tile_id, dx, dy, src_x=0, src_y=0, w=None, h=None):
    tw, th_store = TILE_W, TILE_H
    if w is None:
        w = tw - src_x
    if h is None:
        h = th_store - src_y
    if tile_id < 0:
        return
    base = int(tile_id) * TILE_BYTES
    x0, y0, sx, sy = dx, dy, src_x, src_y
    if x0 < 0:
        sx += -x0
        w -= -x0
        x0 = 0
    if y0 < 0:
        sy += -y0
        h -= -y0
        y0 = 0
    if x0 + w > W:
        w = W - x0
    if y0 + h > GAUGE_Y:
        h = GAUGE_Y - y0
    if w <= 0 or h <= 0:
        return
    for j in range(h):
        src = base + ((sy + j) * tw + sx) * 2
        dst = ((y0 + j) * W + x0) * 2
        fb[dst:dst + w * 2] = tileset[src:src + w * 2]


def blit_tilemap(fb, tileset, tilemap, scroll):
    period = MAP_W * TILE_W
    off = int(scroll) % period
    if off < 0:
        off += period
    x = 0
    while x < W:
        wx = (off + x) % period
        col = wx // TILE_W
        sx = wx % TILE_W
        span = TILE_W - sx
        if span > W - x:
            span = W - x
        for row in range(MAP_H):
            y = row * TILE_H
            if y >= GAUGE_Y:
                break
            th = LAST_ROW_H if row == MAP_H - 1 else TILE_H
            if y + th > GAUGE_Y:
                th = GAUGE_Y - y
            mi = (row * MAP_W + col) * 2
            tid = tilemap[mi] | (tilemap[mi + 1] << 8)
            blit_tile(fb, tileset, tid, x, y, src_x=sx, src_y=0, w=span, h=th)
        x += span


def show_boot_error(lcd, msg):
    """Paint a visible error so a failed start is never a black screen."""
    try:
        if lcd is None:
            return
        if hasattr(lcd, "fillScreen"):
            lcd.fillScreen(0xB00000)
        elif hasattr(lcd, "fill"):
            lcd.fill(0xB00000)
        elif hasattr(lcd, "fillRect"):
            lcd.fillRect(0, 0, 128, 128, 0xB00000)
        text = str(msg)[:80]
        if hasattr(lcd, "setCursor"):
            try:
                lcd.setCursor(4, 8)
            except Exception:
                pass
        for fn in ("print", "drawString", "text"):
            if hasattr(lcd, fn):
                try:
                    getattr(lcd, fn)(text, 4, 8)
                    break
                except TypeError:
                    try:
                        getattr(lcd, fn)(text)
                        break
                    except Exception:
                        pass
                except Exception:
                    pass
        if hasattr(lcd, "push"):
            try:
                lcd.push(0, 0)
            except Exception:
                pass
    except Exception:
        pass
    try:
        print("BOOT_ERR", msg)
    except Exception:
        pass

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
APP_VERSION = 4
APP_VERSION_NAME = "2026.11.21-c5"
BREATH_MS = 1600
BOOST_SPEED = 100.0
BOOST_END_MS = 2000
FX_FRAME_MS = 1000 // 15
CHAO_FRAME_MS = 500
CHAO_FOLLOW_X = 10
AURA_ALPHA = 178  # ~70% of 255
AURA_LEAD = 12    # tip sits this many px ahead of Sonic's front
FACE_ENABLED = False

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
WIFI_DOT_X, WIFI_DOT_Y = 124, 3

COL_TRACK = 0x2A2A2A
COL_ORANGE = 0xFF8C00
COL_ERR = 0xFF1744
COL_GAUGE_BG = 0x101012
COL_HEAT = 0xFF1A1A
COL_HUD_DIGIT = 0xF8F8F8
COL_HUD_OUTLINE = 0x101018

GAUGES = (
    ("rpm", RPM_MIN, RPM_MAX,     COL0_X, ROW0_Y, BAR_W, BAR_H, "value"),
    ("spd", SPEED_MIN, SPEED_MAX, COL1_X, ROW0_Y, BAR_W, BAR_H, "orange"),
    ("tps", TPS_MIN, TPS_MAX,     COL0_X, ROW1_Y, BAR_W, BAR_H, "value"),
    ("clt", CLT_MIN, CLT_MAX,     COL1_X, ROW1_Y, BAR_W, BAR_H, "value"),
)

# Backdrop wrap (384). Ring groups live in a much longer world period
# so a normal drive does not pile up an enormous ring count.
PERIOD = BG_W
RING_PERIOD = 4000
# Packs of 3-8, wide gaps between groups. (world_x, count, spacing, y)
RING_PACKS = (
    (62, 5, 10, 77),
    (62 + 2200, 4, 12, 76),
)

CHAO_KINDS = ("normal", "amy", "knuckles", "sonic", "tails")

# 5x7 digits, bits left-to-right. Drawn with a 1px dark outline.
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


def overheat_t(clt, now_ms):
    """0 if cool; 0.42-0.88 sine breath when coolant >= 90."""
    if clt is None or clt < SWEAT_CLT:
        return 0.0
    phase = ((now_ms % BREATH_MS) / float(BREATH_MS)) * 6.28318530718
    breath = 0.5 + 0.5 * math.sin(phase)
    return 0.22 + 0.34 * breath


def sonic_body_color(clt, now_ms):
    """Kept for tests; tint is applied during the blit."""
    t = overheat_t(clt, now_ms)
    if t <= 0:
        return 0x1A8EF0
    return mix_rgb(0x1A8EF0, COL_HEAT, t)


def mouth_mode(speed, scroll, period=None, hold_speed=INF_SPEED):
    """closed | open | locked_open. Parked never inherits leftover scroll."""
    if period is None:
        period = PERIOD
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


def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def c24_to_565(c):
    return rgb565((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def unpack565(v):
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _u16le(buf, i):
    return buf[i] | (buf[i + 1] << 8)


def _put16le(buf, i, v):
    buf[i] = v & 0xFF
    buf[i + 1] = (v >> 8) & 0xFF


def make_fb():
    return bytearray(W * H * 2)


def fb_fill(fb, color, x=0, y=0, w=W, h=H):
    v = c24_to_565(color)
    lo, hi = v & 0xFF, (v >> 8) & 0xFF
    x0 = 0 if x < 0 else x
    y0 = 0 if y < 0 else y
    x1 = W if x + w > W else x + w
    y1 = H if y + h > H else y + h
    for yy in range(y0, y1):
        row = yy * W * 2 + x0 * 2
        for xx in range(x1 - x0):
            fb[row] = lo
            fb[row + 1] = hi
            row += 2


def blit_spr(fb, spr, x, y, alpha=255, tint=0):
    """Alpha-blit a packed sprite. tint is 0..256 mix toward red (overheat)."""
    sw, sh = spr["w"], spr["h"]
    rgb, am = spr["rgb"], spr["a"]
    x = int(x)
    y = int(y)
    for j in range(sh):
        dy = y + j
        if dy < 0 or dy >= H:
            continue
        src_row = j * sw
        dst_row = dy * W
        for i in range(sw):
            a = am[src_row + i]
            if a == 0:
                continue
            if alpha < 255:
                a = (a * alpha) >> 8
            if a == 0:
                continue
            dx = x + i
            if dx < 0 or dx >= W:
                continue
            v = _u16le(rgb, (src_row + i) * 2)
            sr, sg, sb = unpack565(v)
            if tint and sb > sr + 6:
                # mix bluish body/quills toward red; leave gloves, shoes, muzzle
                sr = sr + ((255 - sr) * tint >> 8)
                sg = sg - (sg * tint >> 8)
                sb = sb - (sb * tint >> 8)
            if a < 255:
                di = (dst_row + dx) * 2
                dr, dg, db = unpack565(_u16le(fb, di))
                inv = 255 - a
                sr = (sr * a + dr * inv) // 255
                sg = (sg * a + dg * inv) // 255
                sb = (sb * a + db * inv) // 255
            _put16le(fb, (dst_row + dx) * 2, rgb565(sr, sg, sb))


def blit_spr_tip(fb, spr, tip_x, tip_y, alpha=255):
    """Place a sprite so its rightmost opaque pixel (near mid-height) sits at tip."""
    sw, sh = spr["w"], spr["h"]
    am = spr["a"]
    ys_min, ys_max, tip = sh, -1, sw - 1
    mid_lo, mid_hi = sh // 2 - 3, sh // 2 + 3
    found = False
    for j in range(sh):
        row = j * sw
        for i in range(sw):
            if am[row + i] > 20:
                if j < ys_min:
                    ys_min = j
                if j > ys_max:
                    ys_max = j
                if mid_lo <= j <= mid_hi:
                    if i > tip or not found:
                        tip = i
                        found = True
    if ys_max < ys_min:
        return
    mid = (ys_min + ys_max) // 2
    if not found:
        tip = sw - 1
    blit_spr(fb, spr, int(tip_x) - tip, int(tip_y) - mid, alpha=alpha)


def fb_fill_circle(fb, cx, cy, r, color):
    rr = r * r
    for y in range(-r, r + 1):
        for x in range(-r, r + 1):
            if x * x + y * y <= rr:
                px, py = cx + x, cy + y
                if 0 <= px < W and 0 <= py < H:
                    _put16le(fb, (py * W + px) * 2, c24_to_565(color))


# Flattened ring list, rebuilt once.
_RING_ITEMS = None


def ring_items():
    global _RING_ITEMS
    if _RING_ITEMS is None:
        items = []
        for x0, n, sp, y in RING_PACKS:
            for i in range(n):
                items.append((x0 + i * sp, y))
        _RING_ITEMS = items
    return _RING_ITEMS


def draw_glyph_fb(fb, x, y, bits, color, outline, s=1):
    # outline first
    for row in range(7):
        rowb = bits[row]
        for col in range(5):
            if rowb & (0b10000 >> col):
                for oy in (-1, 0, 1):
                    for ox in (-1, 0, 1):
                        if ox == 0 and oy == 0:
                            continue
                        fb_fill(fb, outline, x + col * s + ox, y + row * s + oy, s, s)
    for row in range(7):
        rowb = bits[row]
        for col in range(5):
            if rowb & (0b10000 >> col):
                fb_fill(fb, color, x + col * s, y + row * s, s, s)


def draw_digit_fb(fb, x, y, n, color, outline, s=1):
    draw_glyph_fb(fb, x, y, DIGITS[int(n) % 10], color, outline, s)


# 5x7 letters for the c5 OTA check mark. O reuses digit 0.
LETTERS = {
    "O": DIGITS[0],
    "T": (0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100),
    "A": (0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
}


def draw_ota_mark(fb):
    s = 3
    gap = 2
    gw = 5 * s
    gh = 7 * s
    word = "OTA"
    tw = len(word) * gw + (len(word) - 1) * gap
    x = (W - tw) // 2
    y = (H - gh) // 2
    for ch in word:
        draw_glyph_fb(fb, x, y, LETTERS[ch], COL_HUD_DIGIT, COL_HUD_OUTLINE, s)
        x += gw + gap


class Sweep:
    """Speed 0 → 120 → 80 → 0, with matching RPM / throttle / coolant."""

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


class BoostFX:
    """Boost aura / shockwave / sound-barrier state machine, 15 fps."""

    def __init__(self):
        self.initiated = False
        self.phase = "idle"
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
                    self.aura_i = -1
                    self.sw_i = 0
                    self.bar_i = -1
            elif self.phase == "idle":
                self.below_ms = 0

        if self.phase == "idle":
            return

        self.accum += dt_ms
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
            if self.ticks == 1:
                self.aura_i = 1
            elif self.ticks >= 2:
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


class ChaoBuddy:
    """Occasional Chao that follows at cruise below 80 km/h, hovering at head height."""

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
        # always hover at head height, with a 2px bob on alternate frames
        self.y = float(SONIC_HEAD_Y)
        if self.state == "idle":
            if self.cooldown > 0:
                self.cooldown -= dt_ms
            elif cruise and self.cooldown <= 0:
                self.kind = self._rand(5)
                self.state = "join"
                self.x = -16.0
                self.y = float(SONIC_HEAD_Y)
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

    def draw(self, fb):
        if self.state == "idle":
            return
        y = int(self.y)
        if self.frame:
            y -= 2
        name = CHAO_KINDS[self.kind % 5]
        key = "chao_%s_%d" % (name, self.frame & 1)
        spr = SPRITES[key]
        # center the sprite on the hover point
        blit_spr(fb, spr, int(self.x) - spr["w"] // 2, y - spr["h"] // 2)


class Sprite:
    def __init__(self):
        self.scroll = 0.0
        self.phase = 0.0
        self.boost = BoostFX()
        self.chao = ChaoBuddy()
        self.rings = 0
        self._ring_seen = 0

    def advance(self, speed, dt_ms):
        if speed is None:
            speed = 0.0
        # World scroll is continuous in RING_PERIOD; backdrop wraps at PERIOD.
        self.scroll = (self.scroll + speed * dt_ms * 0.012) % float(RING_PERIOD)
        if speed < 1.0:
            self.phase = 0.0
        else:
            self.phase = (self.phase + dt_ms * (0.002 + 0.00035 * speed)) % 4.0
        self.boost.tick(speed, dt_ms)
        self.chao.tick(speed, dt_ms)
        self._collect_rings()

    def _collect_rings(self):
        off = int(self.scroll)
        mask = 0
        mid_x = (SONIC_X + SONIC_FRONT_X) // 2
        for i, (rx, ry) in enumerate(ring_items()):
            x = (rx - off) % RING_PERIOD
            if x <= mid_x:
                mask |= 1 << i
        gained = mask & ~self._ring_seen
        if gained:
            n = 0
            v = gained
            while v:
                n += v & 1
                v >>= 1
            self.rings = (self.rings + n) % 1000
        self._ring_seen = mask

    def _draw_rings(self, fb):
        spr = SPRITES["ring"]
        off = int(self.scroll)
        rw, rh = spr["w"], spr["h"]
        for rx, ry in ring_items():
            x = (rx - off) % RING_PERIOD
            # also draw a wrapped copy if it sits near the left edge
            for cand in (x, x - RING_PERIOD):
                if cand < W and cand + rw > 0:
                    blit_spr(fb, spr, cand - rw // 2, ry - rh // 2)

    def _draw_ring_hud(self, fb):
        bar = SPRITES.get("hud_bar")
        ring = SPRITES.get("hud_ring")
        bx, by = HUD_BAR_POS
        if bar is not None:
            blit_spr(fb, bar, bx, by)
        if ring is not None:
            blit_spr(fb, ring, HUD_RING_POS[0], HUD_RING_POS[1])
        # digits on the blue well of the bar (right half)
        n = int(self.rings)
        if bar is not None:
            dx = bx + 22
            dy = by + 4
        else:
            dx, dy = 28, 8
        draw_digit_fb(fb, dx, dy, (n // 100) % 10, COL_HUD_DIGIT, COL_HUD_OUTLINE, 1)
        draw_digit_fb(fb, dx + 7, dy, (n // 10) % 10, COL_HUD_DIGIT, COL_HUD_OUTLINE, 1)
        draw_digit_fb(fb, dx + 14, dy, n % 10, COL_HUD_DIGIT, COL_HUD_OUTLINE, 1)

    def _draw_gauges(self, fb, values):
        fb_fill(fb, COL_GAUGE_BG, 0, GAUGE_Y, W, GAUGE_H)
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
            fb_fill(fb, COL_TRACK, x, y, w, h)
            if fill_w > 0:
                color = COL_ORANGE if mode == "orange" else value_rgb(frac)
                fb_fill(fb, color, x, y, fill_w, h)

    def _draw_sonic(self, fb, speed, clt, now_ms):
        mode = mouth_mode(speed, self.scroll % PERIOD, PERIOD)
        if mode == "locked_open":
            key = "sonic_locked"
        elif mode == "open":
            key = "sonic_open"
        else:
            key = "sonic_closed"
        spr = SPRITES[key]
        # Subtle run bob so the single source pose still reads as a cycle.
        bob = 0
        if speed >= 1.0:
            bob = (0, 1, 0, -1)[int(self.phase) % 4]
        tint = int(overheat_t(clt, now_ms) * 256)
        blit_spr(fb, spr, SONIC_X, SONIC_Y + bob, tint=tint)

    def render(self, fb, values, now=0, fault=False, no_wifi=False):
        speed = values.get("spd") or 0.0
        clt = values.get("clt") or 0.0
        blit_tilemap(fb, TILESET, TILEMAP, self.scroll)
        self._draw_rings(fb)
        self.chao.draw(fb)
        self._draw_sonic(fb, speed, clt, now)
        # Aura ON TOP of Sonic, ~70% opacity, tip leading in front.
        tip_x = SONIC_FRONT_X + AURA_LEAD
        tip_y = SONIC_MID_Y
        if self.boost.aura_i >= 0:
            spr = SPRITES["aura_%d" % (self.boost.aura_i % 4)]
            blit_spr_tip(fb, spr, tip_x, tip_y, alpha=AURA_ALPHA)
        if self.boost.sw_i >= 0:
            spr = SPRITES["sw_%d" % (self.boost.sw_i % 5)]
            blit_spr_tip(fb, spr, tip_x, tip_y, alpha=255)
        if self.boost.bar_i >= 0:
            spr = SPRITES["bar_%d" % (self.boost.bar_i % 6)]
            blit_spr_tip(fb, spr, tip_x, tip_y, alpha=255)
        self._draw_ring_hud(fb)
        self._draw_gauges(fb, values)
        if fault:
            fb_fill_circle(fb, DOT_X, DOT_Y, DOT_R, COL_ERR)
        if no_wifi:
            fb_fill_circle(fb, WIFI_DOT_X, WIFI_DOT_Y, DOT_R, COL_ERR)
        draw_ota_mark(fb)




_DEPTH_LINES = []


def _depth_log(msg):
    global _DEPTH_LINES
    try:
        print("PRESENT_DEPTH", msg)
    except Exception:
        pass
    _DEPTH_LINES.append(str(msg))
    try:
        with open("present_bpp.txt", "w") as f:
            f.write("\n".join(_DEPTH_LINES) + "\n")
    except Exception as e:
        try:
            print("PRESENT_DEPTH write:", e)
        except Exception:
            pass


def _depth_info(obj, label):
    parts = [label]
    try:
        parts.append(type(obj).__name__)
    except Exception:
        pass
    for name in ("getColorDepth", "colorDepth", "getDepth", "bpp", "depth"):
        if hasattr(obj, name):
            attr = getattr(obj, name)
            try:
                val = attr() if callable(attr) else attr
            except Exception as e:
                val = "ERR:%s" % e
            parts.append("%s=%s" % (name, val))
    try:
        extra = [
            n
            for n in dir(obj)
            if ("depth" in n.lower() or n.lower() in ("bpp", "colorformat", "format"))
        ]
        if extra:
            parts.append("attrs=" + ",".join(extra))
    except Exception:
        pass
    return " ".join(parts)


def prepare_lcd_depth(lcd):
    """Set 16-bit depth on the Lcd itself, then log whatever depth API exists."""
    try:
        lcd.setColorDepth(16)
        _depth_log("lcd.setColorDepth(16)=ok")
    except Exception as e:
        _depth_log("lcd.setColorDepth(16)=ERR:%s" % e)
    _depth_log(_depth_info(lcd, "lcd"))


def _test_pattern_be():
    """High-byte-first RGB565: four horizontal bands, red / green / blue / white."""
    colors = (0xF800, 0x07E0, 0x001F, 0xFFFF)
    buf = bytearray(W * H * 2)
    i = 0
    band = H // 4
    for y in range(H):
        idx = y // band
        if idx > 3:
            idx = 3
        c = colors[idx]
        hi = (c >> 8) & 0xFF
        lo = c & 0xFF
        x = 0
        while x < W:
            buf[i] = hi
            buf[i + 1] = lo
            i += 2
            x += 1
    return buf


def present(lcd, fb):
    """Push the RGB565 framebuffer via newCanvas + drawRawBuf + push().

    Lcd.setColorDepth(16) is applied in main() before the canvas is created.
    First 20 seconds push a known red/green/blue/white band pattern so a
    16-bit path is visually obvious; then the real framebuffer is used.
    Depth is printed and written to present_bpp.txt.
    """
    global _PRESENT_CANVAS, _PRESENT_BE, _PRESENT_T0, _PATTERN_BE, _CANVAS_LOGGED, _PIX_LOGGED
    try:
        _PRESENT_CANVAS
    except NameError:
        _PRESENT_CANVAS = None
    try:
        _PRESENT_BE
    except NameError:
        _PRESENT_BE = None
    try:
        _PRESENT_T0
    except NameError:
        _PRESENT_T0 = None
    try:
        _PATTERN_BE
    except NameError:
        _PATTERN_BE = None
    try:
        _CANVAS_LOGGED
    except NameError:
        _CANVAS_LOGGED = False
    try:
        _PIX_LOGGED
    except NameError:
        _PIX_LOGGED = False
    if _PRESENT_CANVAS is None:
        try:
            _PRESENT_CANVAS = Lcd.newCanvas(w=W, h=H, bpp=16, psram=True)
        except Exception:
            try:
                _PRESENT_CANVAS = Lcd.newCanvas(W, H, 16, True)
            except Exception as e:
                try:
                    print("present canvas:", e)
                except Exception:
                    pass
                _PRESENT_CANVAS = None
        if _PRESENT_CANVAS is not None and hasattr(_PRESENT_CANVAS, "setColorDepth"):
            try:
                _PRESENT_CANVAS.setColorDepth(16)
                _depth_log("canvas.setColorDepth(16)=ok")
            except Exception as e:
                _depth_log("canvas.setColorDepth(16)=ERR:%s" % e)
    if _PRESENT_CANVAS is not None and not _CANVAS_LOGGED:
        _CANVAS_LOGGED = True
        _depth_log(_depth_info(_PRESENT_CANVAS, "canvas"))
    if _PRESENT_T0 is None:
        _PRESENT_T0 = time.ticks_ms()
        _PATTERN_BE = _test_pattern_be()
        _depth_log("pattern=red/green/blue/white bands 20s")
    c = _PRESENT_CANVAS if _PRESENT_CANVAS is not None else lcd
    use_pattern = False  # c3: skip 20s bands so the dashboard comes up immediately
    if use_pattern:
        src = _PATTERN_BE
        n = len(src)
    else:
        n = len(fb)
        if _PRESENT_BE is None or len(_PRESENT_BE) != n:
            _PRESENT_BE = bytearray(n)
        be = _PRESENT_BE
        i = 0
        while i < n:
            be[i] = fb[i + 1]
            be[i + 1] = fb[i]
            i += 2
        src = be
    # drawRawBuf's 6th arg is a count of 16-bit pixels, not bytes.
    # Passing the byte length (n) makes each pixel two screen dots.
    pix = n // 2
    if not _PIX_LOGGED:
        _PIX_LOGGED = True
        _depth_log("drawRawBuf pix=%d (was bytes %d)" % (pix, n))
    try:
        c.drawRawBuf(src, 0, 0, W, H, pix, swap=False)
    except TypeError:
        try:
            c.drawRawBuf(src, 0, 0, W, H, pix)
        except Exception as e:
            try:
                print("present drawRawBuf:", e)
            except Exception:
                pass
            return False
    except Exception as e:
        try:
            print("present drawRawBuf:", e)
        except Exception:
            pass
        return False
    try:
        c.push(0, 0)
        return True
    except Exception as e:
        try:
            print("present push:", e)
        except Exception:
            pass
        return False


def make_canvas(lcd):
    for kwargs in (
        {"w": W, "h": H, "bpp": 16, "psram": True},
        {"w": W, "h": H, "bpp": 16},
    ):
        try:
            canvas = lcd.newCanvas(**kwargs)
            if canvas is not None:
                return canvas
        except Exception:
            pass
    try:
        canvas = lcd.newCanvas(W, H, 16, True)
        if canvas is not None:
            return canvas
    except Exception:
        pass
    return lcd


def find_btns():
    """Face button under the screen only. Never bind Touch."""
    found = []
    ids = set()
    for name in ("BtnA", "Btn", "BtnPWR"):
        btn = getattr(M5, name, None)
        if btn is None:
            continue
        ident = id(btn)
        if ident in ids:
            continue
        ids.add(ident)
        found.append(btn)
    return found


def _btn_clicked(btn):
    """Edge click only. Do not call wasDoubleClicked — it can swallow the press."""
    if hasattr(btn, "wasClicked"):
        try:
            return bool(btn.wasClicked())
        except Exception:
            return False
    if hasattr(btn, "isPressed") or hasattr(btn, "wasPressed"):
        # last-resort level read is handled with edge state in main()
        return False
    return False


_CRASH_DUMPED = 0


def _crash_dump(where, e):
    """Print the exception once to serial and crash.txt, then keep going."""
    global _CRASH_DUMPED
    try:
        import sys
        print("CRASH", where)
        sys.print_exception(e)
    except Exception:
        try:
            print("CRASH", where, e)
        except Exception:
            pass
    if _CRASH_DUMPED:
        return
    _CRASH_DUMPED = 1
    try:
        import sys
        with open("crash.txt", "w") as f:
            f.write("%s\n" % where)
            try:
                sys.print_exception(e, f)
            except TypeError:
                f.write(str(e))
                f.write("\n")
    except Exception:
        pass


class Dashboard:
    def __init__(self, lcd):
        self.lcd = lcd
        self.fb = make_fb()
        self.sprite = Sprite()
        self._last_ms = time.ticks_ms()

    def tick(self, values, fault, now, no_wifi=False):
        dt = time.ticks_diff(now, self._last_ms)
        if dt < 0 or dt > 500:
            dt = REFRESH_MS
        self._last_ms = now
        speed = values.get("spd") or 0.0
        try:
            self.sprite.advance(speed, dt)
            self.sprite.render(self.fb, values, now, fault, no_wifi)
        except Exception as e:
            _crash_dump("tick/render", e)
            return
        present(self.lcd, self.fb)


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
        if msg[0] == ID_RPM and data is not None and len(data) >= 5:
            values["brk"] = 1.0 if (data[4] & 0x01) else 0.0
        n += 1
        if n >= 64:
            break
    return n


def main():
    M5.begin()
    lcd = Lcd
    try:
        prepare_lcd_depth(lcd)
    except Exception as e:
        try:
            print("PRESENT_DEPTH prepare:", e)
        except Exception:
            pass
    try:
        load_gfx()
    except Exception as e:
        show_boot_error(lcd, e)
        raise
    ui = Dashboard(lcd)
    try:
        ui.tick({"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0, "brk": 0.0}, False, time.ticks_ms(), True)
        with open("boot_ok.txt", "w") as _f:
            _f.write("ok")
        print("DASHBOARD_OK", gc.mem_free())
        print("BUILD", APP_VERSION_NAME)
    except Exception as e:
        show_boot_error(lcd, e)
        raise
    sweep = Sweep()
    btns = find_btns()
    try:
        print("FIND_BTNS", len(btns))
    except Exception:
        pass
    btn_down = {}
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
        clicked = False
        for btn in btns:
            if _btn_clicked(btn):
                clicked = True
                break
            # Fallback edge detect if wasClicked is missing
            pressed = False
            if not hasattr(btn, "wasClicked"):
                try:
                    if hasattr(btn, "isPressed"):
                        pressed = bool(btn.isPressed())
                    elif hasattr(btn, "wasPressed"):
                        pressed = bool(btn.wasPressed())
                except Exception:
                    pressed = False
                last = btn_down.get(id(btn), False)
                btn_down[id(btn)] = pressed
                if pressed and not last:
                    clicked = True
                    break
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

        no_wifi = False
        if ota_client is not None:
            try:
                ota_client.poll(now)
            except Exception:
                pass
            try:
                no_wifi = not ota_client.wifi_connected()
            except Exception:
                no_wifi = True

        if time.ticks_diff(now, last_draw_ms) >= REFRESH_MS:
            ui.tick(values, fault, now, no_wifi)
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
