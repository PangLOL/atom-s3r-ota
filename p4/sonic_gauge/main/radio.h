#pragma once

#include <stdint.h>

#define GAUGE_ESPNOW_CHANNEL 1
#define FLAG_LIVE_CAN (1u << 0)

typedef struct {
    int rpm;
    float spd_kmh;
    int tps;
    int clt;
    int flags;
    int seq;
    uint32_t age_ms;
    int seen;
    int packets;
    int bad_magic;
    int bad_len;
    int bad_crc;
} radio_sample_t;

int radio_start(void);
void radio_sample(radio_sample_t *out);
