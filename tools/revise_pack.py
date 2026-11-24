"""Revise the packed sheets from the review.

Closes the boot-flip seam, adds the fifth spark, replaces the ocean tile
with the first sea frame, drops the old scenery sprites, replaces Sonic
(not the hockey stop) from the clean sheet, adds the two spin-dash rows,
and uses the isolated palm.

    python3 tools/revise_pack.py
"""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pack_sheets as ps

ROOT = ps.ROOT
ASSETS = "/home/ubuntu/.cursor/projects/agent-repos-atom-s3r-ota/assets"
SONIC_SHEET = os.path.join(ASSETS, "ed3d7f69-1632-47d7-a785-d9c277eb117d.png")
PALM_SHEET = os.path.join(ASSETS, "a62747f0-1390-41c1-bcb6-bd5a1a89bbd8.png")
VFX_SHEET = os.path.join(ASSETS, "f8256b3e-c0d9-4f88-9ea0-6fce96839aaa.jpg")

DROP = {
    "ground",
    "palm_0",
    "palm_1",
    "island_0",
    "bush_0",
    "flowers_0",
    "tiny_flowers_0",
    "tiny_flowers_1",
    "tiny_flowers_2",
    "tiny_flowers_3",
    "gh_bg_0",
}

# Sheet rows, top to bottom, mapped onto the existing cycles.
SONIC_ROWS = (
    ("look", (7, 40)),
    ("idle", (48, 81)),
    ("idle_trigger", (89, 122)),
    ("run_slow", (127, 163)),
    ("run_medium", (170, 204)),
    ("run_fast", (208, 245)),
)
SPIN_ROWS = (
    (250, 271),
    (281, 304),
)
CELL_W = 39
CELL_H = 36


def _add_pal(pals, pal):
    slot = list(pal) + [0] * ps.COLORS
    pals.append(slot[:ps.COLORS])
    return len(pals) - 1


def _put(frames, pals, name, im, ox, oy, dur=0):
    w, h, idx, pal = ps._quantize(im)
    if w < 1 or h < 1 or max(idx) == 0:
        raise SystemExit("empty frame %s" % name)
    pal_id = _add_pal(pals, pal)
    frames.append({
        "name": name,
        "w": w,
        "h": h,
        "dur": dur,
        "ox": ox,
        "oy": oy,
        "pal": pal_id,
        "flags": 0,
        "idx": idx,
    })
    print("%-16s %3dx%-3d" % (name, w, h))


def _replace(frames, pals, name, im, ox, oy):
    w, h, idx, pal = ps._quantize(im)
    if w < 1 or h < 1 or max(idx) == 0:
        raise SystemExit("empty frame %s" % name)
    for fr in frames:
        if fr["name"] != name:
            continue
        fr["w"] = w
        fr["h"] = h
        fr["idx"] = idx
        fr["ox"] = ox
        fr["oy"] = oy
        fr["pal"] = _add_pal(pals, pal)
        print("replace %-12s %3dx%-3d" % (name, w, h))
        return
    raise SystemExit("missing frame %s" % name)


def _opaque_map(im):
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    rows = []
    for y in range(h):
        xs = []
        for x in range(w):
            if px[x, y][3] > 128:
                xs.append(x)
        rows.append(xs)
    return rows


def _join_no_gap(top):
    """Stack a vertical flip under the top half and close the seam hole."""
    top = top.convert("RGBA")
    bot = top.transpose(Image.FLIP_TOP_BOTTOM)
    w, h = top.size
    top_rows = _opaque_map(top)
    bot_rows = _opaque_map(bot)
    max_gap = 0
    for x in range(w):
        tb = None
        for y in range(h - 1, -1, -1):
            if x in top_rows[y]:
                tb = y
                break
        # Only columns that reach the cut. A tip at the top of the half
        # is not part of the seam and would demand a huge shift.
        if tb is None or tb < h - 4:
            continue
        bt = None
        for y in range(h):
            if x in bot_rows[y]:
                bt = y
                break
        if bt is None:
            continue
        gap = (h + bt) - tb - 1
        if gap > max_gap:
            max_gap = gap
    full_h = h + h - max_gap
    full = Image.new("RGBA", (w, full_h), (0, 0, 0, 0))
    full.paste(top, (0, 0), top)
    full.paste(bot, (0, h - max_gap), bot)
    return full, max_gap


def _rebuild_boots(frames, pals):
    im = Image.open(VFX_SHEET).convert("RGB")
    boxes = ps._row_boxes(im, 365, 460, 320, 2)
    made = []
    for box in boxes:
        crop = ps._mask_vfx(im.crop(box))
        if crop is None:
            continue
        full, shift = _join_no_gap(crop)
        made.append((full, shift))
        print("boot seam shift", shift, "size", full.size)
    # Drop the old boot frames and append the closed ones in order.
    frames[:] = [fr for fr in frames if not fr["name"].startswith("fx_boot_")]
    for i, (full, _shift) in enumerate(made):
        ww, hh = full.size
        _put(frames, pals, "fx_boot_%d" % i, full, ww // 2, hh // 2, 66)


def _add_spark5(frames, pals):
    im = Image.open(VFX_SHEET).convert("RGB")
    crop = ps._mask_vfx(im.crop((138, 0, 160, 40)))
    if crop is None:
        raise SystemExit("fifth spark was not found")
    ww, hh = crop.size
    # Replace if a previous partial add exists.
    frames[:] = [fr for fr in frames if fr["name"] != "fx_spark_4"]
    _put(frames, pals, "fx_spark_4", crop, ww - 1, hh // 2, 66)


def _replace_ocean(frames):
    bg = None
    ocean = None
    for fr in frames:
        if fr["name"] == "gh_bg_0":
            bg = fr
        elif fr["name"] == "ocean":
            ocean = fr
    if bg is None or ocean is None:
        raise SystemExit("ocean or gh_bg_0 missing")
    tw, th = 256, 103
    src0 = 70 - 32
    out = bytearray(tw * th)
    sw, sh = bg["w"], bg["h"]
    src = bg["idx"]
    for y in range(th):
        sy = src0 + y
        if sy < 0:
            sy = 0
        if sy >= sh:
            sy = sh - 1
        row = sy * sw
        for x in range(tw):
            out[y * tw + x] = src[row + (x % sw)]
    ocean["idx"] = bytes(out)
    ocean["w"] = tw
    ocean["h"] = th
    ocean["ox"] = 0
    ocean["oy"] = 0
    ocean["pal"] = bg["pal"]
    print("ocean replaced from gh_bg_0", tw, th)


def _key_magenta(im):
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 16 or (r > 220 and b > 220 and g < 60):
                px[x, y] = (0, 0, 0, 0)
            else:
                px[x, y] = (r, g, b, 255)
    return ps._tight(im)


def _replace_palm(frames, pals):
    im = _key_magenta(Image.open(PALM_SHEET))
    if im is None:
        raise SystemExit("palm empty")
    ww, hh = im.size
    _replace(frames, pals, "prop_palm", im, ww // 2, hh)


def _sheet_boxes(im, y0, y1):
    px = im.load()
    w = im.size[0]

    def ink(x, y):
        r, g, b, a = px[x, y]
        return a > 16 and not (r < 8 and g < 8 and b < 8)

    spans = []
    inb = False
    for x in range(w):
        n = 0
        y = y0
        while y < y1:
            if ink(x, y):
                n += 1
            y += 1
        if n >= 2 and not inb:
            start = x
            inb = True
        elif n < 2 and inb:
            if x - start >= 6:
                spans.append([start, x])
            inb = False
    if inb and w - start >= 6:
        spans.append([start, w])
    merged = []
    for span in spans:
        if merged and span[0] - merged[-1][1] <= 3:
            merged[-1][1] = span[1]
        else:
            merged.append(span)
    boxes = []
    for start, end in merged:
        ys = [y for y in range(y0, y1) if any(ink(x, y) for x in range(start, end))]
        if ys:
            boxes.append((start, ys[0], end, ys[-1] + 1))
    return boxes


def _fit_cell(im):
    im = ps._tight(im)
    if im is None:
        return None
    if im.size[0] > CELL_W or im.size[1] > CELL_H:
        sx = CELL_W / float(im.size[0])
        sy = CELL_H / float(im.size[1])
        s = sx if sx < sy else sy
        nw = max(1, int(round(im.size[0] * s)))
        nh = max(1, int(round(im.size[1] * s)))
        im = im.resize((nw, nh), Image.NEAREST)
    canvas = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    x = (CELL_W - im.size[0]) // 2
    y = CELL_H - im.size[1]
    canvas.paste(im, (x, y), im)
    return canvas


def _replace_sonic(frames, pals):
    im = Image.open(SONIC_SHEET).convert("RGBA")
    for key, (y0, y1) in SONIC_ROWS:
        boxes = _sheet_boxes(im, y0, y1)
        if key == "look":
            for i, box in enumerate(boxes):
                cell = _fit_cell(im.crop(box))
                _put(frames, pals, "look_%d" % i, cell, CELL_W // 2, CELL_H, 0)
            continue
        if len(boxes) != 8:
            raise SystemExit("%s has %d frames" % (key, len(boxes)))
        for i, box in enumerate(boxes):
            cell = _fit_cell(im.crop(box))
            _replace(frames, pals, "%s_%d" % (key, i), cell, CELL_W // 2, CELL_H)
    n = 0
    for y0, y1 in SPIN_ROWS:
        for box in _sheet_boxes(im, y0, y1):
            spr = ps._tight(im.crop(box))
            ww, hh = spr.size
            _put(frames, pals, "spindash_%d" % n, spr, ww // 2, hh, 66)
            n += 1
    if n != 8:
        raise SystemExit("expected 8 spin-dash frames, got %d" % n)


def main():
    src = os.path.join(ROOT, "pixidx.bin")
    pals, frames = ps._load_existing(src)
    hockey = {fr["name"]: fr["idx"] for fr in frames if fr["name"].startswith("hockey_")}
    _replace_ocean(frames)
    _rebuild_boots(frames, pals)
    _add_spark5(frames, pals)
    _replace_palm(frames, pals)
    _replace_sonic(frames, pals)
    kept = []
    for fr in frames:
        if fr["name"] in DROP:
            print("drop", fr["name"])
            continue
        kept.append(fr)
    frames = kept
    for name, idx in hockey.items():
        found = [fr for fr in frames if fr["name"] == name]
        if not found or found[0]["idx"] != idx:
            raise SystemExit("hockey frame changed: %s" % name)
    out = ps._write(src, pals, frames)
    open(src, "wb").write(out)
    print("wrote", src, len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
