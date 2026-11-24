"""Host pixel compare for the V16 C blit against the V15 Python picture.

The shared library is the same gh_blit.c the natmod calls. LCD push, PSRAM,
and SPI stay on the device. This script checks the pixels those calls produce.
"""

import ctypes
import hashlib
import os
import subprocess
import sys

import host_compare_v14 as v14

ROOT = os.path.dirname(os.path.abspath(__file__))
LIB_PATH = os.path.join(ROOT, "native", "libgh_blit.so")


def _build_lib():
    subprocess.check_call(["make", "-C", os.path.join(ROOT, "native"), "libgh_blit.so"])


def _load_lib():
    if not os.path.exists(LIB_PATH):
        _build_lib()
    lib = ctypes.CDLL(LIB_PATH)
    u8 = ctypes.POINTER(ctypes.c_uint8)
    ci = ctypes.c_int
    lib.gh_copy_rows.argtypes = [u8, ci, ci, ci, ci, u8, ci, ci]
    lib.gh_compose_bands.argtypes = [u8, ci, ci, ci, ci, ci, ci, ci, ci, u8, ci]
    lib.gh_compose_bands.restype = None
    lib.gh_blit_stamp.argtypes = [u8, ci, ci, ci, u8, u8, ci, ci, ci, ci, ci]
    lib.gh_stamp_sprite.argtypes = [u8, ci, ci, u8, u8, ci, ci, ci, ci]
    lib.gh_expand_indexed.argtypes = [u8, ci, ci, ci, ci, u8, ci, u8]
    lib.gh_fx.argtypes = [u8, ci, ci, ci, ci, ci, ci, ci, ci, ci, ci, ci, ci]
    lib.gh_draw_hud_digits.argtypes = [u8, ci, ci, ci, ci, ci]
    lib.gh_fill_rect.argtypes = [u8, ci, ci, ci, ci, ci, ci, ctypes.c_uint16]
    lib.gh_fill_ellipse.argtypes = [u8, ci, ci, ci, ci, ci, ci, ctypes.c_uint16]
    lib.gh_fill_triangle.argtypes = [u8, ci, ci, ci, ci, ci, ci, ci, ci, ctypes.c_uint16]
    lib.gh_rgb888_to_565.argtypes = [ctypes.c_uint32]
    lib.gh_rgb888_to_565.restype = ctypes.c_uint16
    for name in (
        "gh_copy_rows",
        "gh_compose_bands",
        "gh_blit_stamp",
        "gh_stamp_sprite",
        "gh_expand_indexed",
        "gh_fx",
        "gh_draw_hud_digits",
        "gh_fill_rect",
        "gh_fill_ellipse",
        "gh_fill_triangle",
    ):
        getattr(lib, name).restype = None
    return lib


def _cbuf(buf, write):
    mv = memoryview(buf)
    if mv.itemsize != 1:
        mv = mv.cast("B")
    if write or not mv.readonly:
        try:
            return (ctypes.c_uint8 * len(mv)).from_buffer(mv)
        except TypeError:
            pass
    if write:
        raise TypeError("need a writable buffer")
    return (ctypes.c_uint8 * len(mv)).from_buffer_copy(mv)


class Native(object):
    """Same calls main.py makes into gh_render."""

    BUILD = "host-c"
    ARCH = "host"

    def __init__(self, lib):
        self.lib = lib

    def compose(self, tile, tw, th, off_c, off_w, off_g, horizon, grass, stamps, offs, window, sea_dy=0):
        vw = 128
        ct = _cbuf(tile, False)
        co = _cbuf(window, True)
        self.lib.gh_compose_bands(
            ct, int(tw), int(th),
            int(off_c), int(off_w), int(off_g),
            int(horizon), int(grass), int(sea_dy),
            co, vw,
        )
        i = 0
        while i < len(stamps):
            st = stamps[i]
            off = offs[i] if i < len(offs) else 0
            if len(st) >= 8 and st[7]:
                self._spans(
                    window, vw, th, tw, st[5], st[3], st[4], st[7], st[1], st[2], off
                )
            else:
                self._blit(window, vw, th, tw, st[5], st[6], st[3], st[4], st[1], st[2], off)
            i += 1

    def copy(self, tile, tw, th, off, window, vw):
        self._rows(tile, tw, 0, th, off, window, vw, 0)

    def stamp_runs(self, window, vw, vh, runs, nruns, pairs, ncolors, x, y):
        cw = _cbuf(window, True)
        cr = _cbuf(runs, False)
        cp = _cbuf(pairs, False)
        self.lib.gh_stamp_runs.argtypes = [
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
        ]
        self.lib.gh_stamp_runs.restype = None
        self.lib.gh_stamp_runs(
            cw, int(vw), int(vh), cr, int(nruns), cp, int(ncolors), int(x), int(y)
        )

    def stamp(self, window, vw, vh, rgb, mask, sw, sh, x, y):
        cw = _cbuf(window, True)
        cr = _cbuf(rgb, False)
        cm = _cbuf(mask, False)
        self.lib.gh_stamp_sprite(cw, int(vw), int(vh), cr, cm, int(sw), int(sh), int(x), int(y))

    def expand(self, src, stride, bpp, w, h, pairs, ncolors, dst):
        cs = _cbuf(src, False)
        cp = _cbuf(pairs, False)
        cd = _cbuf(dst, True)
        self.lib.gh_expand_indexed(
            cs, int(stride), int(bpp), int(w), int(h), cp, int(ncolors), cd
        )

    def fx(self, window, vw, vh, aura_i, sw_i, bar_i, aura_x, aura_y, sw_x,
           spark_i=-1, bar_x=None, spark_x=22, spark_y=88):
        if bar_x is None:
            bar_x = 68
        cw = _cbuf(window, True)
        self.lib.gh_fx(
            cw, int(vw), int(vh),
            int(aura_i), int(sw_i), int(bar_i), int(spark_i),
            int(aura_x), int(aura_y), int(sw_x), int(bar_x),
            int(spark_x), int(spark_y),
        )

    def digits(self, window, vw, vh, bx, by, rings):
        cw = _cbuf(window, True)
        self.lib.gh_draw_hud_digits(cw, int(vw), int(vh), int(bx), int(by), int(rings))

    def _rows(self, tile, tw, y0, y1, off, out, vw, out_y):
        ct = _cbuf(tile, False)
        co = _cbuf(out, True)
        self.lib.gh_copy_rows(ct, int(tw), int(y0), int(y1), int(off), co, int(vw), int(out_y))

    def _spans(self, window, vw, vh, tw, sbuf, sw, sh, runs, ox, oy, off):
        cw = _cbuf(window, True)
        cs = _cbuf(sbuf, False)
        cr = _cbuf(runs, False)
        self.lib.gh_blit_span_runs.argtypes = [
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        self.lib.gh_blit_span_runs.restype = None
        self.lib.gh_blit_span_runs(
            cw, int(vw), int(vh), int(tw), cs, int(sw), int(sh),
            cr, len(runs) // 4, int(ox), int(oy), int(off),
        )

    def _blit(self, window, vw, vh, tw, sbuf, mask, sw, sh, ox, oy, off):
        cw = _cbuf(window, True)
        cs = _cbuf(sbuf, False)
        cm = _cbuf(mask, False)
        self.lib.gh_blit_stamp(
            cw, int(vw), int(vh), int(tw), cs, cm,
            int(sw), int(sh), int(ox), int(oy), int(off),
        )


def _oracle(fw, sprite, amount):
    th = sprite.ocean_th
    play = bytearray(fw.W * th * 2)
    if sprite._parallax:
        v14.oracle_compose(
            fw,
            sprite.ocean_rgb565,
            sprite.ocean_tw,
            th,
            sprite.scroll,
            sprite.ocean_stamps,
            play,
            fw.W,
        )
    else:
        fw.copy_ocean_window(
            sprite.ocean_rgb565, sprite.ocean_tw, th, int(sprite.scroll), play, fw.W
        )
    sprite._stamp_playfield_sprites(amount, False, play)
    return bytes(play)


def _set_state(sprite, scroll, tag, anim_i, ring_i, sparkles, rings=None, aura=-1, sw=-1, bar=-1, spark=-1):
    sprite.scroll = float(scroll)
    sprite.anim_tag = tag
    sprite.anim_i = anim_i
    sprite.ring_i = ring_i
    sprite.sparkles = [list(item) for item in sparkles]
    if rings is not None:
        sprite.rings = rings
    sprite.boost.aura_i = aura
    sprite.boost.sw_i = sw
    sprite.boost.bar_i = bar
    sprite.boost.spark_i = spark


class BufLcd(object):
    """fillRect into LE RGB565, same clip and 888-to-565 as the C digits."""

    def __init__(self, fw, w, h):
        self.fw = fw
        self.w = w
        self.h = h
        self.buf = bytearray(w * h * 2)

    def fillRect(self, x, y, w, h, color):
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w < 0:
            x += w
            w = -w
        if h < 0:
            y += h
            h = -h
        if w < 1 or h < 1:
            return
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x >= self.w or y >= self.h or w < 1 or h < 1:
            return
        if x + w > self.w:
            w = self.w - x
        if y + h > self.h:
            h = self.h - y
        if w < 1 or h < 1:
            return
        c = self.fw._rgb888_to_565(color)
        lo = c & 0xFF
        hi = (c >> 8) & 0xFF
        row = 0
        while row < h:
            i = ((y + row) * self.w + x) * 2
            col = 0
            while col < w:
                self.buf[i] = lo
                self.buf[i + 1] = hi
                i += 2
                col += 1
            row += 1


def _py_digits(fw, vw, vh, bx, by, rings):
    lcd = BufLcd(fw, vw, vh)
    dx, dy = bx + 22, by + 4
    n = int(rings) % 1000
    fw.draw_digit(lcd, dx + 14, dy, n % 10)
    if n >= 10:
        fw.draw_digit(lcd, dx + 7, dy, (n // 10) % 10)
    if n >= 100:
        fw.draw_digit(lcd, dx, dy, (n // 100) % 10)
    return bytes(lcd.buf)


_FX_ADD = False
_FX_COVER = bytearray()
_FX_VW = 0
_FX_VH = 0
_FX_N = 0


def _add565(dst, src):
    dr = (dst >> 11) & 31
    dg = (dst >> 5) & 63
    db = dst & 31
    sr = (src >> 11) & 31
    sg = (src >> 5) & 63
    sb = src & 31
    r = dr + sr
    g = dg + sg
    b = db + sb
    if r > 31:
        r = 31
    if g > 63:
        g = 63
    if b > 31:
        b = 31
    return (r << 11) | (g << 5) | b


def _fx_begin(vw, vh):
    global _FX_ADD, _FX_COVER, _FX_VW, _FX_VH, _FX_N
    _FX_ADD = True
    _FX_VW = vw
    _FX_VH = vh
    _FX_N = (vw * vh + 7) // 8
    if len(_FX_COVER) < _FX_N:
        _FX_COVER = bytearray(_FX_N)
    else:
        _FX_COVER[:_FX_N] = b"\x00" * _FX_N


def _fx_cover_clear():
    _FX_COVER[:_FX_N] = b"\x00" * _FX_N


def _fx_claim(x, y):
    i = y * _FX_VW + x
    byte = i >> 3
    bit = 1 << (i & 7)
    if _FX_COVER[byte] & bit:
        return False
    _FX_COVER[byte] |= bit
    return True


def _store565(buf, vw, x, y, c):
    i = (y * vw + x) * 2
    if _FX_ADD:
        if not _fx_claim(x, y):
            return
        dst = buf[i] | (buf[i + 1] << 8)
        c = _add565(dst, c)
    buf[i] = c & 0xFF
    buf[i + 1] = (c >> 8) & 0xFF


def _hline(buf, vw, vh, x, y, w, c):
    if w < 0:
        x += w
        w = -w
    if w < 1 or y < 0 or y >= vh:
        return
    if x < 0:
        w += x
        x = 0
    if x >= vw or w < 1:
        return
    if x + w > vw:
        w = vw - x
    if w < 1:
        return
    k = 0
    while k < w:
        _store565(buf, vw, x + k, y, c)
        k += 1


def _fill_rect(buf, vw, vh, x, y, w, h, c):
    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h
    if w < 1 or h < 1:
        return
    if x < 0:
        w += x
        x = 0
    if y < 0:
        h += y
        y = 0
    if x >= vw or y >= vh or w < 1 or h < 1:
        return
    if x + w > vw:
        w = vw - x
    if y + h > vh:
        h = vh - y
    row = 0
    while row < h:
        _hline(buf, vw, vh, x, y + row, w, c)
        row += 1


def _vline(buf, vw, vh, x, y, h, c):
    if h < 0:
        y += h
        h = -h
    _fill_rect(buf, vw, vh, x, y, 1, h, c)


def _draw_line(buf, vw, vh, x0, y0, x1, y1, c):
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    guard = 0
    while True:
        if 0 <= x0 < vw and 0 <= y0 < vh:
            _store565(buf, vw, x0, y0, c)
        if x0 == x1 and y0 == y1:
            break
        guard += 1
        if guard > 8192:
            break
        e2 = err * 2
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _fill_ellipse(buf, vw, vh, x, y, rx, ry, c):
    if ry == 0:
        _hline(buf, vw, vh, x - rx, y, (rx * 2) + 1, c)
        return
    if rx == 0:
        _vline(buf, vw, vh, x, y - ry, (ry * 2) + 1, c)
        return
    if rx < 0 or ry < 0:
        return
    rx2 = rx * rx
    ry2 = ry * ry
    _hline(buf, vw, vh, x - rx, y, (rx * 2) + 1, c)
    i = 0
    yt = 0
    xt = rx
    s = (rx2 * 2) + ry2 * (1 - (rx * 2))
    guard = 0
    while True:
        while s < 0:
            yt += 1
            s += rx2 * ((yt * 4) + 2)
            guard += 1
            if guard > 8192:
                return
        _fill_rect(buf, vw, vh, x - xt, y - yt, (xt * 2) + 1, yt - i, c)
        _fill_rect(buf, vw, vh, x - xt, y + i + 1, (xt * 2) + 1, yt - i, c)
        i = yt
        xt -= 1
        s -= (xt * ry2) * 4
        guard += 1
        if guard > 8192:
            return
        if not (rx2 * yt <= ry2 * xt):
            break
    xt = 0
    yt = ry
    s = (ry2 * 2) + rx2 * (1 - (ry * 2))
    guard = 0
    while True:
        while s < 0:
            xt += 1
            s += ry2 * ((xt * 4) + 2)
            guard += 1
            if guard > 8192:
                return
        _hline(buf, vw, vh, x - xt, y - yt, (xt * 2) + 1, c)
        _hline(buf, vw, vh, x - xt, y + yt, (xt * 2) + 1, c)
        yt -= 1
        s -= (yt * rx2) * 4
        guard += 1
        if guard > 8192:
            return
        if not (ry2 * xt <= rx2 * yt):
            break


def _fill_triangle(buf, vw, vh, x0, y0, x1, y1, x2, y2, c):
    if y0 > y1:
        y0, y1 = y1, y0
        x0, x1 = x1, x0
    if y1 > y2:
        y1, y2 = y2, y1
        x1, x2 = x2, x1
    if y0 > y1:
        y0, y1 = y1, y0
        x0, x1 = x1, x0
    if y0 == y2:
        a = b = x0
        if x1 < a:
            a = x1
        elif x1 > b:
            b = x1
        if x2 < a:
            a = x2
        elif x2 > b:
            b = x2
        _hline(buf, vw, vh, a, y0, b - a + 1, c)
        return
    if (x1 - x0) * (y2 - y0) == (x2 - x0) * (y1 - y0):
        _draw_line(buf, vw, vh, x0, y0, x2, y2, c)
        return
    dy1 = y1 - y0
    dy2 = y2 - y0
    change = (x1 - x0) * dy2 > (x2 - x0) * dy1
    dx1 = abs(x1 - x0)
    dx2 = abs(x2 - x0)
    xstep1 = -1 if x1 < x0 else 1
    xstep2 = -1 if x2 < x0 else 1
    a = b = x0
    if change:
        dx1, dx2 = dx2, dx1
        dy1, dy2 = dy2, dy1
        xstep1, xstep2 = xstep2, xstep1
    err1 = (max(dx1, dy1) >> 1) + (min(dx1, dy1) if xstep1 < 0 else dx1)
    err2 = (max(dx2, dy2) >> 1) + (min(dx2, dy2) if xstep2 > 0 else dx2)
    guard = 0
    if y0 != y1:
        while True:
            err1 -= dx1
            while err1 < 0:
                err1 += dy1
                a += xstep1
                guard += 1
                if guard > 8192:
                    return
            err2 -= dx2
            while err2 < 0:
                err2 += dy2
                b += xstep2
                guard += 1
                if guard > 8192:
                    return
            _hline(buf, vw, vh, a, y0, b - a + 1, c)
            guard += 1
            if guard > 8192:
                return
            y0 += 1
            if not (y0 < y1):
                break
    if change:
        b = x1
        xstep2 = -1 if x2 < x1 else 1
        dx2 = abs(x2 - x1)
        dy2 = y2 - y1
        err2 = (max(dx2, dy2) >> 1) + (min(dx2, dy2) if xstep2 > 0 else dx2)
    else:
        a = x1
        dx1 = abs(x2 - x1)
        dy1 = y2 - y1
        xstep1 = -1 if x2 < x1 else 1
        err1 = (max(dx1, dy1) >> 1) + (min(dx1, dy1) if xstep1 < 0 else dx1)
    guard = 0
    while True:
        err1 -= dx1
        while err1 < 0:
            err1 += dy1
            a += xstep1
            if a == x2:
                break
            guard += 1
            if guard > 8192:
                return
        err2 -= dx2
        while err2 < 0:
            err2 += dy2
            b += xstep2
            if b == x2:
                break
            guard += 1
            if guard > 8192:
                return
        _hline(buf, vw, vh, a, y0, b - a + 1, c)
        guard += 1
        if guard > 8192:
            return
        y0 += 1
        if not (y0 <= y2):
            break


def _ellipse_clamped(buf, vw, vh, x, y, rx, ry, c):
    if rx < 1:
        rx = 1
    if ry < 1:
        ry = 1
    _fill_ellipse(buf, vw, vh, x, y, rx, ry, c)


def _chevron(buf, vw, vh, x, cy, half_h, width, c, steps):
    if half_h < 2 or width < 2:
        return
    if steps < 1:
        steps = 1
    step = (half_h * 2) // steps
    if step < 3:
        step = 3
    y = cy - half_h
    end = cy + half_h
    while y < end:
        y2 = y + step
        if y2 > end:
            y2 = end
        midy = (y + y2) // 2
        _fill_triangle(buf, vw, vh, x, y, x + width, midy, x, y2, c)
        y = y2


def _c565(fw, color):
    return fw._rgb888_to_565(color)


def _ref_aura(fw, buf, vw, vh, tip_x, tip_y, frame):
    pals = (
        (0xC8F090, 0x6ED050, 0xFFFFFF, 0xE8FFE0),
        (0x50B0FF, 0x2E78F0, 0xFFFFFF, 0xA0E0FF),
        (0x3A98FF, 0x1E58D8, 0xFFFFFF, 0x7ED0FF),
        (0x2A80F0, 0x1848C0, 0xFFFFFF, 0x6EC4FF),
    )
    slot = frame % 4
    lo, mid, hi, ice = [_c565(fw, c) for c in pals[slot]]
    scale = (0.72, 0.90, 1.0, 1.0)[slot]
    rx = int(20 * scale)
    ry = int(13 * scale)
    cx = tip_x - rx + 2
    cy = tip_y
    shift = (0, 2, 4, 1)[slot]
    back = cx - rx
    # Front to back, matching gh_blit.c so the claim mask keeps one color.
    for dy, length in ((8, 7), (3, 11), (-3, 12), (-8, 8)):
        col = lo if dy < 0 else mid
        _fill_triangle(
            buf, vw, vh, back + 2, cy + dy, back - length, cy + dy, back + 2, cy + dy + 3, col
        )
    cols = (hi, ice, mid)
    i = 2
    while i >= 0:
        _chevron(buf, vw, vh, cx - 2 + i * 5 + shift, cy, max(4, ry - 2), 5, cols[i], 4)
        i -= 1
    _ellipse_clamped(buf, vw, vh, cx + 6, cy, max(2, rx - 11), max(2, ry - 7), hi)
    _ellipse_clamped(buf, vw, vh, cx + 2, cy, max(2, rx - 5), max(2, ry - 4), lo)
    _fill_triangle(buf, vw, vh, cx + rx - 5, cy - 6, tip_x + 1, cy, cx + rx - 5, cy + 6, mid)
    _ellipse_clamped(buf, vw, vh, cx, cy, rx + 1, ry + 1, mid)


def _ref_shock(fw, buf, vw, vh, tip_x, tip_y, frame):
    if frame < 0 or frame > 4:
        return
    cols = (
        _c565(fw, 0xFFFFFF),
        _c565(fw, 0xA8E8FF),
        _c565(fw, 0x3A98FF),
    )
    marks = (
        ((0, -2, -5, -1, -1, -6, 0), (-4, -4, -8, -2, -5, -8, 1)),
        (
            (0, -3, -6, -1, -1, -8, 0),
            (-4, -5, -9, -2, -5, -9, 1),
            (3, -4, 0, -2, 4, -7, 2),
        ),
        (
            (0, -4, -7, -1, -1, -10, 0),
            (-5, -6, -10, -2, -6, -11, 1),
            (3, -5, 0, -2, 5, -9, 2),
        ),
        (
            (0, -5, -8, -1, -1, -12, 0),
            (-5, -7, -11, -2, -6, -12, 1),
            (4, -6, 1, -2, 6, -10, 2),
        ),
        (
            (-2, -4, -6, -1, -3, -8, 0),
            (3, -6, 1, -2, 5, -8, 1),
            (-8, -3, -11, -1, -9, -6, 2),
        ),
    )
    cy = tip_y + fw.BOOT_DY
    for tri in marks[frame]:
        x0, y0, x1, y1, x2, y2, ci = tri
        col = cols[ci]
        for mirror in (1, -1):
            _fill_triangle(
                buf, vw, vh,
                tip_x + x0, cy + mirror * y0,
                tip_x + x1, cy + mirror * y1,
                tip_x + x2, cy + mirror * y2,
                col,
            )


def _ref_barrier(fw, buf, vw, vh, tip_x, tip_y, frame):
    if frame < 0 or frame > 3:
        return
    cw = _c565(fw, 0xFFFFFF)
    ci = _c565(fw, 0xA8E8FF)
    cm = _c565(fw, 0x3A98FF)
    if frame <= 1:
        h = 6 if frame == 0 else 18
        x = tip_x + (2 if frame == 0 else 4)
        _chevron(buf, vw, vh, x - 4, tip_y, max(4, h - 3), 3, ci, 3)
        _chevron(buf, vw, vh, x, tip_y, h, 4, cw, 3)
    else:
        k = 1 if frame == 2 else 3
        x = tip_x + 6 + k * 8
        h = 22 - k * 4
        _fill_triangle(buf, vw, vh, x + 2, tip_y + h, x + 8 + k, tip_y + h - 2, x + 2, tip_y + h - 6, cm)
        _fill_triangle(buf, vw, vh, x + 2, tip_y - h, x + 8 + k, tip_y - h + 2, x + 2, tip_y - h + 6, cw)
        _chevron(buf, vw, vh, x, tip_y, max(4, h), 3, ci, 3)


def _ref_spark(fw, buf, vw, vh, sx, sy, frame):
    if frame < 0:
        return
    shards = (
        ((-1, -1, 5, 0xFFFFFF), (-4, 3, 4, 0xA8E8FF), (-7, -4, 3, 0xFFFFFF), (-3, 6, 3, 0xA8E8FF)),
        ((-2, 1, 6, 0xA8E8FF), (-5, -3, 4, 0xFFFFFF), (-8, 4, 3, 0xA8E8FF), (-3, -6, 3, 0xFFFFFF)),
        ((-1, 2, 5, 0xFFFFFF), (-6, -1, 5, 0xA8E8FF), (-4, 5, 3, 0xFFFFFF), (-9, -3, 3, 0xA8E8FF)),
        ((-2, -2, 4, 0xA8E8FF), (-5, 2, 6, 0xFFFFFF), (-8, -5, 3, 0xA8E8FF), (-3, 5, 4, 0xFFFFFF)),
    )
    for dx, dy, length, col in shards[frame % 4]:
        c = _c565(fw, col)
        _fill_triangle(
            buf, vw, vh,
            sx + dx, sy + dy,
            sx + dx - length, sy + dy - 1,
            sx + dx - 2, sy + dy + 2,
            c,
        )


def _ref_fx(fw, buf, vw, vh, aura_i, sw_i, bar_i, aura_x, aura_y, sw_x,
            spark_i=-1, bar_x=None, spark_x=None, spark_y=None):
    global _FX_ADD
    if bar_x is None:
        bar_x = fw.BAR_TIP_X
    if spark_x is None:
        spark_x = fw.SPARK_FRONT_X
    if spark_y is None:
        spark_y = fw.SPARK_MID_Y
    _fx_begin(vw, vh)
    try:
        if aura_i >= 0:
            _fx_cover_clear()
            _ref_aura(fw, buf, vw, vh, aura_x, aura_y, aura_i)
        if sw_i >= 0:
            _fx_cover_clear()
            _ref_shock(fw, buf, vw, vh, sw_x, aura_y, sw_i)
        if bar_i >= 0:
            _fx_cover_clear()
            _ref_barrier(fw, buf, vw, vh, bar_x, aura_y, bar_i)
        if spark_i >= 0:
            _fx_cover_clear()
            _ref_spark(fw, buf, vw, vh, spark_x, spark_y, spark_i)
    finally:
        _FX_ADD = False


def _pix(buf, vw, x, y):
    i = (y * vw + x) * 2
    return buf[i] | (buf[i + 1] << 8)


def _lum565(c):
    r = (c >> 11) & 31
    g = (c >> 5) & 63
    b = c & 31
    r8 = (r << 3) | (r >> 2)
    g8 = (g << 2) | (g >> 4)
    b8 = (b << 3) | (b >> 2)
    return (299 * r8 + 587 * g8 + 114 * b8) // 1000


def _fill565(buf, vw, vh, c):
    lo = c & 0xFF
    hi = (c >> 8) & 0xFF
    i = 0
    n = vw * vh
    while i < n:
        o = i * 2
        buf[o] = lo
        buf[o + 1] = hi
        i += 1


def _additive_probe(fw, sprite):
    """Numbers for the saturating add, plus a check that FX sits on Sonic."""
    vw, vh = 128, 103
    bad = 0
    grass = fw._rgb888_to_565(fw.COL_GRASS)
    base = bytearray(vw * vh * 2)
    _fill565(base, vw, vh, grass)
    got = bytearray(base)
    fw._GH.fx(got, vw, vh, 0, -1, -1, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X)
    before_l = _lum565(grass)
    lifted = 0
    clipped_g = 0
    sample = None
    peak = before_l
    y = 0
    while y < vh:
        x = 0
        while x < vw:
            c = _pix(got, vw, x, y)
            if c != grass:
                lifted += 1
                lum = _lum565(c)
                if lum > peak:
                    peak = lum
                if ((c >> 5) & 63) == 63:
                    clipped_g += 1
                if sample is None:
                    sample = (x, y, c, lum)
            x += 1
        y += 1
    print(
        "additive grass L", before_l, "peak", peak, "sample", sample,
        "lifted", lifted, "green_clip", clipped_g,
    )
    if sample is None or peak <= before_l or clipped_g < 1:
        print("additive grass lift failed")
        bad += 1
    if _pix(got, vw, 0, 0) != grass:
        print("additive touched the corner")
        bad += 1

    dark = (2 << 11) | (4 << 5) | 2
    one_mid = _add565(dark, fw._rgb888_to_565(0x1E58D8))
    one_lo = _add565(dark, fw._rgb888_to_565(0x3A98FF))
    stacked = _add565(one_mid, fw._rgb888_to_565(0x3A98FF))
    field = bytearray(vw * vh * 2)
    _fill565(field, vw, vh, dark)
    got = bytearray(field)
    fw._GH.fx(got, vw, vh, 2, -1, -1, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X)
    n_mid = 0
    n_lo = 0
    n_stack = 0
    y = 0
    while y < vh:
        x = 0
        while x < vw:
            c = _pix(got, vw, x, y)
            if c == one_mid:
                n_mid += 1
            if c == one_lo:
                n_lo += 1
            if stacked != one_lo and stacked != one_mid and c == stacked:
                n_stack += 1
            if c != dark and _lum565(c) < _lum565(dark):
                print("additive darkened", x, y, hex(c))
                bad += 1
                break
            x += 1
        else:
            y += 1
            continue
        break
    print(
        "additive once mid", n_mid, "lo", n_lo, "stacked", n_stack,
        "lo", hex(one_lo), "stacked_color", hex(stacked),
    )
    if n_mid < 1 or n_lo < 1 or n_stack != 0:
        print("additive mask failed")
        bad += 1

    got = bytearray(vw * vh * 2)
    _fill565(got, vw, vh, 0xFFFF)
    fw._GH.fx(
        got, vw, vh, 0, 4, 3, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
        0, fw.BAR_TIP_X, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
    )
    miss = 0
    i = 0
    while i < len(got):
        if got[i] != 0xFF or got[i + 1] != 0xFF:
            miss += 1
        i += 2
    print("saturate nonwhite", miss)
    if miss:
        bad += 1

    got = bytearray(vw * vh * 2)
    _fill565(got, vw, vh, grass)
    fw._GH.fx(
        got, vw, vh, -1, -1, -1, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
        0, fw.BAR_TIP_X, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
    )
    spark_n = 0
    y = 0
    while y < vh:
        x = 0
        while x < vw:
            if _pix(got, vw, x, y) != grass:
                spark_n += 1
                if x > 40:
                    print("spark reached", x, y)
                    bad += 1
            x += 1
        y += 1
    print("spark pixels", spark_n, "front", fw.SPARK_FRONT_X, fw.SPARK_MID_Y)
    if spark_n < 1:
        bad += 1

    def _min_x(tip):
        buf = bytearray(vw * vh * 2)
        _fill565(buf, vw, vh, dark)
        fw._GH.fx(
            buf, vw, vh, -1, -1, 0, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
            -1, tip, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
        )
        x0 = vw
        yy = 0
        while yy < vh:
            xx = 0
            while xx < vw:
                if _pix(buf, vw, xx, yy) != dark:
                    if xx < x0:
                        x0 = xx
                    break
                xx += 1
            yy += 1
        return x0

    left = _min_x(fw.BAR_TIP_X)
    right = _min_x(fw.SW_TIP_X)
    print("barrier min x", left, "at", fw.BAR_TIP_X, "vs", right, "at", fw.SW_TIP_X)
    if right - left != fw.SW_TIP_X - fw.BAR_TIP_X:
        print("barrier nudge mismatch")
        bad += 1

    _set_state(sprite, 64, "run_fast", 0, 0, [], rings=0, aura=2)
    sprite._paint_native(0.0, False)
    under = bytearray(sprite.play_window)
    sprite._paint_native(0.0, True)
    over = sprite.play_window
    brighter = 0
    y = fw.SONIC_Y
    while y < fw.SONIC_Y + fw.SONIC_H:
        x = fw.SONIC_X
        while x < fw.SONIC_X + fw.SONIC_W:
            a = _pix(under, vw, x, y)
            b = _pix(over, vw, x, y)
            if b != a:
                # Packed sheet sprites replace pixels. The procedural aura only adds.
                if not sprite._packed_fx_on() and _lum565(b) < _lum565(a):
                    print("sonic darkened", x, y)
                    bad += 1
                    break
                brighter += 1
            x += 1
        else:
            y += 1
            continue
        break
    print("sonic box brightened", brighter)
    if brighter < 1:
        bad += 1
    return bad


def _row_word(buf, vw, y):
    i = y * vw * 2
    return buf[i] | (buf[i + 1] << 8)


def _sea_shift_probe(lib):
    """Sea band moves up by sea_dy. sea_dy 0 keeps each row on itself."""
    tw, th = 16, 40
    horizon, grass = 8, 24
    vw = 8
    tile = bytearray(tw * th * 2)
    y = 0
    while y < th:
        c = y + 1
        x = 0
        while x < tw:
            i = (y * tw + x) * 2
            tile[i] = c & 0xFF
            tile[i + 1] = (c >> 8) & 0xFF
            x += 1
        y += 1

    def bands(sea_dy):
        out = bytearray(vw * th * 2)
        lib.gh_compose_bands(
            _cbuf(tile, False), tw, th, 0, 0, 0, horizon, grass, sea_dy,
            _cbuf(out, True), vw,
        )
        return out

    base = bands(0)
    up = bands(1)
    bad = 0
    y = 0
    while y < th:
        if _row_word(base, vw, y) != y + 1:
            bad += 1
            break
        y += 1
    if _row_word(up, vw, 0) != 1:
        bad += 1
    if _row_word(up, vw, horizon - 1) != horizon + 1:
        bad += 1
    if _row_word(up, vw, horizon) != horizon + 2:
        bad += 1
    if _row_word(up, vw, grass) != grass + 1:
        bad += 1
    print(
        "sea shift", "ok" if not bad else "bad",
        "raised seam", _row_word(up, vw, horizon - 1),
    )
    return bad


def main():
    _build_lib()
    lib = _load_lib()
    fw = v14._load_fw()
    assert fw.APP_VERSION == 16
    assert fw.APP_VERSION_NAME == "2026.11.21-v16"
    assert fw.FACE_ENABLED is False
    assert fw.REFRESH_MS == 50
    assert fw.FRAME_MIN_MS == 16
    assert fw.FRAME_MIN_MS < fw.REFRESH_MS
    raw = open(os.path.join(ROOT, "pixidx.bin"), "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == fw.PIXIDX_SHA256
    assert len(raw) == fw.PIXIDX_SIZE
    # int() of the aura scales is baked into the C tables.
    assert int(20 * 0.72) == 14 and int(13 * 0.72) == 9
    assert int(20 * 0.90) == 18 and int(13 * 0.90) == 11
    assert lib.gh_rgb888_to_565(0xF8F8F8) == fw._rgb888_to_565(0xF8F8F8)
    assert lib.gh_rgb888_to_565(0x101018) == fw._rgb888_to_565(0x101018)

    fw._GH = Native(lib)
    fw._GH_KIND = "native"
    pack = fw.PixIdx(raw)
    sprite = fw.Sprite(pack)
    if fw._GH is None:
        print("native self-test rejected the host library")
        return 1
    assert sprite.play_window is not None
    assert sprite._parallax
    print("native", fw._GH.BUILD, "play", len(sprite.play_window), "stamps", len(sprite.ocean_stamps))

    bad = 0
    frames = 0
    runs = pack.cycles["run_fast"]
    n_run = len(runs)

    def step(scroll, tag, anim_i, ring_i, amount, sparkles, label, rings=0, aura=-1, swi=-1, bar=-1, fx=False):
        nonlocal frames, bad
        _set_state(sprite, scroll, tag, anim_i, ring_i, sparkles, rings, aura, swi, bar)
        expect = _oracle(fw, sprite, amount)
        sprite._paint_native(amount, False)
        n, first = v14.mismatches(sprite.play_window, expect, fw.W)
        if n:
            print("playfield", label, "mismatches", n, first, "scroll", scroll, "tag", tag)
            bad += n
        frames += 1
        if fx:
            base = bytearray(expect)
            vh = sprite._playfield_vh()
            if sprite._packed_fx_on():
                sprite._stamp_packed_fx(base, fw.W, vh)
            else:
                _ref_fx(fw, base, fw.W, vh, aura, swi, bar, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X)
            # Digits are part of the native fx paint. Reference draws them too.
            ref_digits = bytearray(_py_digits(fw, fw.W, vh, fw.HUD_BAR_POS[0], fw.HUD_BAR_POS[1], rings))
            # Composite digits onto the fx reference the same way: digits after fx.
            # _py_digits starts from empty. Replay digits onto base with the real drawer.
            lcd = BufLcd(fw, fw.W, vh)
            lcd.buf[:] = base
            dx, dy = fw.HUD_BAR_POS[0] + 22, fw.HUD_BAR_POS[1] + 4
            nring = int(rings) % 1000
            fw.draw_digit(lcd, dx + 14, dy, nring % 10)
            if nring >= 10:
                fw.draw_digit(lcd, dx + 7, dy, (nring // 10) % 10)
            if nring >= 100:
                fw.draw_digit(lcd, dx, dy, (nring // 100) % 10)
            sprite._paint_native(amount, True)
            nf, firstf = v14.mismatches(sprite.play_window, lcd.buf, fw.W)
            if nf:
                print("fx", label, "mismatches", nf, firstf, "aura", aura, "sw", swi, "bar", bar)
                bad += nf
            frames += 1
            del ref_digits

    step(0, "idle", 0, 0, 0.0, [], "boot")
    step(0, "idle", 0, 0, 0.0, [], "parked")
    scroll = 0
    for i in range(1, 97):
        scroll = i
        step(scroll, "run_fast", i % n_run, i % 8, 0.0, [], "cruise")
    for scroll in range(96, -1, -1):
        step(scroll, "run_medium", scroll % n_run, (scroll * 3) % 8, 0.0, [], "back")
    for scroll in (0, 200, 383, 384, 385, 700, 1000, 1535, 10, 10, 11, 90):
        step(scroll, "run_slow", 1, 4, 0.0, [], "jump")
    step(90, "run_medium", 1, 4, 0.0, [(40, 70, 0, 0)], "spark-on")
    step(91, "run_medium", 2, 5, 0.0, [(40, 70, 1, 0), (20, 78, 0, 0)], "spark-two")
    step(92, "run_medium", 2, 5, 0.88, [(40, 70, 2, 0)], "heat")
    step(92, "run_medium", 2, 5, 0.4, [(40, 70, 2, 0)], "heat-change")
    step(92, "run_medium", 2, 5, 0.0, [], "spark-off")
    step(92, "idle", 0, 0, 0.0, [], "idle-again")
    step(10.2, "run_fast", 0, 1, 0.0, [], "frac-a")
    step(10.8, "run_fast", 0, 1, 0.0, [], "frac-hold")
    step(11.2, "run_fast", 1, 1, 0.0, [], "frac-step")
    step(40.0, "run_fast", 0, 2, 0.0, [], "anim-a")
    step(40.0, "run_fast", 1, 2, 0.0, [], "anim-b")
    step(40.0, "run_fast", 1, 3, 0.0, [], "ring-only")
    for amount in (0.0, 0.25, 0.5, 0.88, 1.0):
        step(120.5, "run_fast", 2, 6, amount, [(10, 60, 1, 0)], "tint")
    for scroll in (0.0, 17.4, 88.0, 200.2, 1535.9):
        step(scroll, "run_fast", 3, 7, 0.0, [], "wide", rings=42)
    # Boost does not change the playfield under it. A few frames also check the overlay.
    for aura, swi, bar in (
        (0, 0, -1),
        (1, 2, -1),
        (2, 4, -1),
        (3, -1, -1),
        (-1, 4, -1),
        (-1, -1, 0),
        (-1, -1, 3),
        (-1, -1, 5),
        (2, 1, 2),
    ):
        step(
            64, "run_fast", aura % n_run, 3, 0.0, [(30, 74, 0, 0)],
            "boost", rings=128, aura=aura, swi=swi, bar=bar, fx=True,
        )

    # Ring counter, independent of the backdrop: Python draw_digit vs C.
    digit_bad = 0
    for rings in (0, 5, 9, 10, 42, 99, 100, 256, 999):
        vw, vh = 128, 103
        got = bytearray(vw * vh * 2)
        fw._GH.digits(got, vw, vh, fw.HUD_BAR_POS[0], fw.HUD_BAR_POS[1], rings)
        exp = _py_digits(fw, vw, vh, fw.HUD_BAR_POS[0], fw.HUD_BAR_POS[1], rings)
        n, first = v14.mismatches(got, exp, vw)
        if n:
            print("digits", rings, n, first)
            digit_bad += n
    bad += digit_bad

    # Isolated boost overlays on a patterned buffer, every frame index.
    fx_bad = 0
    vw, vh = 128, 103
    for aura in range(-1, 4):
        for swi in (-1, 0, 1, 2, 3, 4):
            for bar in (-1, 0, 1, 2, 3, 4, 5):
                if aura < 0 and swi < 0 and bar < 0:
                    continue
                base = bytearray(vw * vh * 2)
                i = 0
                while i < len(base):
                    base[i] = (i * 13) & 0xFF
                    base[i + 1] = (i * 7) & 0xFF
                    i += 2
                got = bytearray(base)
                exp = bytearray(base)
                fw._GH.fx(got, vw, vh, aura, swi, bar, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X)
                _ref_fx(fw, exp, vw, vh, aura, swi, bar, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X)
                n, first = v14.mismatches(got, exp, vw)
                if n:
                    print("overlay", aura, swi, bar, n, first)
                    fx_bad += n
                    if fx_bad > 8:
                        break
            if fx_bad > 8:
                break
        if fx_bad > 8:
            break
    bad += fx_bad

    for spark in range(4):
        base = bytearray(vw * vh * 2)
        i = 0
        while i < len(base):
            base[i] = (i * 13) & 0xFF
            base[i + 1] = (i * 7) & 0xFF
            i += 2
        got = bytearray(base)
        exp = bytearray(base)
        fw._GH.fx(
            got, vw, vh, -1, -1, -1, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
            spark, fw.BAR_TIP_X, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
        )
        _ref_fx(
            fw, exp, vw, vh, -1, -1, -1, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
            spark, fw.BAR_TIP_X, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
        )
        n, first = v14.mismatches(got, exp, vw)
        if n:
            print("spark", spark, n, first)
            bad += n
    got = bytearray(vw * vh * 2)
    exp = bytearray(vw * vh * 2)
    i = 0
    while i < len(got):
        got[i] = exp[i] = 0x20
        got[i + 1] = exp[i + 1] = 0x42
        i += 2
    fw._GH.fx(
        got, vw, vh, 1, 2, 2, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
        3, fw.BAR_TIP_X, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
    )
    _ref_fx(
        fw, exp, vw, vh, 1, 2, 2, fw.AURA_TIP_X, fw.AURA_TIP_Y, fw.SW_TIP_X,
        3, fw.BAR_TIP_X, fw.SPARK_FRONT_X, fw.SPARK_MID_Y,
    )
    n, first = v14.mismatches(got, exp, vw)
    if n:
        print("combined", n, first)
        bad += n

    bad += _additive_probe(fw, sprite)
    bad += _sea_shift_probe(lib)
    tw = sprite.ocean_tw
    tile = sprite.ocean_rgb565
    # The checker row under the dark strip repeats every 16px.
    period = 16
    src = (fw.GRASS_Y + fw.BLADE_ROWS + 8) * tw * 2
    tmpl = bytes(tile[src:src + period * 2])
    x = 0
    dirt_bad = 0
    while x < tw:
        if bytes(tile[src + x * 2:src + (x + period) * 2]) != tmpl:
            dirt_bad += 1
        x += period
    if bytes(tile[src + (tw - period) * 2:src + tw * 2]) != tmpl:
        dirt_bad += 1
    print("dirt period breaks", dirt_bad)
    bad += dirt_bad

    print("frames", frames, "digit_bad", digit_bad, "fx_bad", fx_bad, "mismatches", bad)
    if frames < 222:
        print("coverage", frames, "is under 222")
        return 1
    if bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
