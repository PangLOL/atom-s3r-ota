#include "pix6.h"

#include <string.h>

#include "gh_blit.h"

#define PIX_MAX 128

static pix_frame_t s_frames[PIX_MAX];
static int s_count;
static uint16_t s_sky;

static uint16_t u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t u32(const uint8_t *p)
{
    return (uint32_t)u16(p) | ((uint32_t)u16(p + 2) << 16);
}

static int i16(const uint8_t *p)
{
    int v = u16(p);
    if (v >= 32768) {
        v -= 65536;
    }
    return v;
}

uint16_t pix_rgb(uint32_t rgb888)
{
    return gh_rgb888_to_565(rgb888);
}

uint16_t pix_sky(void)
{
    return s_sky;
}

int pix_count(void)
{
    return s_count;
}

int pix_load(const uint8_t *data, size_t len)
{
    s_count = 0;
    s_sky = pix_rgb(0x3AA4F0);
    if (data == 0 || len < 32 || memcmp(data, "PIX6", 4) != 0) {
        return 0;
    }
    if (u16(data + 4) != 1) {
        return 0;
    }
    int flags = u16(data + 6);
    if (flags & 1) {
        return 0;
    }
    int n_pal = u16(data + 8);
    int colors = u16(data + 10);
    int n_frames = u16(data + 12);
    uint32_t pal_off = u32(data + 16);
    uint32_t tab_off = u32(data + 20);
    uint32_t pix_off = u32(data + 24);
    uint32_t pix_size = u32(data + 28);
    if (n_frames < 1 || n_frames > PIX_MAX || colors < 2) {
        return 0;
    }
    if ((size_t)pal_off >= len || (size_t)tab_off >= len || (size_t)pix_off >= len) {
        return 0;
    }
    for (int i = 0; i < n_frames; i++) {
        const uint8_t *e = data + tab_off + (uint32_t)i * 32;
        if ((size_t)(e - data) + 32 > len) {
            return 0;
        }
        pix_frame_t *fr = &s_frames[s_count];
        memset(fr, 0, sizeof(*fr));
        memcpy(fr->name, e, 16);
        fr->name[16] = 0;
        uint32_t off = u32(e + 16);
        fr->w = u16(e + 20);
        fr->h = u16(e + 22);
        fr->ox = i16(e + 26);
        fr->oy = i16(e + 28);
        int pal = e[30];
        if (pal < 0 || pal >= n_pal || fr->w < 0 || fr->h < 0) {
            return 0;
        }
        uint32_t nbytes = (uint32_t)fr->w * (uint32_t)fr->h;
        if (off + nbytes > pix_size) {
            return 0;
        }
        fr->idx = data + pix_off + off;
        fr->pal = (const uint16_t *)(data + pal_off + (uint32_t)pal * (uint32_t)colors * 2);
        fr->colors = colors;
        s_count++;
    }
    return 1;
}

const pix_frame_t *pix_find(const char *name)
{
    for (int i = 0; i < s_count; i++) {
        if (strcmp(s_frames[i].name, name) == 0) {
            return &s_frames[i];
        }
    }
    return 0;
}

void pix_blit(uint8_t *dst, int dw, int dh, const pix_frame_t *fr,
              int dest_x, int dest_y, int use_origin, int clip_b, int skip0)
{
    if (dst == 0 || fr == 0 || fr->w < 1 || fr->h < 1 || dw < 1 || dh < 1) {
        return;
    }
    int x0 = use_origin ? dest_x - fr->ox : dest_x;
    int y0 = use_origin ? dest_y - fr->oy : dest_y;
    if (clip_b > dh) {
        clip_b = dh;
    }
    if (clip_b < 1) {
        return;
    }
    uint16_t sky = s_sky;
    for (int y = 0; y < fr->h; y++) {
        int dy = y0 + y;
        if (dy < 0 || dy >= clip_b) {
            continue;
        }
        const uint8_t *row = fr->idx + (size_t)y * (size_t)fr->w;
        for (int x = 0; x < fr->w; x++) {
            int dx = x0 + x;
            if (dx < 0 || dx >= dw) {
                continue;
            }
            int idx = row[x];
            if (idx == 0 && skip0) {
                continue;
            }
            uint16_t c = sky;
            if (idx > 0 && idx < fr->colors) {
                const uint8_t *p = (const uint8_t *)fr->pal + (size_t)idx * 2;
                c = (uint16_t)p[0] | ((uint16_t)p[1] << 8);
            }
            uint8_t *out = dst + ((size_t)dy * (size_t)dw + (size_t)dx) * 2;
            out[0] = (uint8_t)(c & 0xFF);
            out[1] = (uint8_t)(c >> 8);
        }
    }
}
