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
    # Microseconds, not a 10 ms tick. A 12 ms paint counted as 10 ms
    # would leave 23 ms, and sleep_ms would round that up to 30 ms.
    q = fw.RING_FPS_MS * 1000
    assert fw.pace_remain(0, 12000) == q - 12000
    assert fw.pace_remain(0, 12000) == 21000
    assert fw.pace_remain(0, 12000) != 30000
    assert fw.pace_remain(0, 10000) == 23000
    assert fw.pace_after(0, 12000) == 33000
    # 27 ms still has leftover. 40 ms and 50 ms sleep 0.
    assert fw.pace_remain(0, 27000) == q - 27000
    assert fw.pace_after(0, 40000) is None
    assert fw.pace_remain(0, 40000) == 0
    assert fw.pace_after(0, 50000) is None
    assert fw.pace_remain(0, 50000) == 0
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
    walks = {"n": 0}

    def _free_count():
        walks["n"] += 1
        return 2000000

    gc.collect = lambda: calls.__setitem__("n", calls["n"] + 1)
    gc.mem_free = _free_count
    try:
        sprite = fw.Sprite(None)
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite.report_timing(3500, 80.0)
        assert calls["n"] == 0, calls
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite.report_timing(3500, 0.0)
        assert calls["n"] == 0, calls
        # Critical free must not collect or walk inside the timing line.
        gc.mem_free = lambda: fw.GC_CRITICAL_FREE - 1
        sprite._tim_n = 4
        sprite._tim_t0 = 1000
        sprite.report_timing(3600, 0.0)
        assert calls["n"] == 0, calls
        assert walks["n"] == 0, walks
    finally:
        gc.collect = saved_collect
        if saved_free is None:
            del gc.mem_free
        else:
            gc.mem_free = saved_free

    fw._brk_reset_all()
    fw._brk_add("m5", 12000)
    fw._brk_add("push", 11000)
    fw._brk_commit(46000)
    assert fw._BRK_SUM["m5"] == 12000
    assert fw._BRK_SUM["push"] == 11000
    assert fw._BRK_SUM["left"] == 23000
    assert fw._BRK_SUM["over"] == 0
    line = fw._brk_line()
    assert line.startswith("brk n=1 ")
    assert " wall 46.0/46.0 " in line
    assert " m5 12.0/12.0 " in line
    assert " push 11.0/11.0 " in line
    assert line.endswith("left 23.0/23.0 over 0.0/0.0")
    fw._brk_reset_all()
    fw._brk_add("compose", 4000)
    fw._brk_add("sonic", 3000)
    fw._brk_add("push", 7000)
    fw._brk_commit(46000)
    assert fw._BRK_SUM["left"] == 32000
    fw._brk_reset_all()
    fw._brk_add("push", 50000)
    fw._brk_commit(46000)
    assert fw._BRK_SUM["over"] == 4000
    assert fw._BRK_SUM["left"] == 0
    fw._brk_enter("tick")
    fw._brk_enter("m5")
    fw._brk_exit()
    fw._brk_exit()
    assert fw._BRK_NAME is None and fw._BRK_STN == 0
    fw._brk_reset_all()

    # Between-frame collect: rarer byte gap, after the present, with a
    # bounded wait for a stop or a gap between boosts.
    settled = 512 * 1024
    guess_s = 10.0 * fw.GC_BETWEEN_BYTES / settled
    assert fw.GC_BETWEEN_BYTES == 1280 * 1024
    assert 20.0 <= guess_s <= 30.0, guess_s
    assert fw.GC_CHECK_FRAMES == 30
    assert fw.GC_DEFER_MS == 10000
    assert fw.GC_AUTO_BYTES == 3072 * 1024
    assert fw.GC_AUTO_BYTES > fw.GC_BETWEEN_BYTES
    import inspect
    main_src = inspect.getsource(fw.main)
    tick_src = inspect.getsource(fw.Dashboard._tick_body)
    paint_src = inspect.getsource(fw.Sprite._paint_native)
    assert "_gc_after_present" not in tick_src
    assert "_gc_after_present" not in paint_src
    assert main_src.index("ui.tick") < main_src.index("_gc_after_present")
    assert fw.gc_moment(0.0, "run") is False
    assert fw.gc_moment(0.4, "end_sw") is False
    assert fw.gc_moment(0.0, "barrier") is False
    assert fw.gc_moment(0.0, "idle") is True
    assert fw.gc_moment(80.0, "idle") is True
    assert fw.gc_moment(100.0, "idle") is True
    assert fw.gc_moment(80.0, "run") is False
    assert fw.gc_moment(80.0, "end_sw") is False
    assert fw.gc_moment(80.0, "barrier") is False
    assert fw.gc_moment(120.0, "idle") is True
    assert fw.gc_moment(120.0, "run") is False
    assert fw.gc_moment(None, "run") is False
    assert fw.gc_moment(None, "idle") is True

    import gc
    calls = {"n": 0}
    saved = {
        "collect": gc.collect,
        "disable": getattr(gc, "disable", None),
        "isenabled": getattr(gc, "isenabled", None),
        "threshold": getattr(gc, "threshold", None),
        "mem_alloc": getattr(gc, "mem_alloc", None),
        "mem_free": getattr(gc, "mem_free", None),
    }
    state = {"disabled": False, "enabled": True, "th": None, "alloc": 0}

    def _collect():
        calls["n"] += 1

    def _disable():
        state["disabled"] = True
        state["enabled"] = False

    def _isenabled():
        return state["enabled"]

    def _threshold(*args):
        if args:
            state["th"] = args[0]
            return None
        return state["th"]

    def _mem_alloc():
        return state["alloc"]

    def _mem_free():
        return 7 * 1024 * 1024

    gc.collect = _collect
    gc.disable = _disable
    gc.isenabled = _isenabled
    gc.threshold = _threshold
    gc.mem_alloc = _mem_alloc
    gc.mem_free = _mem_free
    try:
        state["disabled"] = False
        state["enabled"] = True
        fw._PAUSE_N = 0
        fw._install_pause_gc()
        assert state["th"] == fw.GC_AUTO_BYTES
        assert state["disabled"] is True
        assert state["enabled"] is False
        # The live read is what the gcthresh line records.
        piece = None
        i = 0
        while i < fw._PAUSE_N:
            if fw._PAUSE_CAUSE[i] == "gcthresh":
                piece = fw._pause_piece(i)
            i += 1
        assert piece == "gcthresh %db" % fw.GC_AUTO_BYTES, piece
        saw_off = False
        i = 0
        while i < fw._PAUSE_N:
            if fw._PAUSE_CAUSE[i] == "gcauto" and fw._pause_piece(i) == "gcauto off":
                saw_off = True
            i += 1
        assert saw_off

        def _reset(mark, alloc):
            calls["n"] = 0
            fw._GC_ALLOC_MARK = mark
            fw._GC_CHECK_N = 0
            fw._GC_DUE_AT = None
            fw._PAUSE_N = 0
            state["alloc"] = alloc

        # Not due yet: a full check interval under the byte gap does not collect.
        _reset(1000, 1000 + fw.GC_BETWEEN_BYTES - 1)
        n = 0
        while n < fw.GC_CHECK_FRAMES:
            fw._gc_after_present(1000, 0.0, "idle")
            n += 1
        assert calls["n"] == 0, calls
        # The next presents still do not collect. The sample already ran.
        fw._gc_after_present(1100, 0.0, "idle")
        assert calls["n"] == 0

        # Crossing the gap on a stop collects once, on that sample, not every frame.
        _reset(1000, 1000 + fw.GC_BETWEEN_BYTES)
        n = 0
        while n < fw.GC_CHECK_FRAMES - 1:
            fw._gc_after_present(2000, 0.0, "idle")
            n += 1
        assert calls["n"] == 0
        fw._gc_after_present(2000, 0.0, "idle")
        assert calls["n"] == 1
        fw._gc_after_present(2016, 0.0, "idle")
        fw._gc_after_present(2032, 0.0, "idle")
        assert calls["n"] == 1

        # Mid-boost defers. A stop ends the wait. The bound collects anyway.
        _reset(0, fw.GC_BETWEEN_BYTES)
        n = 0
        while n < fw.GC_CHECK_FRAMES:
            fw._gc_after_present(5000, 140.0, "run")
            n += 1
        assert calls["n"] == 0
        assert fw._GC_DUE_AT == 5000
        saw_defer = False
        i = 0
        while i < fw._PAUSE_N:
            if fw._PAUSE_CAUSE[i] == "gcdefer":
                saw_defer = fw._pause_piece(i) == "gcdefer %dms" % fw.GC_DEFER_MS
            i += 1
        assert saw_defer
        fw._gc_after_present(5000 + fw.GC_DEFER_MS - 1, 140.0, "run")
        assert calls["n"] == 0
        fw._gc_after_present(5000 + fw.GC_DEFER_MS - 1, 80.0, "run")
        assert calls["n"] == 0, "aura still up"
        fw._gc_after_present(7000, 0.0, "run")
        assert calls["n"] == 0, "end sequence still playing"
        fw._gc_after_present(7000, 0.0, "idle")
        assert calls["n"] == 1
        assert fw._GC_DUE_AT is None

        _reset(0, fw.GC_BETWEEN_BYTES + 50)
        n = 0
        while n < fw.GC_CHECK_FRAMES:
            fw._gc_after_present(8000, 130.0, "run")
            n += 1
        assert calls["n"] == 0
        fw._gc_after_present(8000 + fw.GC_DEFER_MS, 130.0, "run")
        assert calls["n"] == 1
        saw_wait = False
        i = 0
        while i < fw._PAUSE_N:
            if fw._PAUSE_CAUSE[i] == "gcwait":
                saw_wait = True
            i += 1
        assert saw_wait

        # Cruise after the aura sequence is a moment, so the sample collects.
        _reset(0, fw.GC_BETWEEN_BYTES + 8)
        n = 0
        while n < fw.GC_CHECK_FRAMES:
            fw._gc_after_present(9000, 80.0, "idle")
            n += 1
        assert calls["n"] == 1

        # A cruise above 100 km/h is a gap once the aura is idle.
        # Stopped mid-barrier is not, until the defer cap.
        _reset(0, fw.GC_BETWEEN_BYTES + 8)
        n = 0
        while n < fw.GC_CHECK_FRAMES:
            fw._gc_after_present(12000, 160.0, "idle")
            n += 1
        assert calls["n"] == 1
        _reset(0, fw.GC_BETWEEN_BYTES + 8)
        n = 0
        while n < fw.GC_CHECK_FRAMES:
            fw._gc_after_present(13000, 0.0, "barrier")
            n += 1
        assert calls["n"] == 0
        assert fw._GC_DUE_AT == 13000
        fw._gc_after_present(13000 + fw.GC_DEFER_MS - 1, 0.0, "barrier")
        assert calls["n"] == 0
        fw._gc_after_present(13000 + fw.GC_DEFER_MS, 0.0, "barrier")
        assert calls["n"] == 1

        # Low-free still collects after the present, not on the timing line.
        _reset(0, 0)
        fw._GC_FREE_AT = None
        fw._GC_COLLECT_AT = None
        state["alloc"] = 0

        def _crit_free():
            return fw.GC_CRITICAL_FREE - 1

        gc.mem_free = _crit_free
        fw._gc_after_present(14000, 0.0, "run")
        assert calls["n"] == 1, "critical free collects during the aura"
        fw._gc_after_present(14100, 0.0, "run")
        assert calls["n"] == 1, "critical does not collect every frame"
        gc.mem_free = _mem_free
    finally:
        gc.collect = saved["collect"]
        if saved["disable"] is None:
            del gc.disable
        else:
            gc.disable = saved["disable"]
        if saved["isenabled"] is None:
            del gc.isenabled
        else:
            gc.isenabled = saved["isenabled"]
        if saved["threshold"] is None:
            del gc.threshold
        else:
            gc.threshold = saved["threshold"]
        if saved["mem_alloc"] is None:
            del gc.mem_alloc
        else:
            gc.mem_alloc = saved["mem_alloc"]
        if saved["mem_free"] is None:
            del gc.mem_free
        else:
            gc.mem_free = saved["mem_free"]
        try:
            gc.enable()
        except Exception:
            pass

    # A remembered driver is called on the next present. No second probe.
    fw._PUSH_HOW = None

    class _Raw(object):
        def __init__(self):
            self.n = 0
            self.image = 0

        def drawRawBuf(self, *args):
            self.n += 1

    raw = _Raw()
    buf = bytearray(8)
    assert fw.push_rgb565_window(raw, buf, 2, 2) is True
    assert raw.n == 1 and fw._PUSH_HOW == "raw"
    raw.pushImage = lambda *args: (_ for _ in ()).throw(AssertionError("image"))
    assert fw.push_rgb565_window(raw, buf, 2, 2) is True
    assert raw.n == 2

    fw._PUSH_HOW = None

    class _Image(object):
        def __init__(self):
            self.how = None

        def pushImage(self, *args):
            self.how = "image"

        def drawRawBuf(self, *args):
            raise AssertionError("raw")

    img = _Image()
    assert fw.push_rgb565_window(img, buf, 2, 2) is True
    assert img.how == "image" and fw._PUSH_HOW == "image"
    fw._PUSH_HOW = None

    # Aura follows CAN rpm (VTEC), not 100 km/h. Light throttle is accepted.
    assert fw.VTEC_ON_RPM == 5400.0
    assert fw.VTEC_OFF_RPM < fw.VTEC_ON_RPM
    assert fw.VTEC_ON_RPM - fw.VTEC_OFF_RPM <= 400.0
    fx = fw.BoostFX()
    fx.tick(130.0, 2000, 4000.0)
    assert fx.phase == "idle" and fx.aura_i < 0
    fx.tick(100.0, fw.BOOST_END_MS, 5399.0)
    assert fx.phase == "idle", "100 km/h under 5400 rpm does not start the aura"
    fx.tick(20.0, 50, fw.VTEC_ON_RPM)
    assert fx.phase == "run" and fx.aura_i == 0 and fx.initiated
    fx.tick(20.0, 800, fw.VTEC_OFF_RPM)
    assert fx.phase == "run" and fx.below_ms == 0
    fx.tick(20.0, fw.BOOST_END_MS - 1, fw.VTEC_OFF_RPM - 1)
    assert fx.phase == "run"
    fx.tick(20.0, 1, fw.VTEC_OFF_RPM - 1)
    assert fx.phase == "end_sw" and fx.aura_i < 0
    fx.tick(10.0, 50, fw.VTEC_ON_RPM)
    assert fx.phase == "run" and fx.aura_i == 0
    fx.tick(0.0, fw.BOOST_END_MS, 800.0)
    assert fx.phase == "end_sw"
    guard = 0
    while fx.phase != "idle" and guard < 40:
        fx.tick(0.0, fw.FX_FRAME_MS, 800.0)
        guard += 1
    assert fx.phase == "idle" and fx.aura_i < 0 and not fx.initiated, fx.phase

    sw = fw.Sweep()
    hold = sw._values(120.0, "boost_hold")
    assert hold["spd"] == 120.0
    assert hold["tps"] == 220.0 and hold["clt"] == 86.0
    assert hold["rpm"] >= fw.VTEC_ON_RPM + 400.0
    accel = sw._values(120.0, "accel")
    assert accel["tps"] == 50.0 + 1.7 * 120.0
    assert accel["clt"] == 78.0 + 0.24 * 120.0
    assert accel["rpm"] >= fw.VTEC_ON_RPM
    under = sw._values(100.0, "accel")
    assert under["spd"] == 100.0 and under["rpm"] < fw.VTEC_ON_RPM
    settle = sw._values(120.0, "settle")
    assert settle["rpm"] < fw.VTEC_OFF_RPM
    assert settle["clt"] == 96.0
    assert sum(stage[0] for stage in fw.Sweep.STAGES) == 9900

    sw.start(0)
    fx = fw.BoostFX()
    t = 0
    saw_on = False
    saw_fast_under = False
    peak_rpm = 0.0
    peak_spd = 0.0
    while sw.active and t <= 12000:
        vals = sw.step(t)
        assert vals is not None
        if vals["rpm"] > peak_rpm:
            peak_rpm = vals["rpm"]
        if vals["spd"] > peak_spd:
            peak_spd = vals["spd"]
        if vals["spd"] >= 100.0 and vals["rpm"] < fw.VTEC_ON_RPM:
            saw_fast_under = True
            if not saw_on:
                assert fx.phase == "idle"
        if vals["rpm"] >= fw.VTEC_ON_RPM:
            saw_on = True
        fx.tick(vals["spd"], 50, vals["rpm"])
        if saw_on and fx.phase == "run":
            pass
        t += 50
    assert not sw.active
    assert sw.step(t) is None
    assert saw_fast_under
    assert saw_on and peak_rpm >= fw.VTEC_ON_RPM + 400.0
    assert peak_spd == 120.0
    guard = 0
    while fx.phase != "idle" and guard < 80:
        fx.tick(0.0, fw.FX_FRAME_MS, 800.0)
        guard += 1
    assert fx.phase == "idle" and fx.aura_i < 0, fx.phase

    # A present must not walk the heap unless frame tracing is on.
    import gc
    walks = {"n": 0}
    saved_free = getattr(gc, "mem_free", None)

    def _count_free():
        walks["n"] += 1
        return 1234

    gc.mem_free = _count_free
    try:
        assert fw.FRAME_TRACE is False
        fw._note_presented()
        fw._note_presented()
        assert walks["n"] == 0, walks
        fw.FRAME_TRACE = True
        fw._note_presented()
        assert walks["n"] == 1, walks
    finally:
        fw.FRAME_TRACE = False
        if saved_free is None:
            del gc.mem_free
        else:
            gc.mem_free = saved_free

    print("quiet frame ok", "rpm fill", fill0, "dot", hex(want), "stall px", stepped, "vtec", int(peak_rpm))


if __name__ == "__main__":
    main()
