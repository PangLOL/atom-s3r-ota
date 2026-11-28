#include "scene.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gh_blit.h"
#include "pix6.h"

static const char *TAG = "scene";

#define TILE_W 256
#define TILE_H 128
#define PLAY_H 103
#define HORIZON 53
#define GRASS_Y 88
#define BLADE_ROWS 16
#define FEET_X 35
#define FEET_Y 91
#define RING_Y 78
#define RING_PERIOD 1536

static uint8_t *s_tile;
static float s_scroll;

static const int k_rings[] = {66, 82, 97, 866, 882, 898, 914, 930};
static const char *k_props[] = {
    "prop_palm", "prop_sun", "prop_fl_0", "prop_fl_1", "prop_fl_2", "prop_fl_3",
};
static const int k_prop_x[] = {36, 120, 168, 200, 232, 270};

static int mod_pos(int v, int m)
{
    if (m <= 0) {
        return 0;
    }
    int r = v % m;
    if (r < 0) {
        r += m;
    }
    return r;
}

static void paint_ground(void)
{
    const pix_frame_t *grass = pix_find("gnd_grass");
    const pix_frame_t *floor = pix_find("gnd_floor");
    int period = 96;
    if (floor && floor->w > 0) {
        period = floor->w;
    } else if (grass && grass->w > 0) {
        period = grass->w;
    }
    if (grass && grass->w > 0 && grass->h > 0) {
        int blades = BLADE_ROWS;
        if (blades > grass->h) {
            blades = grass->h;
        }
        for (int y = 0; y < blades && GRASS_Y + y < TILE_H; y++) {
            for (int x = 0; x < TILE_W; x++) {
                int sx = x % period;
                if (sx >= grass->w) {
                    sx -= 8;
                }
                if (sx < 0 || sx >= grass->w) {
                    continue;
                }
                int idx = grass->idx[(size_t)y * (size_t)grass->w + (size_t)sx];
                if (idx <= 0 || idx >= grass->colors) {
                    continue;
                }
                const uint8_t *p = (const uint8_t *)grass->pal + (size_t)idx * 2;
                uint8_t *out = s_tile + ((size_t)(GRASS_Y + y) * TILE_W + (size_t)x) * 2;
                out[0] = p[0];
                out[1] = p[1];
            }
        }
    }
    if (floor == 0 || floor->w < 1 || floor->h < 1) {
        return;
    }
    for (int y = 0; y < floor->h && GRASS_Y + BLADE_ROWS + y < TILE_H; y++) {
        for (int x = 0; x < TILE_W; x++) {
            int sx = x % floor->w;
            int idx = floor->idx[(size_t)y * (size_t)floor->w + (size_t)sx];
            if (idx <= 0 || idx >= floor->colors) {
                continue;
            }
            const uint8_t *p = (const uint8_t *)floor->pal + (size_t)idx * 2;
            uint8_t *out = s_tile + ((size_t)(GRASS_Y + BLADE_ROWS + y) * TILE_W + (size_t)x) * 2;
            out[0] = p[0];
            out[1] = p[1];
        }
    }
}

int scene_prepare(void)
{
    s_tile = heap_caps_malloc((size_t)TILE_W * TILE_H * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_tile == 0) {
        s_tile = malloc((size_t)TILE_W * TILE_H * 2);
    }
    if (s_tile == 0) {
        ESP_LOGE(TAG, "tile alloc failed");
        return 0;
    }
    uint16_t sky = pix_sky();
    for (int i = 0; i < TILE_W * TILE_H; i++) {
        s_tile[i * 2] = (uint8_t)(sky & 0xFF);
        s_tile[i * 2 + 1] = (uint8_t)(sky >> 8);
    }
    const pix_frame_t *far = pix_find("far_bg");
    const pix_frame_t *mid = pix_find("mid_bg");
    if (far) {
        pix_blit(s_tile, TILE_W, TILE_H, far, 0, 0, 0, TILE_H, 0);
    }
    if (mid) {
        int y = far ? far->h : HORIZON;
        pix_blit(s_tile, TILE_W, TILE_H, mid, 0, y, 0, TILE_H, 0);
    }
    paint_ground();
    ESP_LOGI(TAG, "tile ready far %s mid %s", far ? "yes" : "no", mid ? "yes" : "no");
    ESP_LOGI(TAG, "aura blit %s", pix_find("fx_aura_0") ? "ready" : "missing");
    return 1;
}

static void blit_anchor(uint8_t *frame, const pix_frame_t *fr, int x, int y)
{
    if (fr == 0) {
        return;
    }
    pix_blit(frame, SCENE_W, SCENE_H, fr, x, y, 1, PLAY_H, 1);
    pix_blit(frame, SCENE_W, SCENE_H, fr, x - TILE_W, y, 1, PLAY_H, 1);
    pix_blit(frame, SCENE_W, SCENE_H, fr, x + TILE_W, y, 1, PLAY_H, 1);
}

static const pix_frame_t *named(const char *prefix, int index)
{
    char buf[20];
    snprintf(buf, sizeof(buf), "%s_%d", prefix, index);
    return pix_find(buf);
}

static uint16_t value_565(float t)
{
    int r;
    int g;
    int b;
    if (t <= 0) {
        return pix_rgb(0x00C853);
    }
    if (t >= 1) {
        return pix_rgb(0xFF1744);
    }
    if (t < 0.5f) {
        float u = t * 2.0f;
        r = (int)(0x00 + (0xFF - 0x00) * u);
        g = (int)(0xC8 + (0xD6 - 0xC8) * u);
        b = (int)(0x53 + (0x00 - 0x53) * u);
    } else {
        float u = (t - 0.5f) * 2.0f;
        r = 0xFF;
        g = (int)(0xD6 + (0x17 - 0xD6) * u);
        b = (int)(0x00 + (0x44 - 0x00) * u);
    }
    return pix_rgb(((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b);
}

static void draw_gauges(uint8_t *frame, int rpm, float spd, int tps, int clt)
{
    const int gauge_y = 103;
    const int gauge_h = 25;
    const int bar_w = 61;
    const int bar_h = 10;
    const int x0 = 2;
    const int x1 = 65;
    const int y0 = 105;
    const int y1 = 116;
    gh_fill_rect(frame, SCENE_W, SCENE_H, 0, gauge_y, SCENE_W, gauge_h, pix_rgb(0x101012));
    struct {
        float v, lo, hi;
        int x, y;
        int orange;
    } bars[4] = {
        {(float)rpm, 0, 8000, x0, y0, 0},
        {spd, 0, 180, x1, y0, 1},
        {(float)tps, 0, 100, x0, y1, 0},
        {(float)clt, -40, 125, x1, y1, 0},
    };
    uint16_t track = pix_rgb(0x2A2A2A);
    for (int i = 0; i < 4; i++) {
        float span = bars[i].hi - bars[i].lo;
        float frac = span > 0 ? (bars[i].v - bars[i].lo) / span : 0;
        if (frac < 0) {
            frac = 0;
        }
        if (frac > 1) {
            frac = 1;
        }
        int fill = (int)(frac * bar_w);
        gh_fill_rect(frame, SCENE_W, SCENE_H, bars[i].x, bars[i].y, bar_w, bar_h, track);
        if (fill > 0) {
            uint16_t col = bars[i].orange ? pix_rgb(0xFF8C00) : value_565(frac);
            gh_fill_rect(frame, SCENE_W, SCENE_H, bars[i].x, bars[i].y, fill, bar_h, col);
        }
    }
}

void scene_draw(uint8_t *frame, int rpm, float spd_kmh, int tps, int clt,
                int boost, uint32_t step, float dt_s)
{
    if (s_tile == 0 || frame == 0) {
        return;
    }
    if (spd_kmh < 0) {
        spd_kmh = 0;
    }
    if (dt_s < 0.0f) {
        dt_s = 0.0f;
    }
    if (dt_s > 0.05f) {
        dt_s = 0.05f;
    }
    /* The Atom stepped 0.08 px per 66 ms frame. Keep that world speed at 60 Hz. */
    s_scroll += spd_kmh * (0.08f * (1000.0f / 66.0f)) * dt_s;
    int scroll = (int)s_scroll;
    int off_c = mod_pos((int)(s_scroll * 0.25f), TILE_W);
    int off_w = mod_pos((int)(s_scroll * 0.60f), TILE_W);
    int off_g = mod_pos(scroll, TILE_W);
    memset(frame, 0, (size_t)SCENE_W * SCENE_H * 2);
    gh_copy_rows(s_tile, TILE_W, 0, HORIZON, off_c, frame, SCENE_W, 0);
    gh_copy_rows(s_tile, TILE_W, HORIZON, GRASS_Y, off_w, frame, SCENE_W, HORIZON);
    gh_copy_rows(s_tile, TILE_W, GRASS_Y, PLAY_H, off_g, frame, SCENE_W, GRASS_Y);

    for (int i = 0; i < 6; i++) {
        blit_anchor(frame, pix_find(k_props[i]), k_prop_x[i] - off_g, FEET_Y);
    }

    const pix_frame_t *ring = named("rings", (int)(step % 8));
    for (int i = 0; i < 8; i++) {
        int x = k_rings[i] - scroll;
        x = mod_pos(x, RING_PERIOD);
        if (x > SCENE_W + 40) {
            x -= RING_PERIOD;
        }
        if (x > -40 && x < SCENE_W + 40) {
            pix_blit(frame, SCENE_W, SCENE_H, ring, x, RING_Y, 1, PLAY_H, 1);
        }
    }

    const char *tag = "idle";
    if (spd_kmh >= 80.0f) {
        tag = "run_fast";
    } else if (spd_kmh >= 40.0f) {
        tag = "run_medium";
    } else if (spd_kmh >= 1.0f) {
        tag = "run_slow";
    }
    int anim = (tag[0] == 'i') ? 0 : (int)(step % 8);
    pix_blit(frame, SCENE_W, SCENE_H, named(tag, anim), FEET_X, FEET_Y, 1, PLAY_H, 1);

    const pix_frame_t *hud = pix_find("hud_gauge");
    if (hud) {
        pix_blit(frame, SCENE_W, SCENE_H, hud, 4, 2, 0, PLAY_H, 1);
    }

    if (boost) {
        pix_blit(frame, SCENE_W, SCENE_H, named("fx_aura", (int)(step % 4)), 66, 72, 1, PLAY_H, 1);
        pix_blit(frame, SCENE_W, SCENE_H, named("fx_boot", (int)(step % 6)), 76, 90, 1, PLAY_H, 1);
        pix_blit(frame, SCENE_W, SCENE_H, named("fx_bar", (int)(step % 4)), 68, 72, 1, PLAY_H, 1);
        pix_blit(frame, SCENE_W, SCENE_H, named("fx_spark", (int)(step % 5)), 22, 88, 1, PLAY_H, 1);
    }
    draw_gauges(frame, rpm, spd_kmh, tps, clt);
}
