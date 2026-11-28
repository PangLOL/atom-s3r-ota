#pragma once

#include <stddef.h>
#include <stdint.h>

typedef struct {
    char name[17];
    const uint8_t *idx;
    const uint16_t *pal;
    int colors;
    int w;
    int h;
    int ox;
    int oy;
} pix_frame_t;

int pix_load(const uint8_t *data, size_t len);
const pix_frame_t *pix_find(const char *name);
int pix_count(void);

/* use_origin places (ox, oy) on (dest_x, dest_y).
   skip0 leaves index 0 untouched. Otherwise index 0 is sky. */
void pix_blit(uint8_t *dst, int dw, int dh, const pix_frame_t *fr,
              int dest_x, int dest_y, int use_origin, int clip_b, int skip0);

uint16_t pix_rgb(uint32_t rgb888);
uint16_t pix_sky(void);
