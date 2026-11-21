"""Host pixel compare for V15 against a full V14 compose plus stamp.

Runs under CPython. The incremental playfield (clean backdrop, shift,
dirty sprite repair) must match composing the ocean from scratch and
stamping every sprite with the V14 routine. The clean ocean window must
stay free of sprites.
"""

import hashlib
import os
import sys

import host_compare_v14 as v14

ROOT = os.path.dirname(os.path.abspath(__file__))


def _set_state(sprite, scroll, tag, anim_i, ring_i, amount_unused, sparkles):
    sprite.scroll = float(scroll)
    sprite.anim_tag = tag
    sprite.anim_i = anim_i
    sprite.ring_i = ring_i
    sprite.sparkles = [list(item) for item in sparkles]


def _oracle(fw, sprite, amount):
    """V14 picture: full compose, then the unchanged stamp routine."""
    th = sprite.ocean_th
    back = bytearray(fw.W * th * 2)
    v14.oracle_compose(
        fw,
        sprite.ocean_rgb565,
        sprite.ocean_tw,
        th,
        sprite.scroll,
        sprite.ocean_stamps,
        back,
        fw.W,
    )
    play = bytearray(back)
    sprite._stamp_playfield_sprites(amount, False, play)
    return bytes(back), bytes(play)


def _check(fw, sprite, amount, label):
    sprite._update_backdrop(sprite.scroll)
    sprite._sync_playfield(amount)
    back, play = _oracle(fw, sprite, amount)
    n_back, first_back = v14.mismatches(sprite.ocean_window, back, fw.W)
    n_play, first_play = v14.mismatches(sprite.play_window, play, fw.W)
    if n_back or n_play:
        print(
            "mismatch",
            label,
            "scroll",
            sprite.scroll,
            "tag",
            sprite.anim_tag,
            sprite.anim_i,
            "ring",
            sprite.ring_i,
            "mode",
            sprite._bd_mode,
            "back",
            n_back,
            first_back,
            "play",
            n_play,
            first_play,
            "deltas",
            list(sprite._band_delta),
            "modes",
            list(sprite._band_mode),
        )
    return n_back + n_play


def _timing_line(fw, sprite):
    """A cruising sample keeps the V14 prefix and does not print into a full CDC."""
    import sys as _sys

    class Out(object):
        def __init__(self):
            self.lines = []
            self.ready = True

        def write(self, data):
            self.lines.append(data)
            return len(data)

        def flush(self):
            return None

        def ioctl(self, cmd, flags):
            if cmd == 3 and self.ready:
                return 4
            return 0

    out = Out()
    old = _sys.stdout
    _sys.stdout = out
    fw._STDOUT_POLL = 0
    try:
        if not fw._stdout_can_write():
            print("poll did not report writable", file=old)
            return 1
        out.ready = False
        if fw._stdout_can_write():
            print("poll stayed writable while the buffer was full", file=old)
            return 1
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite._tim_compose = 4000
        sprite._tim_stamp = 800
        sprite._tim_push = 200
        sprite._tim_fill = 0
        sprite._tim_flush = 0
        sprite._tim_hold = 1
        sprite._tim_hud = 0
        sprite._tim_sonic = 400
        sprite._tim_rings = 400
        sprite._tim_shift = 3
        sprite._tim_full = 1
        sprite._tim_gc_at = 0
        sprite.report_timing(3500)
        if sprite._tim_n != 4:
            print("skipped print cleared the counters", sprite._tim_n, file=old)
            return 1
        if any("timing" in part for part in out.lines):
            print("printed while CDC was full", out.lines, file=old)
            return 1
        out.ready = True
        sprite.report_timing(3600)
        text = "".join(out.lines)
        if "timing n=4 compose " not in text:
            print("missing v14 prefix", text, file=old)
            return 1
        if " hud " not in text or " sonic " not in text or " rings " not in text:
            print("missing stamp split", text, file=old)
            return 1
        if " shift 3 full 1" not in text:
            print("missing shift/full", text, file=old)
            return 1
        if sprite._tim_n != 0:
            print("print did not reset counters", file=old)
            return 1
        shown = text.strip()
    finally:
        _sys.stdout = old
        fw._STDOUT_POLL = 0
    print("timing line", shown)
    return 0


def main():
    fw = v14._load_fw()
    assert fw.APP_VERSION == 16
    assert fw.APP_VERSION_NAME == "2026.11.21-v16"
    assert fw.FACE_ENABLED is False
    assert fw.REFRESH_MS == 50
    raw = open(os.path.join(ROOT, "pixidx.bin"), "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == fw.PIXIDX_SHA256
    assert len(raw) == fw.PIXIDX_SIZE == 107656
    pack = fw.PixIdx(raw)
    # Cache prepare runs the viper self-test. Kind is set before any frame.
    assert fw._SCENERY_KIND is None or fw._SCENERY_KIND in ("py", "viper")
    sprite = fw.Sprite(pack)
    assert sprite.play_window is not None
    assert sprite._parallax
    assert fw._SCENERY_KIND in ("py", "viper")
    assert fw._STAMP_KIND in ("py", "viper")
    print("prepare", fw._SCENERY_KIND, fw._STAMP_KIND, "play", len(sprite.play_window))

    calls = {"n": 0}
    orig = fw._ensure_scenery

    def _counting():
        calls["n"] += 1
        return orig()

    fw._ensure_scenery = _counting
    bad = 0
    frames = 0
    hud_stamps = 0
    sonic_stamps = 0
    ring_stamps = 0
    runs = pack.cycles["run_fast"]
    n_run = len(runs)

    def step(scroll, tag, anim_i, ring_i, amount, sparkles, label):
        nonlocal frames, bad, hud_stamps, sonic_stamps, ring_stamps
        _set_state(sprite, scroll, tag, anim_i, ring_i, amount, sparkles)
        bad += _check(fw, sprite, amount, label)
        frames += 1
        if sprite._part_hud:
            hud_stamps += 1
        if sprite._part_sonic:
            sonic_stamps += 1
        if sprite._part_rings:
            ring_stamps += 1

    # Parked, then one pixel at a time through a sky step and a water step.
    step(0, "idle", 0, 0, 0.0, [], "boot")
    parked_play = bytes(sprite.play_window)
    step(0, "idle", 0, 0, 0.0, [], "parked")
    if bytes(sprite.play_window) != parked_play:
        print("parked frame changed pixels")
        bad += 1
    if sprite._part_hud or sprite._part_sonic or sprite._part_rings:
        print("parked frame restamped", sprite._part_hud, sprite._part_sonic, sprite._part_rings)
        bad += 1

    scroll = 0
    for i in range(1, 97):
        scroll = i
        step(
            scroll,
            "run_fast",
            i % n_run,
            i % 8,
            0.0,
            [],
            "cruise",
        )
    # Sky offset is int(scroll * 0.25). It stays 0 for scroll 0 and 1.
    # Re-check that specific pair explicitly from a fresh parked HUD.
    step(0, "run_fast", 0, 0, 0.0, [], "sky-base")
    step(1, "run_fast", 0, 0, 0.0, [], "sky-still")
    if sprite._bd_mode != 1:
        print("expected a shift on a 1px scroll", sprite._bd_mode)
        bad += 1
    if sprite._band_delta[0] != 0:
        print("sky moved on a 1px step", sprite._band_delta[0])
        bad += 1
    if sprite._part_hud:
        print("HUD restamped while the sky band was still")
        bad += 1
    if not sprite._part_sonic:
        print("Sonic was not restamped when the ground moved")
        bad += 1

    # Backwards, including the tile wrap, then jumps wider than the panel.
    for scroll in range(96, -1, -1):
        step(scroll, "run_medium", scroll % n_run, (scroll * 3) % 8, 0.0, [], "back")
    for scroll in (0, 200, 383, 384, 385, 700, 1000, 1535, 10, 10, 11, 90):
        step(scroll, "run_slow", 1, 4, 0.0, [], "jump")

    # Sparkles over Sonic, then they expire. Tint on and off.
    step(90, "run_medium", 1, 4, 0.0, [(40, 70, 0, 0)], "spark-on")
    step(91, "run_medium", 2, 5, 0.0, [(40, 70, 1, 0), (20, 78, 0, 0)], "spark-two")
    step(92, "run_medium", 2, 5, 0.88, [(40, 70, 2, 0)], "heat")
    step(92, "run_medium", 2, 5, 0.4, [(40, 70, 2, 0)], "heat-change")
    step(92, "run_medium", 2, 5, 0.0, [], "spark-off")
    step(92, "idle", 0, 0, 0.0, [], "idle-again")
    step(92, "idle", 0, 0, 0.0, [], "idle-hold")
    # Float scroll, the way the device advances: bands and rings use int().
    step(10.2, "run_fast", 0, 1, 0.0, [], "frac-a")
    step(10.8, "run_fast", 0, 1, 0.0, [], "frac-hold")
    if sprite._bd_mode != 0 or sprite._part_sonic or sprite._part_rings or sprite._part_hud:
        print("fractional hold rebuilt the frame", sprite._bd_mode, sprite._part_sonic)
        bad += 1
    step(11.2, "run_fast", 1, 1, 0.0, [], "frac-step")
    step(40.0, "run_fast", 0, 2, 0.0, [], "anim-a")
    step(40.0, "run_fast", 1, 2, 0.0, [], "anim-b")
    step(40.0, "run_fast", 1, 3, 0.0, [], "ring-only")

    if calls["n"]:
        print("scenery self-test ran during compose", calls["n"])
        bad += 1
    fw._ensure_scenery = orig

    bad += _timing_line(fw, sprite)
    print(
        "frames",
        frames,
        "hud stamps",
        hud_stamps,
        "sonic stamps",
        sonic_stamps,
        "ring stamps",
        ring_stamps,
        "mismatches",
        bad,
    )
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
