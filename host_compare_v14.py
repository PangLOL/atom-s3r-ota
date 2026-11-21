"""Host pixel compare for V14 against the V12 scenery blit and full compose.

Runs under CPython. The viper decorators are compiled on device (and by
host_viper_v14.py under MicroPython). This script checks the Python blit,
the window-shift path, and playfield frames.
"""

import hashlib
import importlib.util
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_fw():
    m5 = types.ModuleType("M5")

    class Lcd(object):
        pass

    m5.Lcd = Lcd
    sys.modules["M5"] = m5
    spec = importlib.util.spec_from_file_location("fw14", os.path.join(ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def oracle_blit(window, vw, tw, stamp, scroll):
    """V12 _blit_stamp, unchanged."""
    factor, ox, oy, sw, sh, sbuf, mask = stamp
    off = int(scroll * factor) % tw
    if off < 0:
        off += tw
    sx = ox - off
    for cand in (sx, sx - tw, sx + tw):
        if cand + sw <= 0 or cand >= vw:
            continue
        src_x0 = 0
        dst_x0 = cand
        if dst_x0 < 0:
            src_x0 = -dst_x0
            dst_x0 = 0
        src_x1 = sw
        if cand + sw > vw:
            src_x1 = vw - cand
        if src_x1 <= src_x0:
            continue
        for dy in range(sh):
            wy = oy + dy
            row_i = dy * sw
            dst_row = wy * vw
            for dx in range(src_x0, src_x1):
                di = row_i + dx
                if mask[di >> 3] & (1 << (di & 7)):
                    wx = dst_x0 + (dx - src_x0)
                    si = di * 2
                    diw = (dst_row + wx) * 2
                    window[diw] = sbuf[si]
                    window[diw + 1] = sbuf[si + 1]


def oracle_blit_clip(window, vw, tw, stamp, scroll, clip):
    """V12 _blit_stamp_clip."""
    factor, ox, oy, sw, sh, sbuf, mask = stamp
    cx, cy, cw, ch = clip
    if cw <= 0 or ch <= 0 or sw <= 0 or sh <= 0:
        return
    cright = cx + cw
    cbot = cy + ch
    off = int(scroll * factor) % tw
    if off < 0:
        off += tw
    sx = ox - off
    for cand in (sx, sx - tw, sx + tw):
        if cand + sw <= 0 or cand >= vw:
            continue
        src_x0 = 0
        dst_x0 = cand
        if dst_x0 < 0:
            src_x0 = -dst_x0
            dst_x0 = 0
        src_x1 = sw
        if cand + sw > vw:
            src_x1 = vw - cand
        if src_x1 <= src_x0:
            continue
        if dst_x0 < cx:
            shift = cx - dst_x0
            src_x0 += shift
            dst_x0 = cx
        dest_end = dst_x0 + (src_x1 - src_x0)
        if dest_end > cright:
            src_x1 -= dest_end - cright
        if src_x1 <= src_x0:
            continue
        dy0 = 0
        if oy < cy:
            dy0 = cy - oy
        dy1 = sh
        if oy + dy1 > cbot:
            dy1 = cbot - oy
        if dy0 < 0:
            dy0 = 0
        if dy1 > sh:
            dy1 = sh
        if dy0 >= dy1:
            continue
        dy = dy0
        while dy < dy1:
            wy = oy + dy
            row_i = dy * sw
            dst_row = wy * vw
            dx = src_x0
            while dx < src_x1:
                di = row_i + dx
                if mask[di >> 3] & (1 << (di & 7)):
                    wx = dst_x0 + (dx - src_x0)
                    si = di * 2
                    diw = (dst_row + wx) * 2
                    window[diw] = sbuf[si]
                    window[diw + 1] = sbuf[si + 1]
                dx += 1
            dy += 1


def oracle_compose(fw, tile, tw, th, scroll, stamps, window, vw):
    fw._copy_rows(tile, tw, 0, fw.HORIZON_Y, scroll * fw.PARALLAX_CLOUDS, window, vw, 0)
    fw._copy_rows(tile, tw, fw.HORIZON_Y, fw.GRASS_Y, scroll * fw.PARALLAX_WATER, window, vw, fw.HORIZON_Y)
    fw._copy_rows(tile, tw, fw.GRASS_Y, th, scroll * fw.PARALLAX_GROUND, window, vw, fw.GRASS_Y)
    for st in stamps:
        oracle_blit(window, vw, tw, st, scroll)


def mismatches(a, b, vw):
    n = 0
    first = None
    limit = min(len(a), len(b))
    i = 0
    while i + 1 < limit:
        if a[i] != b[i] or a[i + 1] != b[i + 1]:
            if first is None:
                pix = i // 2
                first = (pix % vw, pix // vw, a[i] | (a[i + 1] << 8), b[i] | (b[i + 1] << 8))
            n += 1
        i += 2
    if len(a) != len(b):
        n += abs(len(a) - len(b))
    return n, first


def main():
    fw = _load_fw()
    assert fw.APP_VERSION == 16, fw.APP_VERSION
    assert fw.APP_VERSION_NAME == "2026.11.21-v16"
    assert fw.FACE_ENABLED is False
    assert fw.REFRESH_MS == 50
    assert fw.DBL_TAP_MS == 450
    assert fw.SONIC_W == 39 and fw.SONIC_H == 36
    assert fw.SONIC_X == 16 and fw.SONIC_Y == 55
    assert fw.SONIC_FEET_Y == 90
    assert fw.SONIC_FEET_DEST_X == 35 and fw.SONIC_FEET_DEST_Y == 91
    assert fw.HUD_RING_POS == (4, 3)
    assert fw.HUD_BAR_POS == (14, 6)
    assert fw.W == 128 and fw.GAUGE_Y == 103
    raw = open(os.path.join(ROOT, "pixidx.bin"), "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == fw.PIXIDX_SHA256, digest
    assert len(raw) == fw.PIXIDX_SIZE == 107656
    pack = fw.PixIdx(raw)
    ocean = pack.by_name["ocean"]
    tile, tw, th = fw.prerender_ocean_rgb565(pack, ocean)
    stamps = fw.build_parallax_layers(tile, tw, th)
    print("stamps", len(stamps), "tile", tw, th)

    # Python blit matches the frozen V12 loops, including clips.
    blit_bad = 0
    scrolls = (0, 1, 7, 50, 90, 180, 383, 384, 500, 1000, 1535)
    clips = (
        (0, 0, 128, 103),
        (0, 0, 8, 103),
        (120, 0, 8, 103),
        (40, 20, 30, 40),
        (0, 32, 128, 56),
        (10, 88, 40, 15),
        (-4, -3, 20, 18),
        (200, 0, 10, 10),
    )
    for st in stamps:
        for scroll in scrolls:
            a = bytearray(fw.W * th * 2)
            b = bytearray(fw.W * th * 2)
            fw._blit_stamp_py(a, fw.W, tw, st, scroll)
            oracle_blit(b, fw.W, tw, st, scroll)
            n, first = mismatches(a, b, fw.W)
            if n:
                blit_bad += n
                print("blit mismatch", n, first, "scroll", scroll)
            for clip in clips:
                a2 = bytearray(fw.W * th * 2)
                b2 = bytearray(fw.W * th * 2)
                fw._blit_stamp_clip_py(a2, fw.W, tw, st, scroll, clip)
                oracle_blit_clip(b2, fw.W, tw, st, scroll, clip)
                # Clip must also match a full blit inside the box and leave
                # the outside untouched.
                full = bytearray(fw.W * th * 2)
                oracle_blit(full, fw.W, tw, st, scroll)
                cx, cy, cw, ch = clip
                pix_bad = 0
                y = 0
                while y < th:
                    x = 0
                    while x < fw.W:
                        inside = cx <= x < cx + cw and cy <= y < cy + ch
                        i = (y * fw.W + x) * 2
                        if inside:
                            if a2[i] != full[i] or a2[i + 1] != full[i + 1]:
                                pix_bad += 1
                        else:
                            if a2[i] != 0 or a2[i + 1] != 0:
                                pix_bad += 1
                        x += 1
                    y += 1
                n, first = mismatches(a2, b2, fw.W)
                if n or pix_bad:
                    blit_bad += n + pix_bad
                    print("clip mismatch", n, pix_bad, first, "scroll", scroll, clip)
    print("blit/clip mismatches", blit_bad)

    compose_bad = 0
    for scroll in scrolls:
        got = bytearray(fw.W * th * 2)
        exp = bytearray(fw.W * th * 2)
        fw.compose_parallax_window(tile, tw, th, scroll, stamps, got, fw.W)
        oracle_compose(fw, tile, tw, th, scroll, stamps, exp, fw.W)
        n, first = mismatches(got, exp, fw.W)
        if n:
            compose_bad += n
            print("compose mismatch", scroll, n, first)
    print("compose mismatches", compose_bad)

    # Window-shift sequence against a full V12 compose at the same scroll.
    sprite = fw.Sprite(pack)
    assert sprite._parallax
    shift_bad = 0
    seq = [0]
    s = 0
    for step in (1, 1, 2, 3, 5, 8, 13, 1, 20, 4, 40, 7, 60, 2, 90, 1, 120, 3):
        s = (s + step) % 1536
        seq.append(s)
    # One pixel each way, including the backwards wrap, then a jump wider
    # than the panel and the tile wrap at 384.
    for s in range(0, 420):
        seq.append(s)
    for s in range(419, -1, -1):
        seq.append(s)
    seq.extend([383, 384, 385, 200, 0, 1535, 1534, 10, 1000, 100])
    seen = []
    for scroll in seq:
        changed = sprite._update_backdrop(scroll)
        got = bytes(sprite.ocean_window)
        exp = bytearray(fw.W * sprite.ocean_th * 2)
        oracle_compose(
            fw,
            sprite.ocean_rgb565,
            sprite.ocean_tw,
            sprite.ocean_th,
            scroll,
            sprite.ocean_stamps,
            exp,
            fw.W,
        )
        n, first = mismatches(got, exp, fw.W)
        if n:
            shift_bad += n
            print("shift mismatch", scroll, "changed", changed, n, first)
            if shift_bad > 20:
                break
            continue
        seen.append(scroll)
        held = sprite._update_backdrop(scroll)
        if held:
            print("parked scroll reported a rewrite", scroll)
            shift_bad += 1
        if bytes(sprite.ocean_window) != got:
            print("parked scroll changed pixels", scroll)
            shift_bad += 1
    print("shift frames", len(seen), "mismatches", shift_bad)

    # Idle / run / ring frames and overheat, on top of a V12 compose.
    frame_bad = 0

    def playfield(scroll, tag, anim_i, ring_i, amount, sparkles):
        win = bytearray(fw.W * sprite.ocean_th * 2)
        oracle_compose(
            fw,
            sprite.ocean_rgb565,
            sprite.ocean_tw,
            sprite.ocean_th,
            scroll,
            sprite.ocean_stamps,
            win,
            fw.W,
        )
        # Stamp with the firmware routine into a copy, and into the sprite
        # window after a backdrop update, then compare those two.
        sprite.scroll = scroll
        sprite._compose_full(scroll)
        sprite.anim_tag = tag
        sprite.anim_i = anim_i
        sprite.ring_i = ring_i
        sprite.sparkles = [list(item) for item in sparkles]
        sprite._sprites_on = False
        sprite._stamp_playfield_sprites(amount, False)
        other = bytearray(win)
        # Re-stamp onto the oracle backdrop with the same routine by
        # borrowing the sprite's stamp against a swapped window.
        saved = sprite.ocean_window
        sprite.ocean_window = other
        sprite._stamp_playfield_sprites(amount, False)
        sprite.ocean_window = saved
        return bytes(sprite.ocean_window), bytes(other), win

    frames = pack.cycles["idle"]
    runs = pack.cycles["run_fast"]
    rings = pack.cycles["rings"]
    cases = []
    for scroll in (0, 90, 383, 384, 700):
        cases.append((scroll, "idle", 0, 0, 0.0, []))
        cases.append((scroll, "run_fast", 0, 3, 0.0, []))
        cases.append((scroll, "run_fast", 2, 7, 0.0, []))
        cases.append((scroll, "idle", 1, 1, 0.0, [(40, 70, 0, 0)]))
    heat_pair = []
    for scroll, tag, anim_i, ring_i, amount, sparks in cases:
        got, exp, _back = playfield(scroll, tag, anim_i, ring_i, amount, sparks)
        n, first = mismatches(got, exp, fw.W)
        if n:
            frame_bad += n
            print("frame mismatch", tag, anim_i, ring_i, scroll, n, first)
    cold, _, back = playfield(90, "run_medium", 1, 4, 0.0, [])
    hot, _, _ = playfield(90, "run_medium", 1, 4, 0.88, [])
    # Overheat may only recolor inside the Sonic box.
    fr = pack.cycles["run_medium"][1]
    x0 = fw.SONIC_FEET_DEST_X - fr.origin_x
    y0 = fw.SONIC_FEET_DEST_Y - fr.origin_y
    outside = 0
    inside = 0
    y = 0
    vh = sprite.ocean_th
    while y < vh:
        x = 0
        while x < fw.W:
            i = (y * fw.W + x) * 2
            diff = cold[i] != hot[i] or cold[i + 1] != hot[i + 1]
            in_box = x0 <= x < x0 + fr.w and y0 <= y < y0 + fr.h
            if diff and not in_box:
                outside += 1
            if diff and in_box:
                inside += 1
            x += 1
        y += 1
    if outside:
        frame_bad += outside
        print("overheat leaked", outside)
    if inside == 0:
        frame_bad += 1
        print("overheat changed nothing")
    # Both still match the oracle stamp path (playfield already compared).
    print(
        "frames",
        len(cases),
        "ring frames",
        len(rings),
        "idle",
        len(frames),
        "run",
        len(runs),
        "overheat inside",
        inside,
        "outside",
        outside,
        "mismatches",
        frame_bad,
    )
    # Silence unused.
    _ = back

    # Direct playfield push must not clear the panel or flush a canvas.
    if not hasattr(fw.time, "ticks_ms"):
        fw.time.ticks_ms = lambda: int(time.time() * 1000)
        fw.time.ticks_diff = lambda a, b: a - b
    sys.modules["M5"].update = lambda: None

    class Rec(object):
        def __init__(self, name):
            self.name = name
            self.ops = []

        def fillScreen(self, color):
            self.ops.append(("fill", color))

        def push(self, x, y):
            self.ops.append(("push", x, y))

        def pushImage(self, x, y, w, h, buf):
            self.ops.append(("image", x, y, w, h))

        def fillRect(self, x, y, w, h, color):
            self.ops.append(("rect", y, h))

        def fillCircle(self, x, y, r, color):
            self.ops.append(("circle", y))

        def newCanvas(self, *args, **kwargs):
            canvas = Rec("canvas")
            self.ops.append(("canvas",))
            return canvas

        def __getattr__(self, name):
            def fn(*args, **kwargs):
                self.ops.append((name,))
                return None
            return fn

    panel = Rec("panel")
    ui = fw.Dashboard(panel, pack, button=None)
    assert ui._direct_playfield()
    ui.tick({"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0}, False, fw.time.ticks_ms())
    panel_ops = [op[0] for op in panel.ops]
    assert "fill" not in panel_ops, panel_ops
    assert "push" not in panel_ops, panel_ops
    assert "image" in panel_ops, panel_ops
    gauge_rows = [op for op in panel.ops if op[0] == "rect" and op[1] >= fw.GAUGE_Y]
    assert gauge_rows, panel.ops
    # The canvas, if allocated, must not be pushed on this path.
    canvases = [op for op in panel.ops if op[0] == "canvas"]
    print("direct ops", panel_ops.count("image"), "gauge rects", len(gauge_rows), "canvas", len(canvases))

    # No pack still clears, because there is no playfield image to keep.
    bare = Rec("bare")
    ui_bare = fw.Dashboard(bare, None, button=None)
    assert not ui_bare._direct_playfield()
    ui_bare.tick({"spd": 0.0, "rpm": 0.0, "tps": 0.0, "clt": 70.0}, False, fw.time.ticks_ms())
    # Drawing target is the canvas when one was created, else the panel.
    target_ops = ui_bare.lcd.ops if ui_bare.lcd is not bare else bare.ops
    assert any(op[0] == "fill" for op in target_ops), target_ops
    print("fallback fill", True)

    polls = {"n": 0}

    def hook():
        polls["n"] += 1

    fw._BTN_HOOK = hook

    def slow():
        time.sleep(0.25)
        return ("body", None)

    ok, val = fw._call_bounded(slow, 2000)
    assert ok and val[0] == "body", (ok, val)
    assert polls["n"] >= 4, polls["n"]
    print("ota wait polls", polls["n"])

    class FakeOta(object):
        def _http_get(self, url):
            return (b"ok", None)

        def apply_update(self, doc, version_url):
            return "updated"

    fake = FakeOta()
    fw._install_ota_fetch_timeout(fake)
    assert fake._atom_http_timeout is True
    data, err = fake._http_get("http://example.test/version.json")
    assert data == b"ok" and err is None, (data, err)
    assert fake.apply_update({}, "http://example.test/version.json") == "updated"
    print("ota wrapper ok")

    total = blit_bad + compose_bad + shift_bad + frame_bad
    print("TOTAL", total)
    if total:
        sys.exit(1)


if __name__ == "__main__":
    main()
