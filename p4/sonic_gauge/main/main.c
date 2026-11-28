/*
 * Sonic gauge for the Waveshare ESP32-P4 720x720 panel.
 *
 * pixidx.bin is the Atom pack, unchanged. Each source pixel is copied
 * into a 5x5 square (640x640) centered on the 720 panel. The rim repeats
 * the outermost pixel. The picture is presented on a 16667 us slot.
 * Sprite frames stay on the 15 fps clock so the run cycle does not
 * play four times too fast. Boost stamps the packed aura with index 0
 * left empty. Samples are the 16-byte gauge-link packet.
 */

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_ops.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"

#include "gh_blit.h"
#include "pix6.h"
#include "radio.h"
#include "scene.h"

static const char *TAG = "sonic";

#define DST 720
#define SCALE 5
#define ORIGIN ((DST - SCENE_W * SCALE) / 2)
#define FRAME_US 16667
#define ANIM_US 66667
#define VTEC_ON 5400

extern const uint8_t pixidx_bin_start[] asm("_binary_pixidx_bin_start");
extern const uint8_t pixidx_bin_end[] asm("_binary_pixidx_bin_end");

static void expand_row(const uint8_t *src, uint16_t *line)
{
    uint16_t left = (uint16_t)src[0] | ((uint16_t)src[1] << 8);
    uint16_t right = (uint16_t)src[(SCENE_W - 1) * 2] | ((uint16_t)src[(SCENE_W - 1) * 2 + 1] << 8);
    for (int i = 0; i < ORIGIN; i++) {
        line[i] = left;
    }
    uint16_t *p = line + ORIGIN;
    for (int x = 0; x < SCENE_W; x++) {
        uint16_t c = (uint16_t)src[x * 2] | ((uint16_t)src[x * 2 + 1] << 8);
        p[0] = c;
        p[1] = c;
        p[2] = c;
        p[3] = c;
        p[4] = c;
        p += SCALE;
    }
    for (int i = 0; i < ORIGIN; i++) {
        line[ORIGIN + SCENE_W * SCALE + i] = right;
    }
}

static void zoom_frame(const uint8_t *src, uint16_t *dst)
{
    static uint16_t line[DST];
    expand_row(src, line);
    for (int y = 0; y < ORIGIN; y++) {
        memcpy(dst + y * DST, line, DST * sizeof(uint16_t));
    }
    for (int sy = 0; sy < SCENE_H; sy++) {
        expand_row(src + (size_t)sy * SCENE_W * 2, line);
        uint16_t *row = dst + (ORIGIN + sy * SCALE) * DST;
        for (int k = 0; k < SCALE; k++) {
            memcpy(row + k * DST, line, DST * sizeof(uint16_t));
        }
    }
    for (int y = ORIGIN + SCENE_H * SCALE; y < DST; y++) {
        memcpy(dst + y * DST, line, DST * sizeof(uint16_t));
    }
}

static void wait_until(int64_t deadline)
{
    int64_t now = esp_timer_get_time();
    if (now >= deadline) {
        return;
    }
    int64_t remain = deadline - now;
    if (remain > 2000) {
        vTaskDelay(pdMS_TO_TICKS((int)((remain - 1000) / 1000)));
    }
    while (esp_timer_get_time() < deadline) {
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "sonic-gauge boot");
    size_t pack_len = (size_t)(pixidx_bin_end - pixidx_bin_start);
    if (!pix_load(pixidx_bin_start, pack_len)) {
        ESP_LOGE(TAG, "pack reject %u", (unsigned)pack_len);
        return;
    }
    ESP_LOGI(TAG, "pack frames %d", pix_count());
    if (!scene_prepare()) {
        return;
    }
    int radio_ok = radio_start();
    if (!radio_ok) {
        ESP_LOGE(TAG, "radio failed");
    }

    esp_lcd_panel_handle_t panel = 0;
    esp_lcd_panel_io_handle_t io = 0;
    esp_err_t err = bsp_display_new(NULL, &panel, &io);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "draw failed init %s", esp_err_to_name(err));
        panel = 0;
    } else {
        bsp_display_backlight_on();
        ESP_LOGI(TAG, "panel up");
    }

    uint8_t *play = heap_caps_malloc((size_t)SCENE_W * SCENE_H * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    void *fb[2] = {0, 0};
    int direct = 0;
    if (panel && esp_lcd_dpi_panel_get_frame_buffer(panel, 2, &fb[0], &fb[1]) == ESP_OK) {
        direct = 1;
    } else {
        fb[0] = heap_caps_aligned_alloc(64, (size_t)DST * DST * sizeof(uint16_t),
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        fb[1] = heap_caps_aligned_alloc(64, (size_t)DST * DST * sizeof(uint16_t),
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (play == 0 || fb[0] == 0 || fb[1] == 0) {
        ESP_LOGE(TAG, "framebuffer alloc failed");
        return;
    }
    ESP_LOGI(TAG, "present slot %d us anim %d us %s", FRAME_US, ANIM_US, direct ? "direct" : "copy");

    int which = direct ? 1 : 0;
    int64_t next_present = 0;
    int64_t prev_us = 0;
    int64_t last_log = 0;
    int last_packets = 0;
    uint32_t last_age = 0;
    int presents = 0;
    int overruns = 0;
    int64_t draw_max = 0;
    while (1) {
        int64_t t0 = esp_timer_get_time();
        if (next_present == 0) {
            next_present = t0;
        }
        float dt = prev_us ? (float)(t0 - prev_us) / 1000000.0f : (FRAME_US / 1000000.0f);
        prev_us = t0;

        radio_sample_t sample;
        radio_sample(&sample);
        int fresh = sample.seen && sample.age_ms <= 500;
        int rpm = fresh ? sample.rpm : 0;
        float spd = fresh ? sample.spd_kmh : 0;
        int tps = fresh ? sample.tps : 0;
        int clt = fresh ? sample.clt : 0;
        int boost = fresh && rpm >= VTEC_ON;
        uint32_t anim = (uint32_t)(t0 / ANIM_US);
        scene_draw(play, rpm, spd, tps, clt, boost, anim, dt);
        if (!fresh && sample.seen) {
            gh_fill_rect(play, SCENE_W, SCENE_H, 1, 1, 5, 5, pix_rgb(0xFF1744));
        }
        if (panel) {
            zoom_frame(play, fb[which]);
            err = esp_lcd_panel_draw_bitmap(panel, 0, 0, DST, DST, fb[which]);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "draw failed %s", esp_err_to_name(err));
            } else {
                which ^= 1;
            }
        }
        int64_t draw_us = esp_timer_get_time() - t0;
        if (draw_us > draw_max) {
            draw_max = draw_us;
        }
        presents++;
        next_present += FRAME_US;
        if (esp_timer_get_time() > next_present) {
            int64_t late = esp_timer_get_time() - next_present;
            int skipped = (int)(late / FRAME_US) + 1;
            overruns += skipped;
            next_present += (int64_t)skipped * FRAME_US;
        } else {
            wait_until(next_present);
        }
        int64_t wall = esp_timer_get_time();
        if (last_log == 0) {
            last_log = wall;
        }
        if (wall - last_log >= 1000000) {
            int pkts = sample.packets - last_packets;
            last_packets = sample.packets;
            ESP_LOGI(TAG,
                     "hz rpm=%d spd=%.1f tps=%d clt=%d flags=0x%02x seq=%d age=%u pkts=%d bad=%d/%d/%d draw=%lld max=%lld fps=%d over=%d radio=%d",
                     rpm, spd, tps, clt, sample.flags, sample.seq, (unsigned)sample.age_ms, pkts,
                     sample.bad_magic, sample.bad_len, sample.bad_crc,
                     (long long)draw_us, (long long)draw_max, presents, overruns, radio_ok);
            if (sample.seen && sample.age_ms > 1000 && last_age <= 1000) {
                ESP_LOGW(TAG, "now gap %u", (unsigned)sample.age_ms);
            }
            last_age = sample.age_ms;
            presents = 0;
            overruns = 0;
            draw_max = 0;
            last_log = wall;
        }
    }
}
