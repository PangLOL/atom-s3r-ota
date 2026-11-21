"""Host checks for the gauge dirty-update and the fault dot in the buffer.

No panel. The playfield pixel compares do not stamp this dot.
"""

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
    spec = importlib.util.spec_from_file_location("fw_quiet", os.path.join(ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Panel(object):
    def __init__(self):
        self.ops = []

    def fillRect(self, x, y, w, h, color):
        self.ops.append((x, y, w, h, color))

    def pushImage(self, *args):
        self.ops.append(("image",))


def _px(buf, vw, x, y):
    i = (y * vw + x) * 2
    return buf[i] | (buf[i + 1] << 8)


def main():
    fw = _load_fw()
    assert fw.APP_VERSION == 16
    assert fw.FRAME_TRACE is False
    time.ticks_ms = lambda: 0
    time.ticks_diff = lambda a, b: a - b

    panel = Panel()
    ui = fw.Dashboard(panel, None, button=None)
    values = {"spd": 0.0, "rpm": 800.0, "tps": 0.0, "clt": 70.0}
    ui._draw_gauges(values)
    band = [
        op for op in panel.ops
        if op[1] == fw.GAUGE_Y and op[2] == fw.W and op[3] == fw.GAUGE_H
    ]
    assert len(band) == 1, band
    assert band[0][4] == 0x101012
    rpm = fw.GAUGES[0]
    fill0 = int((800.0 / (fw.RPM_MAX - fw.RPM_MIN)) * rpm[5])
    assert fill0 > 0

    panel.ops = []
    ui._draw_gauges(values)
    assert panel.ops == [], panel.ops

    values["rpm"] = 0.0
    ui._draw_gauges(values)
    assert panel.ops, "a shrink must repaint the uncovered span"
    assert not any(op[2] == fw.W and op[3] == fw.GAUGE_H for op in panel.ops)
    shrink = [op for op in panel.ops if op[0] == rpm[3] and op[4] == fw.COL_TRACK]
    assert shrink, panel.ops
    assert shrink[0][2] == fill0

    # Grow back without a band clear, and the new span uses the value color.
    values["rpm"] = 800.0
    panel.ops = []
    ui._draw_gauges(values)
    assert not any(op[2] == fw.W for op in panel.ops)
    grown = [op for op in panel.ops if op[0] == rpm[3] + 0]
    assert grown and grown[0][2] == fill0

    vw, vh = 16, 10
    sky = bytearray(vw * vh * 2)
    dot = bytearray(sky)
    fw.stamp_disk565(dot, vw, vh, fw.DOT_X, fw.DOT_Y, fw.DOT_R, fw.COL_ERR)
    want = fw._rgb888_to_565(fw.COL_ERR)
    assert _px(dot, vw, fw.DOT_X, fw.DOT_Y) == want
    assert _px(dot, vw, fw.DOT_X + fw.DOT_R, fw.DOT_Y) == want
    assert _px(dot, vw, fw.DOT_X + fw.DOT_R + 1, fw.DOT_Y) == 0
    assert _px(dot, vw, 15, 9) == 0
    clean = bytearray(sky)
    fw.stamp_disk565(clean, vw, vh, fw.DOT_X, fw.DOT_Y, fw.DOT_R, fw.COL_ERR)
    restored = bytearray(sky)
    assert restored == sky
    assert fw.draw_due(1000, None) is True
    assert fw.next_draw_deadline(1000) == 1000 + fw.RING_FPS_MS
    deadline = fw.next_draw_deadline(1000)
    assert fw.draw_due(deadline - 1, deadline) is False
    assert fw.draw_due(deadline, deadline) is True
    # A paced step is one ring quantum and is under the 50 ms motion cap.
    assert fw.anim_dt(fw.RING_FPS_MS) == fw.RING_FPS_MS
    accum = 0
    for _ in range(8):
        accum += fw.RING_FPS_MS
        steps = 0
        while accum >= fw.RING_FPS_MS:
            accum -= fw.RING_FPS_MS
            steps += 1
        assert steps == 1 and accum == 0
    # The old ~48 ms frame aliases: leftover grows into a double step.
    accum = 0
    counts = []
    for _ in range(3):
        accum += 48
        steps = 0
        while accum >= fw.RING_FPS_MS:
            accum -= fw.RING_FPS_MS
            steps += 1
        counts.append(steps)
    assert counts == [1, 1, 2], counts
    # The present path steps once and drops that leftover.
    assert fw.pace_after(1000, 1012) == 1033
    assert fw.pace_after(1000, 1050) is None
    assert fw.live_can(None, 0, 1000) is False
    assert fw.live_can(object(), 0, 1000) is False
    assert fw.live_can(object(), 1000, 2500) is True
    assert fw.live_can(object(), 1000, 1000 + fw.WATCHDOG_MS + 1) is False
    start, armed, idle = fw.auto_sweep_due(5000, 0, False, False, True, None)
    assert start is False and armed is True
    start, armed, idle = fw.auto_sweep_due(10000, 0, False, False, True, None)
    assert start is True and armed is False and idle is None
    start, armed, idle = fw.auto_sweep_due(12000, 0, True, False, True, None)
    assert start is False and armed is False and idle is None
    start, armed, idle = fw.auto_sweep_due(10000, 0, False, True, True, None)
    assert start is False and armed is True
    start, armed, idle = fw.auto_sweep_due(5000, 0, False, False, False, 1000)
    assert start is False and idle == 1000
    start, armed, idle = fw.auto_sweep_due(21000, 0, False, False, False, 1000)
    assert start is True and armed is False

    class _Fr(object):
        duration_ms = 33

    sprite = fw.Sprite(None)
    sprite.pack = type("Pack", (), {})()
    sprite.pack.cycles = {
        "idle": [_Fr(), _Fr(), _Fr()],
        "run_fast": [_Fr(), _Fr()],
    }
    sprite.anim_tag = "idle"
    sprite.anim_i = 0
    sprite.ring_i = 0
    sprite.idle_wait = 0
    sprite.triggering = False
    sprite._step_anim(0.0, 45)
    assert sprite.anim_i == 1 and sprite.ring_i == 1
    sprite._step_anim(0.0, 90)
    assert sprite.anim_i == 2 and sprite.ring_i == 2
    sprite._step_anim(120.0, 80)
    assert sprite.anim_tag == "run_fast" and sprite.anim_i == 0
    sprite._step_anim(120.0, 80)
    assert sprite.anim_i == 1 and sprite.ring_i == 4
    assert fw.anim_dt(40) == 40
    assert fw.anim_dt(50) == 50
    assert fw.anim_dt(134) == fw.REFRESH_MS
    assert fw.anim_dt(600) == fw.REFRESH_MS
    assert fw.anim_dt(-1) == fw.REFRESH_MS
    cruise = 80.0
    stalled = cruise * 134 * 0.012
    stepped = cruise * fw.anim_dt(134) * 0.012
    assert stepped == cruise * fw.REFRESH_MS * 0.012
    assert stepped < stalled

    # Healthy heap: no periodic collect at rest or in motion.
    assert fw.collect_due(2000000, 2000) is False
    assert fw.collect_due(2000000, 20000) is False
    # Under 64 KB, wait out the 15 s gap. Under 24 KB, collect now.
    assert fw.collect_due(40 * 1024, 2000) is False
    assert fw.collect_due(40 * 1024, fw.GC_MOVE_MIN_MS) is True
    assert fw.collect_due(fw.GC_CRITICAL_FREE - 1, 0) is True
    assert fw.collect_due(-1, 20000) is False

    import gc
    calls = {"n": 0}
    saved_collect = gc.collect
    saved_free = getattr(gc, "mem_free", None)
    gc.collect = lambda: calls.__setitem__("n", calls["n"] + 1)
    gc.mem_free = lambda: 2000000
    try:
        sprite = fw.Sprite(None)
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite._tim_gc_at = 0
        sprite.report_timing(3500, 80.0)
        assert calls["n"] == 0, calls
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite._tim_gc_at = 0
        sprite.report_timing(3500, 0.0)
        assert calls["n"] == 0, calls
        gc.mem_free = lambda: fw.GC_CRITICAL_FREE - 1
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite._tim_gc_at = 3500
        sprite.report_timing(3600, 0.0)
        assert calls["n"] == 1, calls
        gc.mem_free = lambda: 40 * 1024
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite._tim_gc_at = 3600
        sprite.report_timing(4000, 0.0)
        assert calls["n"] == 1, calls
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite._tim_gc_at = 0
        sprite.report_timing(3500, 80.0)
        assert calls["n"] == 2, calls
    finally:
        gc.collect = saved_collect
        if saved_free is None:
            del gc.mem_free
        else:
            gc.mem_free = saved_free

    print("quiet frame ok", "rpm fill", fill0, "dot", hex(want), "stall px", stepped)


if __name__ == "__main__":
    main()
