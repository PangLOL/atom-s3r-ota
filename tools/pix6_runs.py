"""Append precomputed horizontal runs to a PIX6 pack.

Pixel indices stay where they are (format_version stays 1). Flag bit 1
marks a run table after the pixel blob. Each frame is:

    u16 nruns
    nruns * (u16 x, u8 y, u8 len, u8 index)

Index 0 is transparent and is not stored. Runs longer than 255 are split.
Frames wider than 128 (the ocean tile) store nruns 0; the firmware copies
that tile as RGB565 rows, it does not stamp it as indexed runs.

The hud_ring frame in the live pack has a one-off fix that the stock
packer does not: a white inner-left highlight and an opaque near-black
hole. Rebuilding from the C asset alone punches that highlight back to
transparent. finalize_pix6() writes the saved 17x19 index map back before
it builds runs, so a future pack cannot lose it.

    python3 tools/pix6_runs.py pixidx.bin -o pixidx.bin
"""

import argparse
import os
import sys

MAGIC = b"PIX6"
RUN_FLAG = 0x0002
MAX_RUN_W = 128

# 17x19 hud_ring indices from the on-device pack (palette 1). Index 0x1d
# is the near-white inner-left highlight. Index 0x0b is the opaque hole.
HUD_RING_W = 17
HUD_RING_H = 19
_HERE = os.path.dirname(os.path.abspath(__file__))
HUD_RING_IDX = open(os.path.join(_HERE, "hud_ring.idx"), "rb").read()


def _u16(data, off):
    return data[off] | (data[off + 1] << 8)


def _u32(data, off):
    return _u16(data, off) | (_u16(data, off + 2) << 16)


def _put_u16(buf, off, value):
    buf[off] = value & 0xFF
    buf[off + 1] = (value >> 8) & 0xFF


def frame_table(data):
    count = _u16(data, 12)
    tab = _u32(data, 20)
    pix = _u32(data, 24)
    frames = []
    for i in range(count):
        entry = tab + i * 32
        name = bytes(data[entry:entry + 16]).split(b"\0", 1)[0].decode("ascii")
        frames.append({
            "name": name,
            "off": _u32(data, entry + 16),
            "w": _u16(data, entry + 20),
            "h": _u16(data, entry + 22),
            "pix": pix,
        })
    return frames


def restore_hud_ring(data):
    """Put the saved highlight back into hud_ring. Pixels elsewhere stay."""
    raw = bytearray(data[:_u32(data, 24) + _u32(data, 28)])
    _put_u16(raw, 6, _u16(raw, 6) & ~RUN_FLAG)
    for fr in frame_table(raw):
        if fr["name"] != "hud_ring":
            continue
        if fr["w"] != HUD_RING_W or fr["h"] != HUD_RING_H:
            raise ValueError("hud_ring is %dx%d, expected 17x19" % (fr["w"], fr["h"]))
        if len(HUD_RING_IDX) != fr["w"] * fr["h"]:
            raise ValueError("saved hud_ring map has the wrong size")
        start = fr["pix"] + fr["off"]
        raw[start:start + len(HUD_RING_IDX)] = HUD_RING_IDX
        break
    return raw


def _frame_runs(data, fr):
    if fr["w"] > MAX_RUN_W or fr["w"] <= 0 or fr["h"] <= 0:
        return b""
    out = bytearray()
    base = fr["pix"] + fr["off"]
    width = fr["w"]
    for y in range(fr["h"]):
        row = base + y * width
        x = 0
        while x < width:
            idx = data[row + x]
            if idx == 0:
                x += 1
                continue
            x1 = x + 1
            limit = x + 255
            if limit > width:
                limit = width
            while x1 < limit and data[row + x1] == idx:
                x1 += 1
            ln = x1 - x
            out.append(x & 0xFF)
            out.append((x >> 8) & 0xFF)
            out.append(y & 0xFF)
            out.append(ln & 0xFF)
            out.append(idx & 0xFF)
            x = x1
    return bytes(out)


def append_runs(data):
    """data is a pixel pack with flag bit 1 clear. Returns the run-extended pack."""
    frames = frame_table(data)
    blob = bytearray()
    for fr in frames:
        runs = _frame_runs(data, fr)
        nruns = len(runs) // 5
        blob.append(nruns & 0xFF)
        blob.append((nruns >> 8) & 0xFF)
        blob.extend(runs)
    out = bytearray(data)
    _put_u16(out, 6, _u16(out, 6) | RUN_FLAG)
    out.extend(blob)
    return bytes(out)


def finalize_pix6(data):
    """Restore the HUD ring highlight, then append runs. Idempotent."""
    base = restore_hud_ring(data)
    return append_runs(base)


def main(argv):
    parser = argparse.ArgumentParser(description="Bake PIX6 horizontal runs")
    parser.add_argument("src")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    raw = open(args.src, "rb").read()
    if raw[:4] != MAGIC:
        raise SystemExit("not a PIX6 file")
    if _u16(raw, 4) != 1:
        raise SystemExit("unsupported PIX6 version")
    out = finalize_pix6(raw)
    open(args.out, "wb").write(out)
    print("wrote", args.out, len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
