"""Pack the four reference sheets into pixidx.bin.

Sheet A, black background: sparks, sound barrier, aura, and the boot
initiate. The boot row is only the top half, so each frame is stored
flipped down to the full sprite.

Sheet B, white background: the grass surface, and two checker rows.
The rest of a taller ground band repeats those two rows.

Sheet C, light blue: the sunflower, the third frame of each tiny-flower
row, and the first palm. The loose coconut and the grass-top frame
strip are left out.

Sheet D: the first sea frame only. The later frames stay unpacked.

    python3 tools/pack_sheets.py
"""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pix6_runs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = "/home/ubuntu/.cursor/projects/agent-repos-atom-s3r-ota/assets"
SHEET_A = os.path.join(ASSETS, "7c275279-2928-4306-9ed1-4330e5adc053.jpg")
SHEET_B = os.path.join(ASSETS, "518864e0-716c-4b5f-a501-35b32f50cff7.png")
SHEET_C = os.path.join(ASSETS, "993c13dd-95d9-4d12-ae1d-3b3a3d603a9f.png")
SHEET_D = os.path.join(ASSETS, "bac3d9f4-d2fa-4aae-8e48-f7b28afb06e9.png")

COLORS = 32


def _u16(data, off):
    return data[off] | (data[off + 1] << 8)


def _u32(data, off):
    return _u16(data, off) | (_u16(data, off + 2) << 16)


def _i16(data, off):
    v = _u16(data, off)
    if v >= 32768:
        return v - 65536
    return v


def _put_u16(buf, off, value):
    buf[off] = value & 0xFF
    buf[off + 1] = (value >> 8) & 0xFF


def _put_u32(buf, off, value):
    _put_u16(buf, off, value & 0xFFFF)
    _put_u16(buf, off + 2, (value >> 16) & 0xFFFF)


def _rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _load_existing(path):
    data = open(path, "rb").read()
    if data[:4] != b"PIX6" or _u16(data, 4) != 1:
        raise SystemExit("not a PIX6 v1 pack")
    n_pal = _u16(data, 8)
    colors = _u16(data, 10)
    n_frames = _u16(data, 12)
    pal_off = _u32(data, 16)
    tab_off = _u32(data, 20)
    pix_off = _u32(data, 24)
    pals = []
    for p in range(n_pal):
        pal = []
        base = pal_off + p * colors * 2
        for i in range(colors):
            pal.append(_u16(data, base + i * 2))
        pals.append(pal)
    frames = []
    for n in range(n_frames):
        e = tab_off + n * 32
        name = bytes(data[e:e + 16]).split(b"\0", 1)[0].decode("ascii")
        off = _u32(data, e + 16)
        w = _u16(data, e + 20)
        h = _u16(data, e + 22)
        dur = _u16(data, e + 24)
        ox = _i16(data, e + 26)
        oy = _i16(data, e + 28)
        pal_id = data[e + 30]
        flags = data[e + 31]
        raw = bytes(data[pix_off + off:pix_off + off + w * h])
        frames.append({
            "name": name,
            "w": w,
            "h": h,
            "dur": dur,
            "ox": ox,
            "oy": oy,
            "pal": pal_id,
            "flags": flags,
            "idx": raw,
        })
    return pals, frames


def _quantize(im):
    """RGBA with alpha 0 transparent. Returns (w, h, index bytes, rgb565 palette)."""
    im = im.convert("RGBA")
    w, h = im.size
    src = im.load()
    opaque = []
    seen = {}
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a < 128:
                continue
            key = _rgb565(r, g, b)
            if key not in seen:
                seen[key] = (r, g, b)
                opaque.append(key)
    pal = [0]
    index_of = {0: 0}
    if len(opaque) <= COLORS - 1:
        for key in opaque:
            index_of[key] = len(pal)
            pal.append(key)
        idx = bytearray(w * h)
        for y in range(h):
            row = y * w
            for x in range(w):
                r, g, b, a = src[x, y]
                if a < 128:
                    continue
                idx[row + x] = index_of[_rgb565(r, g, b)]
        return w, h, bytes(idx), pal
    # More than 31 colors: quantize the opaque pixels, then snap to RGB565.
    rgb = Image.new("RGB", (w, h), (0, 0, 0))
    mask = im.split()[3]
    rgb.paste(im, mask=mask)
    q = rgb.quantize(colors=COLORS - 1, method=Image.MEDIANCUT, dither=Image.NONE)
    qpal = q.getpalette()
    qpx = q.load()
    used = []
    remap = {}
    idx = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            r, g, b, a = src[x, y]
            if a < 128:
                continue
            qi = qpx[x, y]
            if qi not in remap:
                cr = qpal[qi * 3]
                cg = qpal[qi * 3 + 1]
                cb = qpal[qi * 3 + 2]
                key = _rgb565(cr, cg, cb)
                if key not in index_of:
                    if len(pal) >= COLORS:
                        key = pal[1]
                    else:
                        index_of[key] = len(pal)
                        pal.append(key)
                        used.append(key)
                remap[qi] = index_of[key]
            idx[row + x] = remap[qi]
    while len(pal) < COLORS:
        pal.append(0)
    return w, h, bytes(idx), pal[:COLORS]


def _tight(im):
    bbox = im.getbbox()
    if bbox is None:
        return None
    return im.crop(bbox)


def _vfx_opaque(r, g, b):
    mx = r
    if g > mx:
        mx = g
    if b > mx:
        mx = b
    mn = r
    if g < mn:
        mn = g
    if b < mn:
        mn = b
    if mx < 40:
        return False
    # JPEG gray on the black field. Real white and cyan stay.
    if mx - mn < 18 and mx < 210:
        return False
    return True


def _mask_vfx(im):
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if _vfx_opaque(r, g, b):
                px[x, y] = (r, g, b, 255)
            else:
                px[x, y] = (0, 0, 0, 0)
    return _tight(im)


def _row_boxes(im, y0, y1, x1, gap):
    """Split a sprite row on empty columns. gap merges a hole inside one sprite."""
    px = im.load()
    cols = []
    x = 0
    while x < x1:
        n = 0
        y = y0
        while y < y1:
            if _vfx_opaque(*px[x, y][:3]):
                n += 1
            y += 1
        cols.append(n)
        x += 1
    spans = []
    inb = False
    x = 0
    while x < x1:
        if cols[x] >= 3 and not inb:
            start = x
            inb = True
        elif cols[x] < 3 and inb:
            if x - start >= 8:
                spans.append([start, x])
            inb = False
        x += 1
    if inb and x1 - start >= 8:
        spans.append([start, x1])
    merged = []
    for span in spans:
        if merged and span[0] - merged[-1][1] <= gap:
            merged[-1][1] = span[1]
        else:
            merged.append(span)
    boxes = []
    for start, end in merged:
        ys = []
        y = y0
        while y < y1:
            x = start
            hit = False
            while x < end:
                if _vfx_opaque(*px[x, y][:3]):
                    hit = True
                    break
                x += 1
            if hit:
                ys.append(y)
            y += 1
        if ys and ys[-1] - ys[0] >= 8:
            boxes.append((start, ys[0], end, ys[-1] + 1))
    return boxes


def _sheet_a():
    im = Image.open(SHEET_A).convert("RGB")
    bands = (
        ("fx_spark", 0, 45, 160, 6),
        ("fx_bar", 100, 180, 160, 6),
        ("fx_aura", 240, 320, 270, 3),
        ("fx_boot", 365, 460, 320, 2),
    )
    frames = []
    for prefix, y0, y1, x1, gap in bands:
        n = 0
        for box in _row_boxes(im, y0, y1, x1, gap):
            crop = _mask_vfx(im.crop(box))
            if crop is None:
                continue
            if prefix == "fx_boot":
                top = crop
                full = Image.new("RGBA", (top.size[0], top.size[1] * 2), (0, 0, 0, 0))
                full.paste(top, (0, 0))
                full.paste(top.transpose(Image.FLIP_TOP_BOTTOM), (0, top.size[1]))
                crop = full
            ww, hh = crop.size
            if prefix == "fx_boot":
                ox, oy = ww // 2, hh // 2
            else:
                ox, oy = ww - 1, hh // 2
            frames.append((prefix + "_%d" % n, crop, ox, oy, 66))
            n += 1
    return frames


def _sheet_b():
    im = Image.open(SHEET_B).convert("RGBA")
    px = im.load()

    def white(x, y):
        r, g, b, _a = px[x, y]
        return r > 248 and g > 248 and b > 248

    grass = im.crop((8, 6, 103, 30))
    gp = grass.load()
    gw, gh = grass.size
    for y in range(gh):
        for x in range(gw):
            r, g, b, _a = gp[x, y]
            if r > 248 and g > 248 and b > 248:
                gp[x, y] = (0, 0, 0, 0)
            else:
                gp[x, y] = (r, g, b, 255)
    # Two checker rows. The cell period is 16 px, so two rows are 32 px.
    check = im.crop((108, 21, 108 + 16 * 11, 21 + 32)).convert("RGBA")
    return [
        ("gnd_grass", grass, 0, 0, 0),
        ("gnd_check", check, 0, 0, 0),
    ]


def _sheet_c():
    im = Image.open(SHEET_C).convert("RGBA")
    frames = []
    sun = im.crop((32, 32, 88, 80))
    _drop_key(sun, (255, 0, 255))
    frames.append(("prop_sun", sun, sun.size[0] // 2, sun.size[1], 0))
    # Third frame of each tiny-flower row. Measured on this sheet.
    cells = (
        (84, 88, 108, 96),
        (68, 98, 84, 106),
        (68, 108, 84, 132),
        (68, 134, 84, 150),
    )
    for i, box in enumerate(cells):
        cell = im.crop(box)
        _drop_key(cell, (255, 0, 255))
        cell = _tight(cell)
        frames.append(("prop_fl_%d" % i, cell, cell.size[0] // 2, cell.size[1], 0))
    palm = _first_palm(im)
    frames.append(("prop_palm", palm, palm.size[0] // 2, palm.size[1], 0))
    return frames


def _drop_key(im, key, tol=40):
    px = im.load()
    w, h = im.size
    kr, kg, kb = key
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if abs(r - kr) <= tol and abs(g - kg) <= tol and abs(b - kb) <= tol:
                px[x, y] = (0, 0, 0, 0)
            else:
                px[x, y] = (r, g, b, 255)


def _first_palm(im):
    """First palm. The loose coconut sits above-left and is not included."""
    crop = im.crop((448, 88, 562, 242))
    px = crop.load()
    w, h = crop.size
    for y in range(h):
        for x in range(w):
            # Coconut occupies the top-left of this crop.
            if x < 24 and y < 22:
                px[x, y] = (0, 0, 0, 0)
                continue
            r, g, b, _a = px[x, y]
            if r > 220 and b > 220 and g < 50:
                px[x, y] = (0, 0, 0, 0)
            else:
                px[x, y] = (r, g, b, 255)
    crop = _tight(crop)
    if crop is None or crop.size[0] < 20 or crop.size[1] < 80:
        raise SystemExit("first palm was not found")
    return crop


def _sheet_d():
    im = Image.open(SHEET_D).convert("RGBA")
    # First frame, inside the pink border. Later frames are not packed.
    frame = im.crop((6, 6, 262, 256))
    return [("gh_bg_0", frame, 0, 0, 0)]


def _write(path, pals, frames):
    n_pal = len(pals)
    n_frames = len(frames)
    pal_bytes = bytearray()
    for pal in pals:
        slot = list(pal) + [0] * COLORS
        for i in range(COLORS):
            v = slot[i] & 0xFFFF
            pal_bytes.append(v & 0xFF)
            pal_bytes.append((v >> 8) & 0xFF)
    pix = bytearray()
    entries = []
    for fr in frames:
        off = len(pix)
        pix.extend(fr["idx"])
        entries.append((fr, off))
    header = bytearray(32)
    header[0:4] = b"PIX6"
    _put_u16(header, 4, 1)
    _put_u16(header, 6, 0)
    _put_u16(header, 8, n_pal)
    _put_u16(header, 10, COLORS)
    _put_u16(header, 12, n_frames)
    pal_off = 32
    tab_off = pal_off + len(pal_bytes)
    pix_off = tab_off + n_frames * 32
    _put_u32(header, 16, pal_off)
    _put_u32(header, 20, tab_off)
    _put_u32(header, 24, pix_off)
    _put_u32(header, 28, len(pix))
    table = bytearray(n_frames * 32)
    for i, (fr, off) in enumerate(entries):
        e = i * 32
        name = fr["name"].encode("ascii")
        if len(name) > 15:
            raise SystemExit("name too long: %s" % fr["name"])
        table[e:e + len(name)] = name
        _put_u32(table, e + 16, off)
        _put_u16(table, e + 20, fr["w"])
        _put_u16(table, e + 22, fr["h"])
        _put_u16(table, e + 24, fr["dur"])
        _put_u16(table, e + 26, fr["ox"] & 0xFFFF)
        _put_u16(table, e + 28, fr["oy"] & 0xFFFF)
        table[e + 30] = fr["pal"] & 0xFF
        table[e + 31] = fr["flags"] & 0xFF
    raw = bytes(header) + bytes(pal_bytes) + bytes(table) + bytes(pix)
    return pix6_runs.finalize_pix6(raw)


def main():
    src = os.path.join(ROOT, "pixidx.bin")
    pals, frames = _load_existing(src)
    fresh = []
    fresh.extend(_sheet_a())
    fresh.extend(_sheet_b())
    fresh.extend(_sheet_c())
    fresh.extend(_sheet_d())
    for name, im, ox, oy, dur in fresh:
        w, h, idx, pal = _quantize(im)
        if w < 1 or h < 1 or max(idx) == 0:
            raise SystemExit("empty frame %s" % name)
        pal_id = len(pals)
        slot = list(pal) + [0] * COLORS
        pals.append(slot[:COLORS])
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
        print("%-12s %3dx%-3d pal %d colors %d" % (
            name, w, h, pal_id, sum(1 for c in pal if c),
        ))
    out = _write(src, pals, frames)
    open(src, "wb").write(out)
    print("wrote", src, len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
