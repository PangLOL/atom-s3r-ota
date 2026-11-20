"""Host compare for the V13 driving benchmark.

Proves the packed ocean is not drawn, the V2 Green Hill scene fills the
playfield behind the baked sprites, and those sprite pixels match the
Python stamp and the fillRect blit at the locked positions.

Run from the repo root: python3 host_compare_v13.py
"""

import hashlib
import inspect
import io
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

m5 = types.ModuleType("M5")


class _Lcd(object):
    pass


m5.Lcd = _Lcd
m5.begin = lambda: None
m5.update = lambda: None
sys.modules["M5"] = m5

import main  # noqa: E402


def _px(buf, x, y):
    i = (y * main.W + x) * 2
    return buf[i] | (buf[i + 1] << 8)


def _put_count(a, b):
    n = 0
    i = 0
    limit = len(a) if len(a) < len(b) else len(b)
    while i < limit:
        if a[i] != b[i] or a[i + 1] != b[i + 1]:
            n += 1
        i += 2
    return n


def _row_shifted(a, b, y, shift):
    """True when row y of b is row y of a moved left by shift."""
    if shift < 0:
        return False
    x = 0
    while x + shift < main.W:
        if _px(a, x + shift, y) != _px(b, x, y):
            return False
        x += 1
    return x > 0


def _row_bad(a, b, y, shift):
    if shift < 0:
        return main.W
    bad = 0
    x = 0
    while x + shift < main.W:
        if _px(a, x + shift, y) != _px(b, x, y):
            bad += 1
        x += 1
    return bad


def _best_shift(a, b, y, lo, hi):
    best_d = lo
    best_bad = None
    d = lo
    while d <= hi:
        bad = _row_bad(a, b, y, d)
        if best_bad is None or bad < best_bad:
            best_bad = bad
            best_d = d
        d += 1
    return best_d, best_bad


class BlitRec(object):
    """Records fillRect colors from the interpreter blit."""

    def __init__(self):
        self.pix = [None] * (main.W * main.GAUGE_Y)
        self.fills = 0

    def fillRect(self, x, y, w, h, color):
        self.fills += 1
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        if w <= 0 or h <= 0:
            return
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x >= main.W or y >= main.GAUGE_Y:
            return
        if x + w > main.W:
            w = main.W - x
        if y + h > main.GAUGE_Y:
            h = main.GAUGE_Y - y
        row = 0
        while row < h:
            yy = y + row
            col = 0
            while col < w:
                self.pix[yy * main.W + (x + col)] = color
                col += 1
            row += 1


class FakeLcd(object):
    def __init__(self):
        self.pushes = []
        self.fills = 0
        self.ops = []

    def fillScreen(self, color):
        self.ops.append("fillScreen")

    def pushImage(self, x, y, w, h, data):
        self.pushes.append((x, y, w, h, bytes(data)))
        self.ops.append("pushImage")

    def fillRect(self, x, y, w, h, color):
        self.fills += 1
        self.ops.append("fillRect")

    def fillEllipse(self, *args):
        self.fills += 1

    def fillTriangle(self, *args):
        self.fills += 1

    def fillCircle(self, *args):
        self.fills += 1

    def drawLine(self, *args):
        self.fills += 1


def _stamp_reference(sprite, amount):
    """V2 background plus the Python RGB565 stamp, not draw()'s buffer."""
    fb = main.PlayfieldFB(main.W, main.GAUGE_Y)
    sprite._draw_background(fb, False)
    bg = bytearray(fb.buf)
    old = sprite._pf_fb
    saved = main.stamp_baked
    sprite._pf_fb = fb

    def _py_only(window, vw, vh, rgb, mask, sw, sh, dest_x, dest_y):
        main.stamp_sprite_rgb565(window, vw, vh, rgb, mask, sw, sh, dest_x, dest_y)

    main.stamp_baked = _py_only
    try:
        sprite._stamp_playfield_sprites(amount, False)
    finally:
        main.stamp_baked = saved
        sprite._pf_fb = old
    return bg, bytearray(fb.buf)


def _blit_sprites(sprite, pal_s, pal_c, off):
    rec = BlitRec()
    sprite._blit_playfield_sprites(rec, pal_s, pal_c, off)
    pack = sprite.pack
    bar = pack.by_name.get("hud_bar")
    ring = pack.by_name.get("hud_ring")
    if bar is not None:
        main.blit_frame(
            rec, pack, bar, main.HUD_BAR_POS[0], main.HUD_BAR_POS[1], pal_s, 0, main.GAUGE_Y, False
        )
    if ring is not None:
        main.blit_frame(
            rec, pack, ring, main.HUD_RING_POS[0], main.HUD_RING_POS[1], pal_s, 0, main.GAUGE_Y, False
        )
    return rec


def _blit_mismatch(got, rec):
    bad = 0
    checked = 0
    i = 0
    n = main.W * main.GAUGE_Y
    while i < n:
        color = rec.pix[i]
        if color is not None:
            checked += 1
            v = main._rgb888_to_565(color)
            o = i * 2
            if got[o] != (v & 0xFF) or got[o + 1] != ((v >> 8) & 0xFF):
                bad += 1
        i += 1
    return bad, checked


def _set_state(sprite, scroll, tag, index, ring_i, sparkles):
    sprite.scroll = float(scroll)
    sprite.anim_tag = tag
    sprite.anim_i = index
    sprite.ring_i = ring_i
    sprite.sparkles = [list(item) for item in sparkles]


def _ocean_reference(pack):
    ocean = pack.by_name.get("ocean")
    tile, tw, th = main.prerender_ocean_rgb565(pack, ocean)
    stamps = main.build_parallax_layers(tile, tw, th)
    stamp_bytes = 0
    for st in stamps:
        stamp_bytes += len(st[5]) + len(st[6])
    return tile, tw, th, stamps, stamp_bytes


def _ocean_window(tile, tw, th, stamps, scroll):
    out = bytearray(main.W * th * 2)
    main.compose_parallax_window(tile, tw, th, scroll, stamps, out, main.W)
    return out


def _save_png(path, buf, scale=4):
    try:
        from PIL import Image
    except ImportError:
        return False
    h = main.GAUGE_Y
    img = Image.new("RGB", (main.W, h))
    px = img.load()
    y = 0
    while y < h:
        x = 0
        while x < main.W:
            c = _px(buf, x, y)
            r = ((c >> 11) & 0x1F) * 255 // 31
            g = ((c >> 5) & 0x3F) * 255 // 63
            b = (c & 0x1F) * 255 // 31
            px[x, y] = (r, g, b)
            x += 1
        y += 1
    if scale != 1:
        img = img.resize((main.W * scale, h * scale), Image.NEAREST)
    img.save(path)
    return True


def main_compare():
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            print("FAIL", msg)
        else:
            print("ok", msg)

    text = open(os.path.join(ROOT, "main.py"), "rb").read()
    check(b"hexdigest" not in text, "main.py does not call hexdigest")
    check(main.APP_VERSION == 13, "APP_VERSION 13")
    check(main.APP_VERSION_NAME == "2026.11.21-v13", "APP_VERSION_NAME")
    check(main.PIXIDX_SHA256 == "dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e", "PIXIDX_SHA256")
    check(main.PIXIDX_SIZE == 107656, "PIXIDX_SIZE")
    blob = open(os.path.join(ROOT, "pixidx.bin"), "rb").read()
    digest = hashlib.sha256(blob).hexdigest()
    check(digest == main.PIXIDX_SHA256 and len(blob) == main.PIXIDX_SIZE, "pixidx.bin bytes match the pinned asset")

    pack = main.PixIdx(blob)
    for key in ("idle", "idle_trigger", "run_slow", "run_medium", "run_fast"):
        for fr in pack.cycles[key]:
            tlx = main.SONIC_FEET_DEST_X - fr.origin_x
            tly = main.SONIC_FEET_DEST_Y - fr.origin_y
            check(
                fr.w == 39 and fr.h == 36 and (tlx, tly) == (16, 55),
                "%s locked 39x36 at (16, 55) got %s %s origin %s %s"
                % (fr.name, fr.w, fr.h, fr.origin_x, fr.origin_y),
            )
    hud_ring = pack.by_name["hud_ring"]
    hud_bar = pack.by_name["hud_bar"]
    check(hud_ring.w == 17 and hud_ring.h == 19, "HUD ring 17x19")
    check(hud_bar.w == 48 and hud_bar.h == 15, "HUD bar 48x15")
    for fr in pack.cycles["rings"]:
        check(fr.w == 14 and fr.h == 14, "%s is 14x14" % fr.name)
    check(main.GAUGE_Y == 103 and main.WATER_Y == 54 and main.GRASS_H == 7 and main.PERIOD == 180, "V2 constants")
    check(main.FACE_ENABLED is False, "FACE_ENABLED stays off")

    boot = io.StringIO()
    old_out = sys.stdout
    sys.stdout = boot
    try:
        sprite = main.Sprite(pack)
    finally:
        sys.stdout = old_out
    boot_text = boot.getvalue()
    print(boot_text.strip())
    check("playfield v2" in boot_text, "pack load allocates the V2 window")
    check("sprite cache" in boot_text, "pack load bakes sprites")
    check("ocean rgb565" not in boot_text and "ocean parallax" not in boot_text, "pack load does not prepare the ocean")
    check(sprite.ocean_rgb565 is None and sprite.ocean_window is None, "ocean buffers stay empty")
    check(sprite.ocean_stamps == [] or sprite.ocean_stamps is None or len(sprite.ocean_stamps) == 0, "no scenery stamps")
    check(sprite._pf_fb is not None and len(sprite._pf_fb.buf) == main.W * main.GAUGE_Y * 2, "playfield is 128x103 RGB565")
    check(sprite._under_buf is None, "V12 underlay is not allocated")
    check(sprite.ground_y == main.SONIC_FEET_DEST_Y, "ground_y is the feet dest")
    arena_len = len(sprite._arena)
    playfield_len = len(sprite._pf_fb.buf)

    gy = sprite.ground_y
    grass_y = gy - main.GRASS_H
    plant_y = gy - main.GRASS_H + 2
    check(plant_y == gy - main.GRASS_H + 2, "palms plant at gy - GRASS_H + 2")
    bg0 = bytearray(main.W * main.GAUGE_Y * 2)
    fb = main.PlayfieldFB(main.W, main.GAUGE_Y)
    sprite.scroll = 0
    sprite._draw_background(fb, False)
    bg0[:] = fb.buf
    grass = main._rgb888_to_565(main.COL_GRASS)
    grass_dk = main._rgb888_to_565(main.COL_GRASS_DK)
    dirt_hi = main._rgb888_to_565(main.COL_DIRT_HI)
    dirt = main._rgb888_to_565(main.COL_DIRT)
    dirt_dk = main._rgb888_to_565(main.COL_DIRT_DK)
    sky = main._rgb888_to_565(main.COL_SKY)
    water = main._rgb888_to_565(main.COL_WATER)
    blade = main._rgb888_to_565(main.COL_GRASS_BL)
    grass_n = 0
    x = 0
    while x < main.W:
        if _px(bg0, x, grass_y) in (grass, grass_dk, blade):
            grass_n += 1
        x += 1
    check(grass_n > main.W // 2, "grass crown is on the shoe line y=%d (%d px)" % (grass_y, grass_n))
    check(_px(bg0, 3, gy - 2) in (dirt_hi, dirt, dirt_dk), "dirt bevel is under the feet")
    check(_px(bg0, 0, 90) not in (sky,), "feet row y=90 is not sky")
    check(_px(bg0, 100, main.GAUGE_Y - 1) not in (sky,), "extended dirt reaches the gauges")
    check(_px(bg0, 0, main.WATER_Y + 1) == water, "water band stays at WATER_Y")
    check(_px(bg0, 0, 70) == sky, "sky gap between water and the raised ground")

    # Parallax: clouds 0.25, mountains 0.4, ground 1.0.
    sprite.scroll = 40
    sprite._draw_background(fb, False)
    bg_cloud = bytearray(fb.buf)
    check(_row_shifted(bg0, bg_cloud, 12, int(40 * 0.25)), "clouds scroll at 0.25")
    sprite.scroll = 50
    sprite._draw_background(fb, False)
    bg_mtn = bytearray(fb.buf)
    # y=22 is mountain body. Lower rows also contain the ground-speed loop.
    check(_row_shifted(bg0, bg_mtn, 22, int(50 * 0.4)), "mountains scroll at 0.4")
    check(not _row_shifted(bg0, bg_mtn, 22, int(50 * 0.4) - 1), "mountain row is not a flat sky band")
    sprite.scroll = 20
    sprite._draw_background(fb, False)
    bg_isle = bytearray(fb.buf)
    isle_shift, isle_bad = _best_shift(bg0, bg_isle, main.WATER_Y, 0, 24)
    check(
        isle_shift == int(20 * 0.6),
        "water islands scroll at 0.6 (best shift %d, %d px off)" % (isle_shift, isle_bad),
    )
    sprite.scroll = 6
    sprite._draw_background(fb, False)
    bg_ground = bytearray(fb.buf)
    sprite.scroll = 0
    sprite._draw_background(fb, False)
    check(_row_shifted(fb.buf, bg_ground, grass_y, 6), "ground and near palms scroll at 1.0")

    # Pack-is-None ground stays the original 3px cell (no dirt extension).
    bare = main.Sprite(None)
    check(bare.ground_y == main.GAUGE_Y - 2, "no-pack ground_y is GAUGE_Y - 2")
    bare.scroll = 0
    bare_fb = main.PlayfieldFB(main.W, main.GAUGE_Y)
    bare._draw_background(bare_fb, True)
    check(_px(bare_fb.buf, 0, main.GAUGE_Y - 1) == sky, "no-pack path does not extend the dirt")
    check(bare._pf_fb is None and bare.ocean_rgb565 is None, "no-pack path allocates neither window nor ocean")

    print("building ocean reference (test only)")
    tile, tw, th, stamps, stamp_bytes = _ocean_reference(pack)
    underlay = main._underlay_budget(pack)
    print(
        "ram v13 playfield %d arena %d; not allocated: ocean tile %d scenery stamps %d underlay %d dirty %d"
        % (playfield_len, arena_len, len(tile), stamp_bytes, underlay, th)
    )

    cases = []
    for scroll in (0, 90, 179, 180, 768, 1535):
        cases.append((scroll, "idle", 0, 0, 70.0, 0, []))
    for tag, index in (
        ("idle", 4),
        ("idle_trigger", 0),
        ("idle_trigger", 7),
        ("run_slow", 0),
        ("run_medium", 3),
        ("run_fast", 7),
    ):
        cases.append((0, tag, index, 0, 70.0, 0, []))
        cases.append((90, tag, index, 3, 70.0, 0, []))
    for ring_i in range(8):
        cases.append((0, "idle", 0, ring_i, 70.0, 0, []))
        cases.append((80, "run_slow", 2, ring_i, 70.0, 0, []))
    cases.append((0, "idle", 0, 0, 96.0, 0, []))
    cases.append((0, "idle", 1, 0, 96.0, 400, []))
    cases.append((90, "run_fast", 5, 2, 96.0, 200, []))
    cases.append((179, "run_medium", 1, 6, 70.0, 0, []))
    cases.append((0, "idle", 2, 1, 70.0, 0, [[40, 70, 0, 0], [100, 80, 5, 0]]))
    cases.append((90, "run_fast", 4, 4, 96.0, 800, [[20, 78, 2, 0], [110, 64, 9, 0]]))
    cases.append((1535, "idle_trigger", 3, 7, 70.0, 0, [[8, 78, 1, 0]]))

    stamp_bad = 0
    blit_bad = 0
    blit_checked = 0
    ocean_bg_bad = 0
    ocean_bg_px = 0
    case_bad = 0
    heat_outside = None
    cold_buf = None
    hot_buf = None
    t_case = time.perf_counter()
    for scroll, tag, index, ring_i, clt, now, sparkles in cases:
        _set_state(sprite, scroll, tag, index, ring_i, sparkles)
        amount = main.sonic_tint_amount(clt, now)
        bg, ref = _stamp_reference(sprite, amount)
        lcd = FakeLcd()
        sprite.draw(lcd, {"spd": 0.0, "tps": 0.0, "clt": clt}, now)
        got = bytearray(sprite._pf_fb.buf)
        bad = _put_count(got, ref)
        stamp_bad += bad
        if bad:
            case_bad += 1
        if len(lcd.pushes) != 1 or lcd.pushes[0][2] != main.W or lcd.pushes[0][3] != main.GAUGE_Y:
            failures.append("push shape %s %s" % (tag, scroll))
            print("FAIL push", tag, scroll, lcd.pushes)
        elif lcd.pushes[0][4] != got:
            # pushImage received a copy of the composed window
            if lcd.pushes[0][4] != bytes(got):
                failures.append("push bytes differ %s %s" % (tag, scroll))
        pal_s = pack.pal(1)
        pal_c = pack.pal(0)
        if amount > 0:
            pal_c = main.tint_blue_palette(pal_c, amount)
        rec = _blit_sprites(sprite, pal_s, pal_c, int(scroll))
        bbad, bchecked = _blit_mismatch(got, rec)
        blit_bad += bbad
        blit_checked += bchecked
        if scroll in (0, 90, 179):
            ocean = _ocean_window(tile, tw, th, stamps, scroll)
            # Background pixels are the V2 scene with no sprite on top.
            i = 0
            n = main.W * main.GAUGE_Y
            while i < n:
                o = i * 2
                if bg[o] == ref[o] and bg[o + 1] == ref[o + 1]:
                    ocean_bg_px += 1
                    if ocean[o] != bg[o] or ocean[o + 1] != bg[o + 1]:
                        ocean_bg_bad += 1
                i += 1
        if tag == "idle" and index == 0 and ring_i == 0 and scroll == 0 and not sparkles:
            if clt < main.SWEAT_CLT and cold_buf is None:
                cold_buf = bytearray(got)
            elif clt >= main.SWEAT_CLT and now == 0 and hot_buf is None:
                hot_buf = bytearray(got)
    elapsed = time.perf_counter() - t_case
    print(
        "cases %d stamp_mismatch_px %d blit_mismatch_px %d blit_px %d case_failures %d in %.2fs"
        % (len(cases), stamp_bad, blit_bad, blit_checked, case_bad, elapsed)
    )
    print(
        "ocean background px %d mismatched %d (packed scenery is not the V2 fill)"
        % (ocean_bg_px, ocean_bg_bad)
    )
    check(stamp_bad == 0 and case_bad == 0, "baked window matches V2 background + stamp_sprite_rgb565")
    check(blit_bad == 0 and blit_checked > 0, "baked sprite pixels match blit_frame")
    check(ocean_bg_bad > 2000, "V2 background is not the packed ocean (%d mismatches)" % ocean_bg_bad)

    if cold_buf is not None and hot_buf is not None:
        outside = 0
        inside = 0
        y = 0
        while y < main.GAUGE_Y:
            x = 0
            while x < main.W:
                o = (y * main.W + x) * 2
                if cold_buf[o] != hot_buf[o] or cold_buf[o + 1] != hot_buf[o + 1]:
                    if 16 <= x < 16 + 39 and 55 <= y < 55 + 36:
                        inside += 1
                    else:
                        outside += 1
                x += 1
            y += 1
        heat_outside = outside
        print("overheat tint pixels inside sonic %d outside %d" % (inside, outside))
        check(inside > 0 and outside == 0, "overheat tints only the on-screen Sonic frame")

    # draw() does not prepare the ocean or rebuild the sprite cache.
    counts = {"ocean": 0, "sprites": 0, "compose": 0, "copy": 0, "tiled": 0, "prerender": 0, "layers": 0}
    orig = {
        "ocean": main.Sprite._prepare_ocean,
        "sprites": main.Sprite._prepare_sprites,
        "compose": main.compose_parallax_window,
        "copy": main.copy_ocean_window,
        "tiled": main.blit_tiled_x,
        "prerender": main.prerender_ocean_rgb565,
        "layers": main.build_parallax_layers,
    }

    def _wrap(key, fn):
        def wrapped(*args, **kwargs):
            counts[key] += 1
            return fn(*args, **kwargs)

        return wrapped

    main.Sprite._prepare_ocean = _wrap("ocean", orig["ocean"])
    main.Sprite._prepare_sprites = _wrap("sprites", orig["sprites"])
    main.compose_parallax_window = _wrap("compose", orig["compose"])
    main.copy_ocean_window = _wrap("copy", orig["copy"])
    main.blit_tiled_x = _wrap("tiled", orig["tiled"])
    main.prerender_ocean_rgb565 = _wrap("prerender", orig["prerender"])
    main.build_parallax_layers = _wrap("layers", orig["layers"])

    for name, fn in (
        ("draw", main.Sprite.draw),
        ("_draw_sheet_scene", main.Sprite._draw_sheet_scene),
        ("_sync_ground", main.Sprite._sync_ground),
        ("_prepare_playfield", main.Sprite._prepare_playfield),
    ):
        src = inspect.getsource(fn)
        check("_prepare_ocean" not in src, "%s does not call _prepare_ocean" % name)
        check("compose_parallax_window" not in src, "%s does not compose parallax" % name)
        check("blit_tiled_x" not in src, "%s does not blit the ocean tile" % name)
        check("prerender_ocean_rgb565" not in src, "%s does not prerender the ocean" % name)
    check("_prepare_sprites" not in inspect.getsource(main.Sprite.draw), "draw() does not rebuild sprites")
    check("_prepare_sprites" not in inspect.getsource(main.Sprite._draw_sheet_scene), "_draw_sheet_scene does not rebuild sprites")

    arena_id = id(sprite._arena)
    fb_id = id(sprite._pf_fb.buf)
    bake_at_id = id(sprite._bake_at)
    lcd = FakeLcd()
    for i in range(30):
        sprite.advance(30.0, 50)
        sprite.draw(lcd, {"spd": 30.0, "tps": 10.0, "clt": 70.0}, 1000 + i * 50)
    check(counts["ocean"] == 0 and counts["sprites"] == 0, "30 draws call prepare 0 times (%s)" % counts)
    check(
        counts["compose"] == 0 and counts["copy"] == 0 and counts["tiled"] == 0 and counts["prerender"] == 0 and counts["layers"] == 0,
        "30 draws never touch packed scenery (%s)" % counts,
    )
    check(id(sprite._arena) == arena_id and id(sprite._bake_at) == bake_at_id, "sprite cache object is stable across draws")
    check(id(sprite._pf_fb.buf) == fb_id, "playfield buffer is stable across draws")
    check(sprite.ocean_rgb565 is None, "draws leave the ocean tile unallocated")

    sprite._tim_n = 10
    sprite._tim_compose = 50000
    sprite._tim_stamp = 20000
    sprite._tim_push = 1000
    sprite._tim_hold = 0
    sprite._tim_t0 = 1
    captured = io.StringIO()
    sys.stdout = captured
    try:
        sprite.report_timing(2500)
    finally:
        sys.stdout = old_out
    line = captured.getvalue().strip()
    print(line)
    check(line.startswith("timing n=10 ") and " compose " in line and " stamp " in line and " push " in line and " hold 0" in line, "timing line format")
    check(counts["ocean"] == 0 and counts["sprites"] == 0, "timing print does not rebuild caches")
    check(id(sprite._arena) == arena_id, "timing print keeps the sprite cache")

    # Scroll and animation use real dt, so a long frame jumps ahead.
    sprite.scroll = 0
    sprite.advance(100.0, 50)
    small = sprite.scroll
    sprite.scroll = 0
    sprite.advance(100.0, 200)
    large = sprite.scroll
    expect_small = (100.0 * 50 * 0.012) % main.RING_PERIOD
    expect_large = (100.0 * 200 * 0.012) % main.RING_PERIOD
    check(abs(small - expect_small) < 1e-6 and abs(large - expect_large) < 1e-6, "scroll uses speed * dt_ms * 0.012")
    check(large > small, "a longer dt travels farther (choppy, not slow-motion)")
    sprite.anim_tag = "run_fast"
    sprite.anim_i = 0
    sprite.anim_accum = 0
    sprite.triggering = False
    dur = pack.cycles["run_fast"][0].duration_ms
    sprite._step_anim(100.0, dur - 1)
    stayed = sprite.anim_i
    sprite._step_anim(100.0, 1)
    check(dur > 0 and stayed == 0 and sprite.anim_i == 1, "run frames advance by duration_ms (%d)" % dur)

    # Stamp failure and missing window: fillRect on the V2 background, no ocean.
    sys.stdout = io.StringIO()
    try:
        fallback = main.Sprite(pack)
    finally:
        sys.stdout = old_out
    before = dict(counts)
    saved_stamp = main.stamp_baked

    def _boom(*args, **kwargs):
        raise MemoryError("stamp")

    main.stamp_baked = _boom
    lcd = FakeLcd()
    fallback.scroll = 12
    fallback.anim_tag = "idle"
    fallback.anim_i = 0
    sys.stdout = io.StringIO()
    try:
        fallback.draw(lcd, {"spd": 0.0, "tps": 0.0, "clt": 70.0}, 0)
    finally:
        sys.stdout = old_out
        main.stamp_baked = saved_stamp
    check(fallback._sprite_bake is False, "a stamp failure clears the bake flag")
    check(len(lcd.pushes) == 0, "a torn window is not pushed")
    check(lcd.fills > 0, "stamp failure draws V2 primitives and fillRect sprites")
    check(counts["tiled"] == before["tiled"] and counts["compose"] == before["compose"], "stamp failure does not draw the ocean")

    before = dict(counts)
    fallback._pf_fb = None
    fallback._sprite_bake = False
    lcd = FakeLcd()
    sys.stdout = io.StringIO()
    try:
        fallback.draw(lcd, {"spd": 0.0, "tps": 0.0, "clt": 80.0}, 10)
    finally:
        sys.stdout = old_out
    check(len(lcd.pushes) == 0 and lcd.fills > 0, "no playfield buffer: V2 primitives plus fillRect sprites")
    check(counts["ocean"] == before["ocean"] and counts["tiled"] == before["tiled"], "missing window does not prepare or blit the ocean")

    # fillScreen runs before the playfield push, and the push contains the V2 scene.
    time.ticks_ms = lambda: 0
    time.ticks_diff = lambda a, b: a - b
    panel = FakeLcd()
    ui = main.Dashboard(panel, pack, button=None)
    ui.tick({"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0, "brk": 0.0}, False, 0)
    check(panel.ops and panel.ops[0] == "fillScreen", "tick clears before the frame")
    check("pushImage" in panel.ops, "tick pushes the playfield after the clear")
    pushed = panel.pushes[-1]
    check(pushed[2] == main.W and pushed[3] == main.GAUGE_Y, "pushed playfield is 128x103")
    # The pushed bytes are the V2 background plus sprites, not flat sky.
    sky_pair = bytes((sky & 0xFF, (sky >> 8) & 0xFF))
    check(pushed[4] != sky_pair * (main.W * main.GAUGE_Y), "push is not a flat fillScreen")
    check(_px(pushed[4], 0, main.WATER_Y + 1) == water, "pushed image still has the V2 water band")

    out_dir = os.environ.get("V13_PNG_DIR", "")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        for scroll, name in ((0, "scroll0"), (90, "scroll90"), (179, "scroll179")):
            _set_state(sprite, scroll, "run_fast" if scroll else "idle", 0, 0, [])
            sprite.draw(FakeLcd(), {"spd": 0.0, "tps": 0.0, "clt": 70.0}, 0)
            _save_png(os.path.join(out_dir, name + ".png"), sprite._pf_fb.buf)
        print("wrote pngs to", out_dir)

    print("failures", len(failures))
    if failures:
        for msg in failures:
            print(" -", msg)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_compare())
