/* Green Hill playfield blit. No MicroPython headers.

   Host tests compile this file into a shared library. The xtensawin
   natmod and the optional firmware user module call the same functions.
   Offsets are already Python int() values, so this file does not redo
   the scroll multiply.
*/
#ifndef GH_BLIT_H
#define GH_BLIT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint16_t gh_rgb888_to_565(uint32_t c);

void gh_copy_rows(const uint8_t *tile, int tw,
                  int y0, int y1, int off,
                  uint8_t *out, int vw, int out_y);

void gh_blit_stamp(uint8_t *window, int vw, int vh, int tw,
                   const uint8_t *sbuf, const uint8_t *mask,
                   int sw, int sh, int ox, int oy, int off);

void gh_stamp_sprite(uint8_t *window, int vw, int vh,
                     const uint8_t *rgb, const uint8_t *mask,
                     int sw, int sh, int dest_x, int dest_y);

void gh_expand_indexed(const uint8_t *src, int stride, int bpp,
                       int w, int h,
                       const uint8_t *pairs, int ncolors,
                       uint8_t *dst);

void gh_fill_rect(uint8_t *buf, int vw, int vh,
                  int x, int y, int w, int h, uint16_t c565);

void gh_fill_ellipse(uint8_t *buf, int vw, int vh,
                     int x, int y, int rx, int ry, uint16_t c565);

void gh_fill_triangle(uint8_t *buf, int vw, int vh,
                      int x0, int y0, int x1, int y1, int x2, int y2,
                      uint16_t c565);

/* aura_i / sw_i / bar_i < 0 skips that layer. Order is aura, shockwave, barrier. */
void gh_fx(uint8_t *buf, int vw, int vh,
           int aura_i, int sw_i, int bar_i,
           int aura_x, int aura_y, int sw_x);

void gh_draw_digit(uint8_t *buf, int vw, int vh,
                   int x, int y, int n,
                   uint16_t color, uint16_t outline, int scale);

void gh_draw_hud_digits(uint8_t *buf, int vw, int vh,
                        int bx, int by, int rings);

#ifdef __cplusplus
}
#endif

#endif
