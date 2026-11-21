/* Loadable native module for UiFlow2 / MicroPython 1.27 (xtensawin, .mpy v6.3).

   import gh_render
   gh_render.compose(tile, tw, th, off_c, off_w, off_g, horizon, grass, stamps, offs, window)
   gh_render.copy(tile, tw, th, off, window, vw)
   gh_render.stamp(window, vw, vh, rgb, mask, sw, sh, x, y)
   gh_render.expand(src, stride, bpp, w, h, pairs, ncolors, dst)
   gh_render.fx(window, vw, vh, aura_i, sw_i, bar_i, aura_x, aura_y, sw_x)
   gh_render.digits(window, vw, vh, bx, by, rings)
   gh_render.refresh_ok()  -> prints "present-ok" then "refresh ok" after a successful panel push

   A real playfield (height >= 64) also prints, in order:
   top ok, compose-ok or copy-ok, stamp-ok, fx-ok, digits-ok.
   The boot self-test uses a short tile and stays quiet.
*/

#include "py/dynruntime.h"

#include "gh_blit.h"

/* The natmod is not linked with libc. Frame copies go through the firmware. */
void *memcpy(void *dst, const void *src, size_t n) {
    return mp_fun_table.memmove_(dst, src, n);
}

void *memset(void *s, int c, size_t n) {
    return mp_fun_table.memset_(s, c, n);
}

/* Set by a real-playfield compose or copy. gh_fx prints stamp-ok once and clears it.
   Global BSS: a native module cannot use a static BSS variable. */
int gh_stamp_pending;

#if defined(__xtensa__)
#define GH_BUILD "micropython-1.27.0-xtensawin-mpy6.3"
#define GH_ARCH "xtensawin"
#elif defined(__x86_64__) || defined(__amd64__)
#define GH_BUILD "micropython-1.27.0-x64-mpy6.3"
#define GH_ARCH "x64"
#else
#define GH_BUILD "micropython-1.27.0-native-mpy6.3"
#define GH_ARCH "native"
#endif

static void need_buf(mp_obj_t obj, mp_buffer_info_t *info, int write) {
    mp_get_buffer_raise(obj, info, write ? MP_BUFFER_WRITE : MP_BUFFER_READ);
}

static int arg_int(mp_obj_t o) {
    return (int)mp_obj_get_int(o);
}

static void need_len(const mp_buffer_info_t *info, size_t n, const char *what) {
    if (info->len < n) {
        (void)what;
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
    (void)n_args;
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
    if (th >= 64) {
        mp_printf(&mp_plat_print, "top ok\n");
        gh_stamp_pending = 1;
    }
    need_len(&tile, (size_t)tw * (size_t)th * 2, "tile");
    need_len(&win, (size_t)vw * (size_t)th * 2, "window");
    gh_copy_rows(tile.buf, tw, 0, horizon, off_c, win.buf, vw, 0);
    gh_copy_rows(tile.buf, tw, horizon, grass, off_w, win.buf, vw, horizon);
    gh_copy_rows(tile.buf, tw, grass, th, off_g, win.buf, vw, grass);
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
        off = 0;
        if (i < noff) {
            off = arg_int(offs[i]);
        }
        if (len >= 8) {
            mp_buffer_info_t spans;
            need_buf(item[7], &spans, 0);
            if (spans.len >= 4) {
                gh_blit_span_runs(win.buf, vw, th, tw, sbuf.buf, sw, sh,
                                  spans.buf, (int)(spans.len / 4), ox, oy, off);
                continue;
            }
        }
        gh_blit_stamp(win.buf, vw, th, tw, sbuf.buf, mask.buf, sw, sh, ox, oy, off);
    }
    if (th >= 64) {
        mp_printf(&mp_plat_print, "compose-ok\n");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_compose_obj, 11, 11, gh_compose);

static mp_obj_t gh_copy(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t tile, win;
    int tw, th, off, vw;
    (void)n_args;
    need_buf(args[0], &tile, 0);
    tw = arg_int(args[1]);
    th = arg_int(args[2]);
    off = arg_int(args[3]);
    need_buf(args[4], &win, 1);
    vw = arg_int(args[5]);
    if (tw > 0 && th > 0 && vw > 0) {
        if (th >= 64) {
            mp_printf(&mp_plat_print, "top ok\n");
            gh_stamp_pending = 1;
        }
        need_len(&tile, (size_t)tw * (size_t)th * 2, "tile");
        need_len(&win, (size_t)vw * (size_t)th * 2, "window");
        gh_copy_rows(tile.buf, tw, 0, th, off, win.buf, vw, 0);
        if (th >= 64) {
            mp_printf(&mp_plat_print, "copy-ok\n");
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_copy_obj, 6, 6, gh_copy);

static mp_obj_t gh_stamp(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win, rgb, mask;
    int vw, vh, sw, sh, x, y;
    (void)n_args;
    need_buf(args[0], &win, 1);
    vw = arg_int(args[1]);
    vh = arg_int(args[2]);
    need_buf(args[3], &rgb, 0);
    need_buf(args[4], &mask, 0);
    sw = arg_int(args[5]);
    sh = arg_int(args[6]);
    x = arg_int(args[7]);
    y = arg_int(args[8]);
    if (vw > 0 && vh > 0) {
        need_len(&win, (size_t)vw * (size_t)vh * 2, "window");
        gh_stamp_sprite(win.buf, vw, vh, rgb.buf, mask.buf, sw, sh, x, y);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_stamp_obj, 9, 9, gh_stamp);

static mp_obj_t gh_stamp_runs_fn(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win, runs, pairs;
    int vw, vh, nruns, ncolors, x, y;
    (void)n_args;
    need_buf(args[0], &win, 1);
    vw = arg_int(args[1]);
    vh = arg_int(args[2]);
    need_buf(args[3], &runs, 0);
    nruns = arg_int(args[4]);
    need_buf(args[5], &pairs, 0);
    ncolors = arg_int(args[6]);
    x = arg_int(args[7]);
    y = arg_int(args[8]);
    if (vw > 0 && vh > 0 && nruns > 0) {
        need_len(&win, (size_t)vw * (size_t)vh * 2, "window");
        gh_stamp_runs(win.buf, vw, vh, runs.buf, nruns, pairs.buf, ncolors, x, y);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_stamp_runs_obj, 9, 9, gh_stamp_runs_fn);

static mp_obj_t gh_push_in(void) {
    mp_printf(&mp_plat_print, "push-in\n");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(gh_push_in_obj, gh_push_in);

static mp_obj_t gh_expand(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t src, pairs, dst;
    int stride, bpp, w, h, ncolors;
    (void)n_args;
    need_buf(args[0], &src, 0);
    stride = arg_int(args[1]);
    bpp = arg_int(args[2]);
    w = arg_int(args[3]);
    h = arg_int(args[4]);
    need_buf(args[5], &pairs, 0);
    ncolors = arg_int(args[6]);
    need_buf(args[7], &dst, 1);
    if (w > 0 && h > 0) {
        need_len(&dst, (size_t)w * (size_t)h * 2, "dst");
        gh_expand_indexed(src.buf, stride, bpp, w, h, pairs.buf, ncolors, dst.buf);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_expand_obj, 8, 8, gh_expand);

static mp_obj_t gh_fx_fn(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win;
    int vw, vh;
    (void)n_args;
    need_buf(args[0], &win, 1);
    vw = arg_int(args[1]);
    vh = arg_int(args[2]);
    if (vw > 0 && vh > 0) {
        if (vh >= 64 && gh_stamp_pending) {
            mp_printf(&mp_plat_print, "stamp-ok\n");
            gh_stamp_pending = 0;
        }
        need_len(&win, (size_t)vw * (size_t)vh * 2, "window");
        gh_fx(win.buf, vw, vh,
              arg_int(args[3]), arg_int(args[4]), arg_int(args[5]),
              arg_int(args[6]), arg_int(args[7]), arg_int(args[8]));
        if (vh >= 64) {
            mp_printf(&mp_plat_print, "fx-ok\n");
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_fx_obj, 9, 9, gh_fx_fn);

static mp_obj_t gh_digits(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t win;
    int vw, vh;
    (void)n_args;
    need_buf(args[0], &win, 1);
    vw = arg_int(args[1]);
    vh = arg_int(args[2]);
    if (vw > 0 && vh > 0) {
        need_len(&win, (size_t)vw * (size_t)vh * 2, "window");
        gh_draw_hud_digits(win.buf, vw, vh, arg_int(args[3]), arg_int(args[4]), arg_int(args[5]));
        if (vh >= 64) {
            mp_printf(&mp_plat_print, "digits-ok\n");
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gh_digits_obj, 6, 6, gh_digits);

/* main.py calls this after pushImage accepts the playfield. */
static mp_obj_t gh_refresh_ok(void) {
    mp_printf(&mp_plat_print, "present-ok\n");
    mp_printf(&mp_plat_print, "refresh ok\n");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(gh_refresh_ok_obj, gh_refresh_ok);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    (void)n_args;
    (void)n_kw;
    (void)args;
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_compose, MP_OBJ_FROM_PTR(&gh_compose_obj));
    mp_store_global(MP_QSTR_copy, MP_OBJ_FROM_PTR(&gh_copy_obj));
    mp_store_global(MP_QSTR_stamp, MP_OBJ_FROM_PTR(&gh_stamp_obj));
    mp_store_global(MP_QSTR_stamp_runs, MP_OBJ_FROM_PTR(&gh_stamp_runs_obj));
    mp_store_global(MP_QSTR_push_in, MP_OBJ_FROM_PTR(&gh_push_in_obj));
    mp_store_global(MP_QSTR_expand, MP_OBJ_FROM_PTR(&gh_expand_obj));
    mp_store_global(MP_QSTR_fx, MP_OBJ_FROM_PTR(&gh_fx_obj));
    mp_store_global(MP_QSTR_digits, MP_OBJ_FROM_PTR(&gh_digits_obj));
    mp_store_global(MP_QSTR_refresh_ok, MP_OBJ_FROM_PTR(&gh_refresh_ok_obj));
    mp_store_global(MP_QSTR_BUILD, mp_obj_new_str(GH_BUILD, sizeof(GH_BUILD) - 1));
    mp_store_global(MP_QSTR_ARCH, mp_obj_new_str(GH_ARCH, sizeof(GH_ARCH) - 1));
    MP_DYNRUNTIME_INIT_EXIT
}
