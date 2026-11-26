"""MicroPython check: viper scenery blits match the Python loops.

Run with a unix MicroPython that has the viper emitter:
    micropython host_viper_v14.py
"""

import sys

ROOT = "."
for arg in sys.argv:
    if arg.endswith("host_viper_v14.py"):
        slash = arg.rfind("/")
        if slash >= 0:
            ROOT = arg[:slash]
        break

sys.path.insert(0, ROOT)

# Unix MicroPython has no `types` module, so the stub is a real M5.py.
# Create /tmp/stubs first if it is missing (the directory itself is not
# created here; `open` cannot mkdir).
_stub_dir = "/tmp/stubs"
try:
    _stub = open(_stub_dir + "/M5.py", "w")
except OSError:
    print("create /tmp/stubs before running this script")
    sys.exit(1)
_stub.write("class Lcd:\n    pass\n\ndef update():\n    pass\n")
_stub.close()
sys.path.insert(0, _stub_dir)

import main as fw


def mismatches(a, b):
    n = 0
    i = 0
    limit = len(a) if len(a) < len(b) else len(b)
    while i < limit:
        if a[i] != b[i]:
            n += 1
        i += 1
    return n


def main():
    kind = fw._ensure_scenery()
    print("scenery", kind)
    if kind != "viper":
        print("viper was not enabled")
        sys.exit(1)
    path = ROOT + "/pixidx.bin"
    f = open(path, "rb")
    raw = f.read()
    f.close()
    pack = fw.PixIdx(raw)
    tile, tw, th = fw.prerender_sea_rgb565(pack)
    stamps = fw.build_parallax_layers(tile, tw, th)
    print("stamps", len(stamps))
    bad = 0
    scrolls = (0, 1, 7, 50, 90, 180, 383, 384, 700, 1535)
    clips = (
        (0, 0, 128, 103),
        (0, 0, 6, 103),
        (122, 10, 6, 80),
        (40, 30, 25, 40),
        (0, 32, 128, 56),
        (8, 80, 30, 20),
        (-3, -2, 16, 12),
        (100, 0, 40, 103),
    )
    vw = fw.W
    for st in stamps:
        for scroll in scrolls:
            a = bytearray(vw * th * 2)
            b = bytearray(vw * th * 2)
            fw._blit_stamp(a, vw, tw, st, scroll)
            fw._blit_stamp_py(b, vw, tw, st, scroll)
            n = mismatches(a, b)
            if n:
                bad += n
                print("full", n, scroll)
            for clip in clips:
                # Restart from a known fill so the clip cannot hide a miss.
                i = 0
                while i < len(a):
                    a[i] = 0x5A
                    b[i] = 0x5A
                    i += 1
                fw._blit_stamp_clip(a, vw, tw, st, scroll, clip)
                fw._blit_stamp_clip_py(b, vw, tw, st, scroll, clip)
                n = mismatches(a, b)
                if n:
                    bad += n
                    print("clip", n, scroll, clip)
    print("viper mismatches", bad)
    if bad:
        sys.exit(1)


main()
