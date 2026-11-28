#pragma once

#include <stdint.h>

#define SCENE_W 128
#define SCENE_H 128

int scene_prepare(void);
void scene_draw(uint8_t *frame, int rpm, float spd_kmh, int tps, int clt,
                int boost, uint32_t step, float dt_s);
