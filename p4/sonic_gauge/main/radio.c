#include "radio.h"

#include <stdint.h>
#include <string.h>

#include "esp_hosted_misc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_event.h"
#include "esp_netif.h"

static const char *TAG = "radio";

#define GL_MAGIC0 0x47
#define GL_MAGIC1 0x4C
#define GL_VERSION 1
#define GL_PACKET_SIZE 16
#define GL_MSG_INIT 0x474C0101u
#define GL_MSG_ACK 0x474C0102u
#define GL_MSG_RX 0x474C0103u

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static int s_rpm;
static float s_spd;
static int s_tps;
static int s_clt;
static int s_flags;
static int s_seq;
static int64_t s_rx_us;
static int s_seen;
static int s_packets;
static int s_bad_magic;
static int s_bad_len;
static int s_bad_crc;
static int s_logged_mac;
static SemaphoreHandle_t s_ack;
static volatile int32_t s_ack_status;

static uint16_t crc16_ccitt(const uint8_t *data, int len)
{
    uint16_t crc = 0xFFFF;
    for (int i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000) {
                crc = (uint16_t)((crc << 1) ^ 0x1021);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

static void on_ack(uint32_t msg_id, const uint8_t *data, size_t len, void *ctx)
{
    (void)msg_id;
    (void)ctx;
    int32_t status = -1;
    if (data != 0 && len >= 4) {
        memcpy(&status, data, sizeof(status));
    }
    s_ack_status = status;
    if (s_ack) {
        xSemaphoreGive(s_ack);
    }
}

static void on_packet(const uint8_t *data, const uint8_t *mac)
{
    if (data[0] != GL_MAGIC0 || data[1] != GL_MAGIC1 || data[2] != GL_VERSION) {
        portENTER_CRITICAL(&s_mux);
        s_bad_magic++;
        portEXIT_CRITICAL(&s_mux);
        return;
    }
    uint16_t got = (uint16_t)data[14] | ((uint16_t)data[15] << 8);
    if (crc16_ccitt(data, 14) != got) {
        portENTER_CRITICAL(&s_mux);
        s_bad_crc++;
        portEXIT_CRITICAL(&s_mux);
        return;
    }
    if (!s_logged_mac && mac) {
        s_logged_mac = 1;
        ESP_LOGI(TAG, "peer %02x:%02x:%02x:%02x:%02x:%02x",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    }
    int rpm = (int)((uint16_t)data[8] | ((uint16_t)data[9] << 8));
    int spd = (int)((uint16_t)data[6] | ((uint16_t)data[7] << 8));
    int seq = (int)((uint16_t)data[4] | ((uint16_t)data[5] << 8));
    portENTER_CRITICAL(&s_mux);
    s_rpm = rpm;
    s_spd = (float)spd;
    s_tps = data[10];
    s_clt = (int8_t)data[11];
    s_flags = data[3];
    s_seq = seq;
    s_rx_us = esp_timer_get_time();
    s_seen = 1;
    s_packets++;
    portEXIT_CRITICAL(&s_mux);
}

static void on_rx(uint32_t msg_id, const uint8_t *data, size_t len, void *ctx)
{
    (void)msg_id;
    (void)ctx;
    if (data == 0 || len < 9 + GL_PACKET_SIZE) {
        portENTER_CRITICAL(&s_mux);
        s_bad_len++;
        portEXIT_CRITICAL(&s_mux);
        return;
    }
    unsigned plen = data[8];
    if (plen < GL_PACKET_SIZE || plen > 64 || 9u + plen > len) {
        portENTER_CRITICAL(&s_mux);
        s_bad_len++;
        portEXIT_CRITICAL(&s_mux);
        return;
    }
    on_packet(data + 9, data + 2);
}

int radio_start(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "radio failed nvs %s", esp_err_to_name(err));
        return 0;
    }
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "radio failed wifi init %s", esp_err_to_name(err));
        return 0;
    }
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    err = esp_wifi_set_channel(GAUGE_ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "set_channel: %s", esp_err_to_name(err));
    }
    s_ack = xSemaphoreCreateBinary();
    if (s_ack == 0) {
        ESP_LOGE(TAG, "radio failed espnow");
        return 0;
    }
    err = esp_hosted_register_custom_callback(GL_MSG_ACK, on_ack, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ack cb: %s", esp_err_to_name(err));
        return 0;
    }
    err = esp_hosted_register_custom_callback(GL_MSG_RX, on_rx, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rx cb: %s", esp_err_to_name(err));
        return 0;
    }
    uint8_t channel = GAUGE_ESPNOW_CHANNEL;
    for (int attempt = 1; attempt <= 4; attempt++) {
        s_ack_status = -1;
        xSemaphoreTake(s_ack, 0);
        err = esp_hosted_send_custom_data(GL_MSG_INIT, &channel, 1);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "init send: %s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(30));
            continue;
        }
        if (xSemaphoreTake(s_ack, pdMS_TO_TICKS(500)) == pdTRUE && s_ack_status == 0) {
            ESP_LOGI(TAG, "ESP-NOW ready on channel %u", channel);
            return 1;
        }
        ESP_LOGW(TAG, "no C6 ack (attempt %d)", attempt);
        if (s_ack_status != 0 && s_ack_status != -1) {
            ESP_LOGW(TAG, "C6 rejected ESP-NOW (%ld)", (long)s_ack_status);
        }
        vTaskDelay(pdMS_TO_TICKS(30));
    }
    ESP_LOGE(TAG, "radio failed espnow");
    return 0;
}

void radio_sample(radio_sample_t *out)
{
    int64_t rx = 0;
    portENTER_CRITICAL(&s_mux);
    out->rpm = s_rpm;
    out->spd_kmh = s_spd;
    out->tps = s_tps;
    out->clt = s_clt;
    out->flags = s_flags;
    out->seq = s_seq;
    out->seen = s_seen;
    out->packets = s_packets;
    out->bad_magic = s_bad_magic;
    out->bad_len = s_bad_len;
    out->bad_crc = s_bad_crc;
    rx = s_rx_us;
    portEXIT_CRITICAL(&s_mux);
    if (!out->seen || rx == 0) {
        out->age_ms = 0;
        out->rpm = 0;
        out->spd_kmh = 0;
        out->tps = 0;
        out->clt = 0;
        return;
    }
    int64_t age = (esp_timer_get_time() - rx) / 1000;
    if (age < 0) {
        age = 0;
    }
    out->age_ms = (uint32_t)age;
    if (out->age_ms > 500) {
        out->rpm = 0;
        out->spd_kmh = 0;
        out->tps = 0;
        out->clt = 0;
    }
}
