"""Host check for the CS edge latch versus a 1 ms poll.

No panel and no GPIO hardware. A fake memory map checks that arming the
latch does not rewrite unrelated pin bits, and a waveform check shows a
low pulse the old sampler misses.
"""

import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_fw():
    m5 = types.ModuleType("M5")

    class Lcd(object):
        pass

    m5.Lcd = Lcd
    sys.modules["M5"] = m5
    spec = importlib.util.spec_from_file_location("fw_bus", os.path.join(ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Mem(dict):
    """Enough of machine.mem32 for the edge latch, including W1TC."""

    def __init__(self, fw):
        self.fw = fw

    def __getitem__(self, key):
        return dict.get(self, key, 0)

    def __setitem__(self, key, value):
        value &= 0xFFFFFFFF
        fw = self.fw
        if key == fw._GPIO_STATUS_W1TC:
            cur = dict.get(self, fw._GPIO_STATUS, 0)
            dict.__setitem__(self, fw._GPIO_STATUS, cur & ~value)
            return
        if key == fw._GPIO_STATUS1_W1TC:
            cur = dict.get(self, fw._GPIO_STATUS1, 0)
            dict.__setitem__(self, fw._GPIO_STATUS1, cur & ~value)
            return
        dict.__setitem__(self, key, value)


def _levels_at(times, low_from, low_to):
    out = []
    for t in times:
        if low_from <= t < low_to:
            out.append(0)
        else:
            out.append(1)
    return out


def main():
    fw = _load_fw()
    assert fw.APP_VERSION == 16

    # 80 ms CS-low while the VM cannot run. Samples only at the ends are
    # both high, which is what the 1 ms timer observed on the board.
    blocked = _levels_at([0, 80], 1, 79)
    assert fw.bus_sample_saw_low(blocked) == 0, blocked
    caught = _levels_at([0, 1, 40, 79, 80], 1, 79)
    assert fw.bus_edge_seen(caught) == 1, caught

    # A 0.2 ms low between 1 ms ticks. A real 1 ms grid misses it.
    grid = [0, 1, 2, 3, 4, 5]
    assert fw.bus_sample_saw_low(_levels_at(grid, 1.1, 1.3)) == 0
    dense = [i * 0.1 for i in range(20)]
    assert fw.bus_edge_seen(_levels_at(dense, 1.1, 1.3)) == 1
    assert fw.bus_edge_seen(_levels_at(grid, 1.1, 1.3)) == 0

    mem = Mem(fw)
    # Bits outside int_type (9:7) and int_ena (17:13).
    pad = 0x00A40020
    for pin in (fw._LCD_CS, fw._LCD_DC, fw._LCD_SCK):
        mem[fw._GPIO_PIN0 + pin * 4] = pad
    saved = []
    for pin, ena in ((fw._LCD_CS, True), (fw._LCD_DC, True), (fw._LCD_SCK, False)):
        saved.append((pin, fw._edge_arm_one(mem, pin, ena)))
    for pin, ena in ((fw._LCD_CS, True), (fw._LCD_DC, True), (fw._LCD_SCK, False)):
        word = mem[fw._GPIO_PIN0 + pin * 4]
        assert (word & ~fw._INT_TYPE_MASK & ~fw._INT_ENA_MASK) == pad, hex(word)
        assert ((word & fw._INT_TYPE_MASK) >> fw._INT_TYPE_LSB) == fw._INT_ANYEDGE
        ena_field = (word & fw._INT_ENA_MASK) >> fw._INT_ENA_LSB
        if ena:
            assert ena_field == 1, ena_field
        else:
            assert ena_field == 0, ena_field
    # Stale status must have been cleared, then a new edge sticks.
    assert fw._status_bit(mem, fw._LCD_CS) == 0
    mem[fw._GPIO_STATUS] = 1 << fw._LCD_CS
    mem[fw._GPIO_STATUS1] = 1 << (fw._LCD_DC - 32)
    assert fw._status_bit(mem, fw._LCD_CS) == 1
    assert fw._status_bit(mem, fw._LCD_DC) == 1
    assert fw._status_bit(mem, fw._LCD_SCK) == 0
    fw._edge_restore(mem, saved)
    for pin, word in saved:
        assert mem[fw._GPIO_PIN0 + pin * 4] == word
        assert fw._status_bit(mem, pin) == 0

    # An enable bit the driver already set is left alone.
    busy = pad | fw._INT_ENA_MASK
    mem[fw._GPIO_PIN0 + fw._LCD_CS * 4] = busy
    fw._edge_arm_one(mem, fw._LCD_CS, True)
    word = mem[fw._GPIO_PIN0 + fw._LCD_CS * 4]
    assert (word & fw._INT_ENA_MASK) == fw._INT_ENA_MASK, hex(word)

    print("bus edge ok")
    print("blocked 80ms poll", fw.bus_sample_saw_low(blocked), "latch", fw.bus_edge_seen(caught))
    print("short 0.2ms poll", 0, "latch", 1)


if __name__ == "__main__":
    main()
