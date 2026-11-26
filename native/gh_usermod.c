/* Firmware user C module. Same entry points as gh_mod.c.

   Build against UiFlow2's MicroPython (see README). This is the fallback
   when the running firmware will not import a native .mpy. Installing it
   means flashing a firmware image, not only copying main.py.
*/

#include "py/runtime.h"
#include "py/obj.h"

#include "gh_blit.h"

static void need_buf(mp_obj_t obj, mp_buffer_info_t *info, int write) {
    mp_get_buffer_raise(obj, info, write ? MP_BUFFER_WRITE : MP_BUFFER_READ);
}

static int arg_int(mp_obj_t o) {
    return (int)mp_obj_get_int(o);
}

static void need_len(const mp_buffer_info_t *info, size_t n) {
    if (info->len < n) {
        mp_raise_ValueError(MP_ERROR_TEXT("short buffer"));
    }
}

static mp_obj_t gh_compose(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t tile, win;
    size_t nstamp = 0;
    size_t noff = 0;
    mp_obj_t *stamps = NULL;
    mp_obj_t *offs = NULL;
    int tw, th, vw, off_c, off_w, off_g, horizon, grass;
    size_t i;
    need_buf(args[0], &tile, 0);
    tw = arg_int(args[1]);
    th = arg_int(args[2]);
    off_c = arg_int(args[3]);
    off_w = arg_int(args[4]);
    off_g = arg_int(args[5]);
    horizon = arg_int(args[6]);
    grass = arg_int(args[7]);
    mp_obj_get_array(args[8], &nstamp, &stamps);
    mp_obj_get_array(args[9], &noff, &offs);
    need_buf(args[10], &win, 1);
    vw = 128;
    if (tw <= 0 || th <= 0) {
        return mp_const_none;
    }
    if (horizon < 0) {
        horizon = 0;
    }
    if (horizon > th) {
        horizon = th;
    }
    if (grass < horizon) {
        grass = horizon;
    }
    if (grass > th) {
        grass = th;
    }
    need_len(&tile, (size_t)tw * (size_t)th * 2);
    need_len(&win, (size_t)vw * (size_t)th * 2);
    {
        int sea_dy = 0;
        if (n_args >= 12) {
            sea_dy = arg_int(args[11]);
        }
        gh_compose_bands(tile.buf, tw, th, off_c, off_w, off_g,
                         horizon, grass, sea_dy, win.buf, vw);
    }
    for (i = 0; i < nstamp; i++) {
        size_t len = 0;
        mp_obj_t *item = NULL;
        mp_buffer_info_t sbuf, mask;
        int ox, oy, sw, sh, off;
        mp_obj_get_array(stamps[i], &len, &item);
        if (len < 7) {
            mp_raise_ValueError(MP_ERROR_TEXT("stamp"));
        }
        ox = arg_int(item[1]);
        oy = arg_int(item[2]);
        sw = arg_int(item[3]);
        sh = arg_int(item[4]);
        need_buf(item[5], &sbuf, 0);
        need_buf(item[6], &mask, 0);
        off = (i < noff) ? arg_int(offs[i]) : 0;
        gh_blit_stamp(win.buf, vw, th, tw, sbuf.buf, mask.buf, sw, sh, ox, oy, off);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_compose_obj, 11, 12, gh_compose);

static mp_obj_t gh_copy(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t tile, win;
    int tw = arg_int(args[1]);
    int th = arg_int(args[2]);
    int off = arg_int(args[3]);
    int vw = arg_int(args[5]);
    (void)n_args;
    need_buf(args[0], &tile, 0);
    need_buf(args[4], &win, 1);
    if (tw > 0 && th > 0 && vw > 0) {
        need_len(&tile, (size_t)tw * (size_t)th * 2);
        need_len(&win, (size_t)vw * (size_t)th * 2);
        gh_copy_rows(tile.buf, tw, 0, th, off, win.buf, vw, 0);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_copy_obj, 6, 6, gh_copy);

static mp_obj_t gh_stamp(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win, rgb, mask;
    int vw = arg_int(args[1]);
    int vh = arg_int(args[2]);
    int sw = arg_int(args[5]);
    int sh = arg_int(args[6]);
    int x = arg_int(args[7]);
    int y = arg_int(args[8]);
    (void)n_args;
    need_buf(args[0], &win, 1);
    need_buf(args[3], &rgb, 0);
    need_buf(args[4], &mask, 0);
    if (vw > 0 && vh > 0) {
        need_len(&win, (size_t)vw * (size_t)vh * 2);
        gh_stamp_sprite(win.buf, vw, vh, rgb.buf, mask.buf, sw, sh, x, y);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_stamp_obj, 9, 9, gh_stamp);

static mp_obj_t gh_expand(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t src, pairs, dst;
    int stride = arg_int(args[1]);
    int bpp = arg_int(args[2]);
    int w = arg_int(args[3]);
    int h = arg_int(args[4]);
    int ncolors = arg_int(args[6]);
    (void)n_args;
    need_buf(args[0], &src, 0);
    need_buf(args[5], &pairs, 0);
    need_buf(args[7], &dst, 1);
    if (w > 0 && h > 0) {
        need_len(&dst, (size_t)w * (size_t)h * 2);
        gh_expand_indexed(src.buf, stride, bpp, w, h, pairs.buf, ncolors, dst.buf);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_expand_obj, 8, 8, gh_expand);

static mp_obj_t gh_fx_fn(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win;
    int vw = arg_int(args[1]);
    int vh = arg_int(args[2]);
    int spark_i;
    int aura_x, aura_y, sw_x, bar_x, spark_x, spark_y;
    (void)n_args;
    need_buf(args[0], &win, 1);
    aura_x = arg_int(args[6]);
    aura_y = arg_int(args[7]);
    sw_x = arg_int(args[8]);
    spark_i = -1;
    bar_x = sw_x;
    spark_x = 22;
    spark_y = 88;
    if (n_args >= 10) {
        spark_i = arg_int(args[9]);
    }
    if (n_args >= 11) {
        bar_x = arg_int(args[10]);
    }
    if (n_args >= 12) {
        spark_x = arg_int(args[11]);
    }
    if (n_args >= 13) {
        spark_y = arg_int(args[12]);
    }
    if (vw > 0 && vh > 0) {
        need_len(&win, (size_t)vw * (size_t)vh * 2);
        gh_fx(win.buf, vw, vh,
              arg_int(args[3]), arg_int(args[4]), arg_int(args[5]), spark_i,
              aura_x, aura_y, sw_x, bar_x, spark_x, spark_y);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_fx_obj, 9, 13, gh_fx_fn);

static mp_obj_t gh_digits(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win;
    int vw = arg_int(args[1]);
    int vh = arg_int(args[2]);
    (void)n_args;
    need_buf(args[0], &win, 1);
    if (vw > 0 && vh > 0) {
        need_len(&win, (size_t)vw * (size_t)vh * 2);
        gh_draw_hud_digits(win.buf, vw, vh, arg_int(args[3]), arg_int(args[4]), arg_int(args[5]));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_digits_obj, 6, 6, gh_digits);

static mp_obj_t gh_refresh_ok(void) {
    mp_printf(&mp_plat_print, "top ok\n");
    mp_printf(&mp_plat_print, "refresh ok\n");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(gh_refresh_ok_obj, gh_refresh_ok);

static const mp_rom_map_elem_t gh_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_gh_render)},
    {MP_ROM_QSTR(MP_QSTR_compose), MP_ROM_PTR(&gh_compose_obj)},
    {MP_ROM_QSTR(MP_QSTR_copy), MP_ROM_PTR(&gh_copy_obj)},
    {MP_ROM_QSTR(MP_QSTR_stamp), MP_ROM_PTR(&gh_stamp_obj)},
    {MP_ROM_QSTR(MP_QSTR_expand), MP_ROM_PTR(&gh_expand_obj)},
    {MP_ROM_QSTR(MP_QSTR_fx), MP_ROM_PTR(&gh_fx_obj)},
    {MP_ROM_QSTR(MP_QSTR_digits), MP_ROM_PTR(&gh_digits_obj)},
    {MP_ROM_QSTR(MP_QSTR_refresh_ok), MP_ROM_PTR(&gh_refresh_ok_obj)},
    {MP_ROM_QSTR(MP_QSTR_BUILD), MP_ROM_QSTR(MP_QSTR_firmware)},
    {MP_ROM_QSTR(MP_QSTR_ARCH), MP_ROM_QSTR(MP_QSTR_usermod)},
};

static MP_DEFINE_CONST_DICT(gh_module_globals, gh_module_globals_table);

const mp_obj_module_t gh_render_user_cmodule = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&gh_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_gh_render, gh_render_user_cmodule);
