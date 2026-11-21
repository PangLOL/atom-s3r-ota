#include "gh_blit.h"

#include <string.h>

/* Division stays in these helpers so the natmod does not need libgcc
   for the few FX quotients. Scroll offsets arrive already reduced. */

static int iabs(int v) {
    return v < 0 ? -v : v;
}

static int imax(int a, int b) {
    return a > b ? a : b;
}

static int imin(int a, int b) {
    return a < b ? a : b;
}

static int floor_div2(int v) {
    if (v >= 0) {
        return v >> 1;
    }
    return -(((-v) + 1) >> 1);
}

static int udiv_pos(int a, int b) {
    int q = 0;
    if (b <= 0 || a < 0) {
        return 0;
    }
    while (a >= b) {
        a -= b;
        q++;
    }
    return q;
}

static int norm_off(int off, int tw) {
    if (tw <= 0) {
        return 0;
    }
    if (off >= 0 && off < tw) {
        return off;
    }
    if (off >= 0) {
        while (off >= tw) {
            off -= tw;
        }
        return off;
    }
    while (off < 0) {
        off += tw;
    }
    return off;
}

static int mod_pos(int n, int m) {
    if (m <= 0) {
        return 0;
    }
    if (n >= 0 && n < m) {
        return n;
    }
    if (n < 0) {
        while (n < 0) {
            n += m;
        }
        return n;
    }
    while (n >= m) {
        n -= m;
    }
    return n;
}

uint16_t gh_rgb888_to_565(uint32_t c) {
    uint32_t r = (c >> 16) & 0xFFu;
    uint32_t g = (c >> 8) & 0xFFu;
    uint32_t b = c & 0xFFu;
    return (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
}

static void put565(uint8_t *p, uint16_t c) {
    p[0] = (uint8_t)(c & 0xFFu);
    p[1] = (uint8_t)(c >> 8);
}

static void hline(uint8_t *buf, int vw, int vh, int x, int y, int w, uint16_t c) {
    int i;
    uint8_t *p;
    if (w < 0) {
        x += w;
        w = -w;
    }
    if (w < 1 || y < 0 || y >= vh) {
        return;
    }
    if (x < 0) {
        w += x;
        x = 0;
    }
    if (x >= vw || w < 1) {
        return;
    }
    if (x + w > vw) {
        w = vw - x;
    }
    if (w < 1) {
        return;
    }
    p = buf + ((y * vw + x) * 2);
    for (i = 0; i < w; i++) {
        put565(p, c);
        p += 2;
    }
}

void gh_fill_rect(uint8_t *buf, int vw, int vh, int x, int y, int w, int h, uint16_t c) {
    int row;
    if (w < 0) {
        x += w;
        w = -w;
    }
    if (h < 0) {
        y += h;
        h = -h;
    }
    if (w < 1 || h < 1) {
        return;
    }
    if (x < 0) {
        w += x;
        x = 0;
    }
    if (y < 0) {
        h += y;
        y = 0;
    }
    if (x >= vw || y >= vh || w < 1 || h < 1) {
        return;
    }
    if (x + w > vw) {
        w = vw - x;
    }
    if (y + h > vh) {
        h = vh - y;
    }
    if (w < 1 || h < 1) {
        return;
    }
    for (row = 0; row < h; row++) {
        hline(buf, vw, vh, x, y + row, w, c);
    }
}

static void vline(uint8_t *buf, int vw, int vh, int x, int y, int h, uint16_t c) {
    if (h < 0) {
        y += h;
        h = -h;
    }
    gh_fill_rect(buf, vw, vh, x, y, 1, h, c);
}

static void draw_line(uint8_t *buf, int vw, int vh, int x0, int y0, int x1, int y1, uint16_t c) {
    int dx = iabs(x1 - x0);
    int sx = x0 < x1 ? 1 : -1;
    int dy = -iabs(y1 - y0);
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    int guard = 0;
    for (;;) {
        if ((unsigned)x0 < (unsigned)vw && (unsigned)y0 < (unsigned)vh) {
            put565(buf + ((y0 * vw + x0) * 2), c);
        }
        if (x0 == x1 && y0 == y1) {
            break;
        }
        if (++guard > 8192) {
            break;
        }
        {
            int e2 = err * 2;
            if (e2 >= dy) {
                err += dy;
                x0 += sx;
            }
            if (e2 <= dx) {
                err += dx;
                y0 += sy;
            }
        }
    }
}

/* LovyanGFX LGFXBase::fillEllipse, clipped to the buffer. */
void gh_fill_ellipse(uint8_t *buf, int vw, int vh, int x, int y, int rx, int ry, uint16_t c) {
    int32_t xt, yt, i, s;
    int32_t rx2, ry2;
    int guard;
    if (ry == 0) {
        hline(buf, vw, vh, x - rx, y, (rx * 2) + 1, c);
        return;
    }
    if (rx == 0) {
        vline(buf, vw, vh, x, y - ry, (ry * 2) + 1, c);
        return;
    }
    if (rx < 0 || ry < 0) {
        return;
    }
    rx2 = (int32_t)rx * (int32_t)rx;
    ry2 = (int32_t)ry * (int32_t)ry;
    hline(buf, vw, vh, x - rx, y, (rx * 2) + 1, c);
    i = 0;
    yt = 0;
    xt = rx;
    s = (rx2 * 2) + ry2 * (1 - (rx * 2));
    guard = 0;
    do {
        while (s < 0) {
            yt++;
            s += rx2 * ((yt * 4) + 2);
            if (++guard > 8192) {
                return;
            }
        }
        gh_fill_rect(buf, vw, vh, (int)(x - xt), (int)(y - yt), (int)((xt * 2) + 1), (int)(yt - i), c);
        gh_fill_rect(buf, vw, vh, (int)(x - xt), (int)(y + i + 1), (int)((xt * 2) + 1), (int)(yt - i), c);
        i = yt;
        xt--;
        s -= (xt * ry2) * 4;
        if (++guard > 8192) {
            return;
        }
    } while (rx2 * yt <= ry2 * xt);

    xt = 0;
    yt = ry;
    s = (ry2 * 2) + rx2 * (1 - (ry * 2));
    guard = 0;
    do {
        while (s < 0) {
            xt++;
            s += ry2 * ((xt * 4) + 2);
            if (++guard > 8192) {
                return;
            }
        }
        hline(buf, vw, vh, (int)(x - xt), (int)(y - yt), (int)((xt * 2) + 1), c);
        hline(buf, vw, vh, (int)(x - xt), (int)(y + yt), (int)((xt * 2) + 1), c);
        yt--;
        s -= (yt * rx2) * 4;
        if (++guard > 8192) {
            return;
        }
    } while (ry2 * xt <= rx2 * yt);
}

/* LovyanGFX LGFXBase::fillTriangle. Collinear input becomes a 1 px line. */
void gh_fill_triangle(uint8_t *buf, int vw, int vh,
                      int x0, int y0, int x1, int y1, int x2, int y2,
                      uint16_t c) {
    int a, b;
    int dy1, dy2, dx1, dx2, xstep1, xstep2, change;
    int err1, err2;
    int guard;
    int tmp;
    if (y0 > y1) {
        tmp = y0; y0 = y1; y1 = tmp;
        tmp = x0; x0 = x1; x1 = tmp;
    }
    if (y1 > y2) {
        tmp = y2; y2 = y1; y1 = tmp;
        tmp = x2; x2 = x1; x1 = tmp;
    }
    if (y0 > y1) {
        tmp = y0; y0 = y1; y1 = tmp;
        tmp = x0; x0 = x1; x1 = tmp;
    }
    if (y0 == y2) {
        a = x0;
        b = x0;
        if (x1 < a) {
            a = x1;
        } else if (x1 > b) {
            b = x1;
        }
        if (x2 < a) {
            a = x2;
        } else if (x2 > b) {
            b = x2;
        }
        hline(buf, vw, vh, a, y0, b - a + 1, c);
        return;
    }
    if ((x1 - x0) * (y2 - y0) == (x2 - x0) * (y1 - y0)) {
        draw_line(buf, vw, vh, x0, y0, x2, y2, c);
        return;
    }
    dy1 = y1 - y0;
    dy2 = y2 - y0;
    change = ((x1 - x0) * dy2 > (x2 - x0) * dy1);
    dx1 = iabs(x1 - x0);
    dx2 = iabs(x2 - x0);
    xstep1 = x1 < x0 ? -1 : 1;
    xstep2 = x2 < x0 ? -1 : 1;
    a = x0;
    b = x0;
    if (change) {
        tmp = dx1; dx1 = dx2; dx2 = tmp;
        tmp = dy1; dy1 = dy2; dy2 = tmp;
        tmp = xstep1; xstep1 = xstep2; xstep2 = tmp;
    }
    err1 = (imax(dx1, dy1) >> 1) + (xstep1 < 0 ? imin(dx1, dy1) : dx1);
    err2 = (imax(dx2, dy2) >> 1) + (xstep2 > 0 ? imin(dx2, dy2) : dx2);
    guard = 0;
    if (y0 != y1) {
        do {
            err1 -= dx1;
            while (err1 < 0) {
                err1 += dy1;
                a += xstep1;
                if (++guard > 8192) {
                    return;
                }
            }
            err2 -= dx2;
            while (err2 < 0) {
                err2 += dy2;
                b += xstep2;
                if (++guard > 8192) {
                    return;
                }
            }
            hline(buf, vw, vh, a, y0, b - a + 1, c);
            if (++guard > 8192) {
                return;
            }
        } while (++y0 < y1);
    }
    if (change) {
        b = x1;
        xstep2 = x2 < x1 ? -1 : 1;
        dx2 = iabs(x2 - x1);
        dy2 = y2 - y1;
        err2 = (imax(dx2, dy2) >> 1) + (xstep2 > 0 ? imin(dx2, dy2) : dx2);
    } else {
        a = x1;
        dx1 = iabs(x2 - x1);
        dy1 = y2 - y1;
        xstep1 = x2 < x1 ? -1 : 1;
        err1 = (imax(dx1, dy1) >> 1) + (xstep1 < 0 ? imin(dx1, dy1) : dx1);
    }
    guard = 0;
    do {
        err1 -= dx1;
        while (err1 < 0) {
            err1 += dy1;
            a += xstep1;
            if (a == x2) {
                break;
            }
            if (++guard > 8192) {
                return;
            }
        }
        err2 -= dx2;
        while (err2 < 0) {
            err2 += dy2;
            b += xstep2;
            if (b == x2) {
                break;
            }
            if (++guard > 8192) {
                return;
            }
        }
        hline(buf, vw, vh, a, y0, b - a + 1, c);
        if (++guard > 8192) {
            return;
        }
    } while (++y0 <= y2);
}

void gh_copy_rows(const uint8_t *tile, int tw,
                  int y0, int y1, int off,
                  uint8_t *out, int vw, int out_y) {
    int row;
    int n1;
    int end1;
    int rest;
    int width_y;
    if (tile == 0 || out == 0 || tw <= 0 || vw <= 0 || y1 <= y0) {
        return;
    }
    off = norm_off(off, tw);
    width_y = y1 - y0;
    n1 = tw - off;
    if (n1 > vw) {
        n1 = vw;
    }
    end1 = n1 * 2;
    rest = (vw - n1) * 2;
    /* One wrapped copy from the start of the row. The panel is narrower
       than the tile, so this stays inside that row. */
    if (rest > tw * 2) {
        rest = tw * 2;
    }
    for (row = 0; row < width_y; row++) {
        int src = (y0 + row) * tw * 2;
        int dst = (out_y + row) * vw * 2;
        memcpy(out + dst, tile + src + off * 2, (size_t)end1);
        if (rest) {
            memcpy(out + dst + end1, tile + src, (size_t)rest);
        }
    }
}

void gh_blit_stamp(uint8_t *window, int vw, int vh, int tw,
                   const uint8_t *sbuf, const uint8_t *mask,
                   int sw, int sh, int ox, int oy, int off) {
    int sx;
    int k;
    if (window == 0 || sbuf == 0 || mask == 0 || sw <= 0 || sh <= 0 || vw <= 0 || tw <= 0) {
        return;
    }
    sx = ox - off;
    for (k = 0; k < 3; k++) {
        int cand;
        int src_x0;
        int dst_x0;
        int src_x1;
        int dy;
        if (k == 0) {
            cand = sx;
        } else if (k == 1) {
            cand = sx - tw;
        } else {
            cand = sx + tw;
        }
        if (cand + sw <= 0 || cand >= vw) {
            continue;
        }
        src_x0 = 0;
        dst_x0 = cand;
        if (dst_x0 < 0) {
            src_x0 = -dst_x0;
            dst_x0 = 0;
        }
        src_x1 = sw;
        if (cand + sw > vw) {
            src_x1 = vw - cand;
        }
        if (src_x1 <= src_x0) {
            continue;
        }
        for (dy = 0; dy < sh; dy++) {
            int wy = oy + dy;
            int row_i;
            int dst_row;
            int dx;
            if (wy < 0 || wy >= vh) {
                continue;
            }
            row_i = dy * sw;
            dst_row = wy * vw;
            for (dx = src_x0; dx < src_x1; dx++) {
                int di = row_i + dx;
                if (mask[di >> 3] & (uint8_t)(1u << (di & 7))) {
                    int wx = dst_x0 + (dx - src_x0);
                    int si = di * 2;
                    int diw = (dst_row + wx) * 2;
                    window[diw] = sbuf[si];
                    window[diw + 1] = sbuf[si + 1];
                }
            }
        }
    }
}

void gh_stamp_sprite(uint8_t *window, int vw, int vh,
                     const uint8_t *rgb, const uint8_t *mask,
                     int sw, int sh, int dest_x, int dest_y) {
    int y0, y1, x0, x1, lim, y;
    if (window == 0 || rgb == 0 || mask == 0) {
        return;
    }
    if (sw <= 0 || sh <= 0 || vw <= 0 || vh <= 0) {
        return;
    }
    if (dest_y >= 0) {
        y0 = 0;
    } else {
        y0 = 0 - dest_y;
    }
    y1 = sh;
    lim = vh - dest_y;
    if (y1 > lim) {
        y1 = lim;
    }
    if (y0 >= y1) {
        return;
    }
    if (dest_x >= 0) {
        x0 = 0;
    } else {
        x0 = 0 - dest_x;
    }
    x1 = sw;
    lim = vw - dest_x;
    if (x1 > lim) {
        x1 = lim;
    }
    if (x0 >= x1) {
        return;
    }
    for (y = y0; y < y1; y++) {
        int wy = dest_y + y;
        int sy = y * sw;
        int row_base = wy * vw;
        int x;
        for (x = x0; x < x1; x++) {
            int di = sy + x;
            if (mask[di >> 3] & (uint8_t)(1u << (di & 7))) {
                int si = di * 2;
                int wi = (row_base + dest_x + x) * 2;
                window[wi] = rgb[si];
                window[wi + 1] = rgb[si + 1];
            }
        }
    }
}

void gh_expand_indexed(const uint8_t *src, int stride, int bpp,
                       int w, int h,
                       const uint8_t *pairs, int ncolors,
                       uint8_t *dst) {
    int y, x, di;
    if (src == 0 || pairs == 0 || dst == 0 || w <= 0 || h <= 0) {
        return;
    }
    di = 0;
    for (y = 0; y < h; y++) {
        const uint8_t *row = src + y * stride;
        for (x = 0; x < w; x++) {
            int idx;
            if (bpp == 4) {
                uint8_t b = row[x >> 1];
                if ((x & 1) == 0) {
                    idx = b >> 4;
                } else {
                    idx = b & 0x0F;
                }
            } else {
                idx = row[x];
            }
            if (idx <= 0 || idx >= ncolors) {
                dst[di] = 0;
                dst[di + 1] = 0;
            } else {
                dst[di] = pairs[idx * 2];
                dst[di + 1] = pairs[idx * 2 + 1];
            }
            di += 2;
        }
    }
}

static const uint8_t GH_DIGITS[10][7] = {
    {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
    {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},
    {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F},
    {0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E},
    {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02},
    {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E},
    {0x0E, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x0E},
    {0x1F, 0x01, 0x02, 0x04, 0x04, 0x04, 0x04},
    {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E},
    {0x0E, 0x11, 0x11, 0x11, 0x0F, 0x01, 0x0E},
};

void gh_draw_digit(uint8_t *buf, int vw, int vh,
                   int x, int y, int n,
                   uint16_t color, uint16_t outline, int scale) {
    int row, col;
    const uint8_t *bits;
    if (buf == 0) {
        return;
    }
    if (scale < 1) {
        scale = 1;
    }
    n = mod_pos(n, 10);
    bits = GH_DIGITS[n];
    for (row = 0; row < 7; row++) {
        uint8_t rowb = bits[row];
        for (col = 0; col < 5; col++) {
            if (rowb & (uint8_t)(0x10u >> col)) {
                int oy, ox;
                for (oy = -1; oy <= 1; oy++) {
                    for (ox = -1; ox <= 1; ox++) {
                        if (ox == 0 && oy == 0) {
                            continue;
                        }
                        gh_fill_rect(buf, vw, vh,
                                     x + col * scale + ox,
                                     y + row * scale + oy,
                                     scale, scale, outline);
                    }
                }
            }
        }
    }
    for (row = 0; row < 7; row++) {
        uint8_t rowb = bits[row];
        for (col = 0; col < 5; col++) {
            if (rowb & (uint8_t)(0x10u >> col)) {
                gh_fill_rect(buf, vw, vh,
                             x + col * scale,
                             y + row * scale,
                             scale, scale, color);
            }
        }
    }
}

void gh_draw_hud_digits(uint8_t *buf, int vw, int vh, int bx, int by, int rings) {
    uint16_t color = gh_rgb888_to_565(0xF8F8F8u);
    uint16_t outline = gh_rgb888_to_565(0x101018u);
    int n = mod_pos(rings, 1000);
    int dx = bx + 22;
    int dy = by + 4;
    int tens;
    int hundreds;
    gh_draw_digit(buf, vw, vh, dx + 14, dy, n, color, outline, 1);
    if (n < 10) {
        return;
    }
    tens = udiv_pos(n, 10);
    gh_draw_digit(buf, vw, vh, dx + 7, dy, tens, color, outline, 1);
    if (n < 100) {
        return;
    }
    hundreds = udiv_pos(n, 100);
    gh_draw_digit(buf, vw, vh, dx, dy, hundreds, color, outline, 1);
}

static void chevron(uint8_t *buf, int vw, int vh,
                    int x, int cy, int half_h, int width, uint16_t c, int steps) {
    int step, y, end;
    if (half_h < 2 || width < 2) {
        return;
    }
    if (steps < 1) {
        steps = 1;
    }
    step = udiv_pos(half_h * 2, steps);
    if (step < 3) {
        step = 3;
    }
    y = cy - half_h;
    end = cy + half_h;
    while (y < end) {
        int y2 = y + step;
        int midy;
        if (y2 > end) {
            y2 = end;
        }
        midy = floor_div2(y + y2);
        gh_fill_triangle(buf, vw, vh, x, y, x + width, midy, x, y2, c);
        y = y2;
    }
}

static void fill_ellipse_clamped(uint8_t *buf, int vw, int vh,
                                 int x, int y, int rx, int ry, uint16_t c) {
    if (rx < 1) {
        rx = 1;
    }
    if (ry < 1) {
        ry = 1;
    }
    gh_fill_ellipse(buf, vw, vh, x, y, rx, ry, c);
}

static const uint32_t AURA_PAL[4][4] = {
    {0xC8F090u, 0x6ED050u, 0xFFFFFFu, 0xE8FFE0u},
    {0x50B0FFu, 0x2E78F0u, 0xFFFFFFu, 0xA0E0FFu},
    {0x3A98FFu, 0x1E58D8u, 0xFFFFFFu, 0x7ED0FFu},
    {0x2A80F0u, 0x1848C0u, 0xFFFFFFu, 0x6EC4FFu},
};

/* int(20 * scale), int(13 * scale) for (0.72, 0.90, 1, 1). */
static const int AURA_RX[4] = {14, 18, 20, 20};
static const int AURA_RY[4] = {9, 11, 13, 13};
static const int AURA_SHIFT[4] = {0, 2, 4, 1};

static const uint32_t COL_AURA_W = 0xFFFFFFu;
static const uint32_t COL_AURA_I = 0xA8E8FFu;
static const uint32_t COL_AURA_M = 0x3A98FFu;

static void draw_aura(uint8_t *buf, int vw, int vh, int tip_x, int tip_y, int frame) {
    int slot = mod_pos(frame, 4);
    uint16_t lo = gh_rgb888_to_565(AURA_PAL[slot][0]);
    uint16_t mid = gh_rgb888_to_565(AURA_PAL[slot][1]);
    uint16_t hi = gh_rgb888_to_565(AURA_PAL[slot][2]);
    uint16_t ice = gh_rgb888_to_565(AURA_PAL[slot][3]);
    int rx = AURA_RX[slot];
    int ry = AURA_RY[slot];
    int cx = tip_x - rx + 2;
    int cy = tip_y;
    int shift = AURA_SHIFT[slot];
    int back;
    int i;
    uint16_t cols[3];
    static const int spikes[4][2] = {{-8, 8}, {-3, 12}, {3, 11}, {8, 7}};
    fill_ellipse_clamped(buf, vw, vh, cx, cy, rx + 1, ry + 1, mid);
    gh_fill_triangle(buf, vw, vh, cx + rx - 5, cy - 6, tip_x + 1, cy, cx + rx - 5, cy + 6, mid);
    fill_ellipse_clamped(buf, vw, vh, cx + 2, cy, imax(2, rx - 5), imax(2, ry - 4), lo);
    fill_ellipse_clamped(buf, vw, vh, cx + 6, cy, imax(2, rx - 11), imax(2, ry - 7), hi);
    cols[0] = hi;
    cols[1] = ice;
    cols[2] = mid;
    for (i = 0; i < 3; i++) {
        chevron(buf, vw, vh, cx - 2 + i * 5 + shift, cy, imax(4, ry - 2), 5, cols[i], 4);
    }
    back = cx - rx;
    for (i = 0; i < 4; i++) {
        int dy = spikes[i][0];
        int length = spikes[i][1];
        uint16_t col = dy < 0 ? lo : mid;
        gh_fill_triangle(buf, vw, vh,
                         back + 2, cy + dy,
                         back - length, cy + dy,
                         back + 2, cy + dy + 3,
                         col);
    }
}

static void draw_shockwave(uint8_t *buf, int vw, int vh, int tip_x, int tip_y, int frame) {
    static const int heights[5] = {8, 14, 22, 30, 34};
    static const int widths[5] = {6, 8, 10, 12, 14};
    static const int frags[12][4] = {
        {-18, -16, 6, 0}, {-10, -22, 5, 1}, {4, -18, 5, 2},
        {10, -8, 5, 1}, {-20, 8, 5, 2}, {-8, 18, 6, 1},
        {8, 16, 4, 0}, {12, 4, 5, 2}, {-24, -4, 4, 0},
        {6, 22, 3, 1}, {-14, 20, 4, 2}, {14, -20, 4, 0},
    };
    int h, w;
    uint16_t cw, ci, cm;
    if (frame < 0 || frame > 4) {
        return;
    }
    h = heights[frame];
    w = widths[frame];
    cw = gh_rgb888_to_565(COL_AURA_W);
    ci = gh_rgb888_to_565(COL_AURA_I);
    cm = gh_rgb888_to_565(COL_AURA_M);
    if (frame < 4) {
        chevron(buf, vw, vh, tip_x - 1, tip_y, h, w, cw, 4 + frame);
        chevron(buf, vw, vh, tip_x - 6, tip_y, imax(4, h - 3), w - 2, ci, 4);
        chevron(buf, vw, vh, tip_x - 11, tip_y, imax(4, h - 6), imax(3, w - 3), cm, 3);
        if (frame >= 2) {
            chevron(buf, vw, vh, tip_x + 3, tip_y, imax(4, h - 8), 4, ci, 3);
        }
    } else {
        int i;
        uint16_t cols[3];
        cols[0] = ci;
        cols[1] = cw;
        cols[2] = cm;
        for (i = 0; i < 12; i++) {
            int dx = frags[i][0];
            int dy = frags[i][1];
            int s = frags[i][2];
            uint16_t col = cols[frags[i][3]];
            gh_fill_triangle(buf, vw, vh,
                             tip_x + dx, tip_y + dy,
                             tip_x + dx - s, tip_y + dy - 2,
                             tip_x + dx - 2, tip_y + dy + s,
                             col);
        }
    }
}

static void draw_barrier(uint8_t *buf, int vw, int vh, int tip_x, int tip_y, int frame) {
    uint16_t cw = gh_rgb888_to_565(COL_AURA_W);
    uint16_t ci = gh_rgb888_to_565(COL_AURA_I);
    uint16_t cm = gh_rgb888_to_565(COL_AURA_M);
    if (frame < 0 || frame > 5) {
        return;
    }
    if (frame <= 2) {
        int h = 6 + frame * 6;
        int x = tip_x + 2 + frame;
        chevron(buf, vw, vh, x, tip_y, h, 4, cw, 3);
        chevron(buf, vw, vh, x - 4, tip_y, imax(4, h - 3), 3, ci, 3);
    } else {
        int k = frame - 2;
        int x = tip_x + 6 + k * 8;
        int h = 22 - k * 4;
        chevron(buf, vw, vh, x, tip_y, imax(4, h), 3, ci, 3);
        gh_fill_triangle(buf, vw, vh,
                         x + 2, tip_y - h,
                         x + 8 + k, tip_y - h + 2,
                         x + 2, tip_y - h + 6,
                         cw);
        gh_fill_triangle(buf, vw, vh,
                         x + 2, tip_y + h,
                         x + 8 + k, tip_y + h - 2,
                         x + 2, tip_y + h - 6,
                         cm);
    }
}

void gh_fx(uint8_t *buf, int vw, int vh,
           int aura_i, int sw_i, int bar_i,
           int aura_x, int aura_y, int sw_x) {
    if (buf == 0 || vw <= 0 || vh <= 0) {
        return;
    }
    if (aura_i >= 0) {
        draw_aura(buf, vw, vh, aura_x, aura_y, aura_i);
    }
    if (sw_i >= 0) {
        draw_shockwave(buf, vw, vh, sw_x, aura_y, sw_i);
    }
    if (bar_i >= 0) {
        draw_barrier(buf, vw, vh, sw_x, aura_y, bar_i);
    }
}
