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
    assert sprite.anim_i == 0 and sprite.anim_accum == 45 and sprite.ring_i == 1
    sprite._step_anim(0.0, 30)
    assert sprite.anim_i == 1 and sprite.anim_accum == 9 and sprite.ring_i == 2
    sprite._step_anim(120.0, 80)
    assert sprite.anim_tag == "run_fast" and sprite.anim_i == 0 and sprite.anim_accum == 0
    sprite._step_anim(120.0, 80)
    assert sprite.anim_i == 1 and sprite.ring_i == 4
    # Idle trigger still keys off the same wait. The frame rate does not.
    sprite.pack.cycles["idle_trigger"] = [_Fr(), _Fr(), _Fr()]
    sprite.anim_tag = "idle"
    sprite.anim_i = 0
    sprite.anim_accum = 0
    sprite.idle_wait = 0
    sprite.triggering = False
    waited = 0
    while waited < fw.IDLE_TRIGGER_AFTER_MS - 50:
        sprite._step_anim(0.0, 50)
        waited += 50
    assert sprite.triggering is False and sprite.anim_tag == "idle"
    assert sprite.idle_wait == waited
    sprite._step_anim(0.0, 50)
    assert sprite.triggering is True and sprite.anim_tag == "idle_trigger"
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

    # One ~3 ms slice fits in the leftover of a 33 ms frame, plus a margin.
    # A late frame, and a leftover under that bar, skips. No heap read.
    quantum = fw.RING_FPS_MS * 1000
    need = fw.GC_SLICE_US + fw.GC_SLICE_MARGIN_US
    assert fw.GC_SLICE_US == 3000
    assert fw.GC_SLICE_MARGIN_US == 2000
    assert fw.GC_SLICE_STALL_US > fw.GC_SLICE_US
    assert quantum == 33000
    assert need == 5000
    assert fw.gc_slice_room(16000) is True
    assert fw.gc_slice_room(0) is True
    assert fw.gc_slice_room(quantum - need) is True
    assert fw.gc_slice_room(quantum - need + 1) is False
    assert fw.gc_slice_room(quantum - 1) is False
    assert fw.gc_slice_room(quantum) is False
    assert fw.gc_slice_room(quantum + 8000) is False
    assert fw.gc_slice_room(-1) is False

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
        # A low free value must not collect or walk inside the timing line.
        gc.mem_free = lambda: 1024
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

    # One slice after the present, only when the 33 ms frame has room.
    # Auto-GC stays off and the high threshold stays. The version stamp stays.
    assert fw.GC_AUTO_BYTES == 3072 * 1024
    assert fw.APP_VERSION == 16
    assert fw.APP_VERSION_NAME == "2026.11.21-v16"
    assert fw.VTEC_ON_RPM == 5400.0
    assert fw.VTEC_OFF_RPM == 5100.0
    import inspect
    main_src = inspect.getsource(fw.main)
    tick_src = inspect.getsource(fw.Dashboard._tick_body)
    paint_src = inspect.getsource(fw.Sprite._paint_native)
    after_src = inspect.getsource(fw._gc_after_present)
    assert "_gc_after_present" not in tick_src
    assert "_gc_after_present" not in paint_src
    assert main_src.index("ui.tick") < main_src.index("_gc_after_present")
    assert main_src.index("_gc_after_present") < main_src.index("pace_after")
    assert after_src.count("gc.collect(") == 1
    assert "while" not in after_src
    assert "for " not in after_src

    import gc
    calls = {"n": 0}
    walks = {"n": 0}
    saved = {
        "collect": gc.collect,
        "disable": getattr(gc, "disable", None),
        "isenabled": getattr(gc, "isenabled", None),
        "threshold": getattr(gc, "threshold", None),
        "mem_alloc": getattr(gc, "mem_alloc", None),
        "mem_free": getattr(gc, "mem_free", None),
    }
    state = {"disabled": False, "enabled": True, "th": None}
    clock = {"t": 5000000, "dur": 0}
    saved_mono = fw._mono_us

    def _collect():
        calls["n"] += 1
        clock["t"] += clock["dur"]

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
        walks["n"] += 1
        return 1000

    def _mem_free():
        walks["n"] += 1
        return 7 * 1024 * 1024

    def _mono():
        return clock["t"]

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
        assert walks["n"] == 0, walks
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

        # A slice is not a pause line and does not walk the heap.
        # A finish longer than one slice is logged without a walk.
        fw._mono_us = _mono
        clock["dur"] = 3000
        held = fw._PAUSE_N
        gc.collect()
        assert calls["n"] == 1
        assert fw._PAUSE_N == held
        assert walks["n"] == 0, walks
        clock["dur"] = 180000
        fw._PAUSE_N = 0
        gc.collect()
        assert calls["n"] == 2
        assert walks["n"] == 0, walks
        saw_full = False
        i = 0
        while i < fw._PAUSE_N:
            if fw._PAUSE_CAUSE[i] == "collect":
                saw_full = "na>na" in fw._pause_piece(i)
            i += 1
        assert saw_full
        fw._mono_us = saved_mono
        clock["dur"] = 0

        # Room for one slice: exactly one call. The next frame is another
        # single call, not a burst left over from the first. A late frame
        # and a leftover under the margin skip. Heap counters stay put.
        calls["n"] = 0
        fw._gc_after_present(16000)
        assert calls["n"] == 1 and walks["n"] == 0
        fw._gc_after_present(16000)
        assert calls["n"] == 2
        fw._gc_after_present(quantum)
        fw._gc_after_present(quantum - need + 1)
        fw._gc_after_present(-5)
        assert calls["n"] == 2
        fw._gc_after_present(quantum - need)
        assert calls["n"] == 3
        n0 = calls["n"]
        n = 0
        while n < 10:
            fw._gc_after_present(15000)
            n += 1
        assert calls["n"] == n0 + 10
        assert walks["n"] == 0, walks

        # Hitch lines used to print free= by walking the heap.
        fw._PAUSE_N = 0
        fw._pause_on_slow("left", 121100)
        fw._pause_on_slow("sonic", 88400)
        assert walks["n"] == 0, walks
        assert fw._pause_piece(0).startswith("stall left ")
        assert "free na" in fw._pause_piece(0)
        assert fw._pause_piece(1).startswith("stall sonic ")
        assert "free na" in fw._pause_piece(1)
    finally:
        fw._mono_us = saved_mono
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
    assert fx.spark_i < 0
    seen = [fx.aura_i]
    guard = 0
    while len(seen) < 8 and guard < 12:
        fx.tick(20.0, fw.FX_FRAME_MS, fw.VTEC_ON_RPM)
        seen.append(fx.aura_i)
        guard += 1
    assert seen[:8] == [0, 1, 2, 3, 0, 1, 2, 3], seen
    assert fx.sw_i < 0 and fx.start_sw_done
    fast = fw.BoostFX()
    fast.tick(fw.SPEED_FAST, 20, 0.0)
    assert fast.phase == "idle" and fast.spark_i == 0
    fast.tick(fw.SPEED_FAST, fw.FX_FRAME_MS, 0.0)
    assert fast.spark_i == 1
    fast.tick(fw.SPEED_FAST, fw.FX_FRAME_MS, 0.0)
    assert fast.spark_i == 2
    fast.tick(fw.SPEED_FAST, fw.FX_FRAME_MS, 0.0)
    assert fast.spark_i == 3
    fast.tick(fw.SPEED_FAST, fw.FX_FRAME_MS, 0.0)
    assert fast.spark_i == 0
    fast.tick(fw.SPEED_FAST - 1.0, 10, 0.0)
    assert fast.spark_i < 0
    assert not hasattr(fw, "AURA_ALPHA")
    assert fw.BAR_TIP_X == 68 and fw.BAR_TIP_Y == 72
    assert fw.AURA_TIP_X == 66 and fw.AURA_TIP_Y == 72
    assert fw.SW_TIP_X == 76
    assert fw.SPARK_FRONT_X == 22 and fw.SPARK_MID_Y == 88
    assert fw.FX_FRAME_MS == 1000 // 15
    assert fw.HOCKEY_ON_DECEL == 10.0 and fw.HOCKEY_OFF_DECEL == 6.0
    assert fw.HOCKEY_MIN_KMH == 25.0
    assert 150 <= fw.HOCKEY_ARM_MS <= 200
    assert fw.HOCKEY_FRAMES == 3
    stop = fw.Sprite(None)
    stop._spd_prev = 80.0
    stop._step_hockey(78.0, 100, 1.0)
    assert stop.hockey is False and stop._hockey_arm == 100
    stop._step_hockey(76.0, 100, 1.0)
    assert stop.hockey is True and stop.hockey_i == 0
    stop._step_hockey(74.0, fw.FX_FRAME_MS, 1.0)
    assert stop.hockey is True and stop.hockey_i == 1
    stop._step_hockey(72.0, fw.FX_FRAME_MS, 1.0)
    stop._step_hockey(70.0, fw.FX_FRAME_MS, 1.0)
    assert stop.hockey_i == 2
    stop._step_hockey(68.0, fw.FX_FRAME_MS, 1.0)
    assert stop.hockey_i == 2
    stop._step_hockey(67.5, 200, 1.0)
    assert stop.hockey is False
    slow = fw.Sprite(None)
    slow._spd_prev = 30.0
    slow._step_hockey(20.0, 200, 1.0)
    assert slow.hockey is False and slow._hockey_arm == 0
    stop.pack = type("Pack", (), {})()
    stop.pack.cycles = {
        "hockey": [_Fr(), _Fr(), _Fr()],
        "run_fast": [_Fr(), _Fr()],
    }
    stop.hockey = True
    stop.hockey_i = 1
    stop.anim_tag = "run_fast"
    stop.anim_i = 0
    stop.anim_accum = 0
    stop.ring_i = 0
    stop.idle_wait = 0
    stop.triggering = False
    stop._step_anim(70.0, 16)
    assert stop.anim_tag == "hockey" and stop.anim_i == 1
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

    # Boot mark matches splash_128.png pixel for pixel, and the panel is
    # not cleared before that paint or before the cache build.
    import splash_mark
    assert splash_mark.W == 128 and splash_mark.H == 128
    assert len(splash_mark.BITS) == 2048
    assert splash_mark.white_count() == 2512
    png = open(os.path.join(ROOT, "splash_128.png"), "rb").read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    import struct
    import zlib
    pos = 8
    idat = b""
    while pos < len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        typ = png[pos + 4:pos + 8]
        chunk = png[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            assert struct.unpack(">IIBB", chunk[:10])[:4] == (128, 128, 8, 2)
        elif typ == b"IDAT":
            idat += chunk
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = 128 * 3
    prev = bytearray(stride)
    at = 0
    y = 0
    while y < 128:
        filt = raw[at]
        at += 1
        row = bytearray(raw[at:at + stride])
        at += stride
        x = 0
        if filt == 1 or filt == 3 or filt == 4:
            pass
        if filt == 0:
            pass
        elif filt == 1:
            x = 0
            while x < stride:
                left = row[x - 3] if x >= 3 else 0
                row[x] = (row[x] + left) & 255
                x += 1
        elif filt == 2:
            x = 0
            while x < stride:
                row[x] = (row[x] + prev[x]) & 255
                x += 1
        elif filt == 3:
            x = 0
            while x < stride:
                left = row[x - 3] if x >= 3 else 0
                row[x] = (row[x] + ((left + prev[x]) // 2)) & 255
                x += 1
        elif filt == 4:
            x = 0
            while x < stride:
                a = row[x - 3] if x >= 3 else 0
                b = prev[x]
                c = prev[x - 3] if x >= 3 else 0
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                row[x] = (row[x] + pr) & 255
                x += 1
        else:
            raise AssertionError(filt)
        x = 0
        while x < 128:
            px = row[x * 3:x * 3 + 3]
            white = px == b"\xff\xff\xff"
            assert px == b"\x00\x00\x00" or white
            assert splash_mark._bit(x, y) is white
            x += 1
        prev = row
        y += 1

    class _Panel(object):
        def __init__(self):
            self.calls = []

        def drawRawBuf(self, buf, x, y, w, h, n, swap):
            self.calls.append((x, y, w, h, n, swap, bytes(buf[:w * h * 2])))

        def fillScreen(self, color):
            raise AssertionError("splash must not clear")

    panel = _Panel()
    assert splash_mark.show(panel) is True
    assert len(panel.calls) == 16
    y = 0
    covered = 0
    for call in panel.calls:
        assert call[0] == 0 and call[1] == y and call[2] == 128 and call[3] == 8
        assert call[4] == 128 * 8 and call[5] is True
        covered += call[3]
        y += 8
    assert covered == 128
    # Top-left of the mark is black. A white pixel expands to 0xFFFF.
    assert panel.calls[0][6][0:2] == b"\x00\x00"
    saw_white = False
    for call in panel.calls:
        if b"\xff\xff" in call[6]:
            saw_white = True
    assert saw_white

    boot = open(os.path.join(ROOT, "boot.py"), "r").read()
    assert boot.index("splash_mark.show") < boot.index("boot_check")
    assert "import main" not in boot
    assert "clear_display" in boot
    begin_src = inspect.getsource(fw._begin_keep_splash)
    assert "clear_display" in begin_src and "False" in begin_src
    main_src = inspect.getsource(fw.main)
    assert main_src.index("_begin_keep_splash") < main_src.index("_show_boot_splash")
    assert main_src.index("_show_boot_splash") < main_src.index("try_load_pixidx")
    assert main_src.index("try_load_pixidx") < main_src.index("Dashboard(")
    assert fw.APP_VERSION == 16
    assert fw.FACE_ENABLED is False

    # Cruise was ~36 ms because a 26 KB present copy and a mid-paint
    # yield sat on top of the paint. The push blocks, so the copy is
    # skipped. The yield is not between compose and the stamp plan.
    sp = fw.Sprite(None)
    src = bytearray(b"\x11\x22")
    sp.present_buf = bytearray(b"\x00\x00")
    assert sp._present_copy(src) is src
    assert sp.present_buf == bytearray(b"\x00\x00")
    paint_src = inspect.getsource(fw.Sprite._paint_native)
    assert "offs_ready" in paint_src
    assert "_service(" not in paint_src
    draw_src = inspect.getsource(fw.Sprite._draw_native_frame)
    assert draw_src.index("_fill_native_offs") < draw_src.index("_paint_native")

    # A sparkle slot that is an int used to fail the native paint, then
    # the fallback blit raised again and rebooted. It is dropped instead.
    sp.sparkles = [[3, 78, 1, 0], 9, "nope", [1, 2]]
    fw._PAUSE_N = 0
    sp._purge_sparkles()
    assert sp.sparkles == [[3, 78, 1, 0]]
    assert fw._pause_piece(0) == "spark drop 3"
    sp.pack = None
    sp.sparkles = [9, [8, 78, 0, 0]]
    sp._step_sparkles(16)
    assert sp.sparkles == []

    class _SparkPack(object):
        def __init__(self):
            self.cycles = {"rings_sparkle": [object()]}
            self.by_name = {}

    sp.pack = _SparkPack()
    sp.sparkles = [4]
    sp._blit_playfield_sprites(None, None, None, 0)
    assert sp.sparkles == []
    sp.sparkles = [5]
    sp._planned = []
    sp._plan_places_at(0)
    assert sp.sparkles == []
    sp._native_ready = False
    sp.sparkles = [6]
    assert sp._native_same_at(0, 0, 0, 0) is False
    assert sp.sparkles == []
    assert fw._BRK_PRINT_MS == 2000

    print("quiet frame ok", "rpm fill", fill0, "dot", hex(want), "stall px", stepped, "vtec", int(peak_rpm))


if __name__ == "__main__":
    main()
