# Atom S3R OTA artifacts

Public payload for the M5Stack Atom S3R GE8 dashboard.

This repository is the OTA host only. It contains `version.json`,
`main.py`, and the companion asset `pixidx.bin`. It does not contain
device secrets.

The Atom polls:

```text
https://panglol.github.io/atom-s3r-ota/version.json
```

Fallback:

```text
https://raw.githubusercontent.com/PangLOL/atom-s3r-ota/main/version.json
```

`version.json` points at `main.py`. Bump `version` when publishing a
newer dashboard. The device only replaces `main.py` when the remote
version is greater than the `ota_version.txt` written at the last flash
or update. `pixidx.bin` is also hosted as a companion asset; the
firmware fetches it over Wi-Fi after boot.

`.nojekyll` keeps GitHub Pages from running Jekyll.

## Version 16 is not published

Live GitHub Pages still serve version **12**. This branch is
**2026.11.21-v16** (version **16**). It carries the unmerged V14 packed
path and the V15 compositor, and adds a native C frame. It targets
`main`. Do not merge it, do not flash it, and do not publish GitHub
Pages unless you mean to. V13 (PR #9) is not included.

The per-frame blit now runs in C: backdrop scroll, scenery stamps, Sonic,
field rings, sparkles, the boost aura / shockwave / sound barrier, and
the HUD ring digits. Python still owns CAN, the face button (BtnA / Btn
/ BtnPWR), the one-press bench sweep, animation and scroll state, the
OTA client, the watchdog, and the timing line. There is no 12–15 fps
cap. `REFRESH_MS` stays 50 and is still the cap for a bad `dt`. The draw
loop waits only `FRAME_MIN_MS` (16), so a fast frame can run at up to
60 fps.

`main.py` loads `gh_render` from the same directory. The shipped file is
a MicroPython native module for **xtensawin**, **.mpy v6.3**, built
against **MicroPython 1.27.0** (the base of UiFlow2 2.5.5). On a
matching firmware, copy these next to each other and reboot:

- `main.py`
- `gh_render.mpy`
- `pixidx.bin` (only if it is not already the V12–V15 file)

`ota_client.py` is unchanged, so a main.py-only OTA does not install
`gh_render.mpy`. If the module is missing or the firmware rejects the
.mpy, the serial line says `native render off` and the V15 Python path
runs instead. A self-test during cache build compares C against the
Python blit and drops the module on a miss (`native self-test failed`).

The C code fills the existing 128×103 playfield. After that blit
returns, Python copies the playfield into a second buffer and hands
that copy to the UiFlow2 driver. `pushImage` is tried first. Current
UiFlow2 does not export it; `drawRawBuf` does, and its length is a
pixel count. The ST7735 TE pin is not wired on the Atom S3R, and there
is no BUSY pin. Chip-select is GPIO14, active low. A loadable module
cannot reliably take over the panel pins from that driver. Gauges
(y 103–127) and the fault dot stay in Python.

The panel call holds the VM, so a 1 ms `machine.Timer` callback cannot
see CS while the bytes are moving. The handoff instead arms the GPIO
edge-status latch (not `machine.Pin`, and not the IO mux) for CS and
DC around that call. SCK's latch is armed without the CPU interrupt
bit, because any-edge on the clock would flood an existing GPIO ISR.
Each native frame also prints where the microseconds went. `yield 0`
means the native blit and `drawRawBuf` do not release the GIL, so a
Python poll cannot run inside them.

```text
gap us since 1400000 gauge 8000 loop 20000 gc 0 adv 500 paint 3000 other 1368500 gcmem -128 yield 0
bus cs 1/1 dc 1/1 sck 0/0 lo 0 edge cs 1 dc 1 sck 0
push-ms 78 raw
stage us compose 400 stamp 900 fx 200 digits 150 between 300 copy 250 pre 400 raw 78000 post 900000 held 0
```

`gap` is the time from the previous `present-ok` to this `push-in`.
`since -1` is the first frame. `other` is the part of that gap that is
not gauges, the main loop, `gc.collect`, `advance`, or this frame's
paint: serial blocking and VM pauses. `gcmem` is the change in
`gc.mem_free`. `bus` levels are before/after on GPIO input registers.
`lo` is 1 only if a Python sample saw CS low, which does not happen
while the driver holds the VM. `edge` is the hardware latch: 1 if an
edge was recorded, `na` if the registers could not be armed. `stage`
splits this frame. `stamp` there is the sprite stamps only. `pre` is
the handoff before the driver call (gap print, `push-in`, arming the
latch). `raw` is the driver call. `post` is the button poll and the
`bus` / `push-ms` prints. The two-second `timing` line is unchanged,
and its `stamp` still includes boost and the ring digits.

No wide Green Hill ribbon is allocated. Boot still uses the inpainted
ocean tile (79104 bytes), the clean window, the playfield, and the
present buffer (26368 bytes each), and the sprite cache (about 129 KB).
The native module is about 6 KB of code once loaded.

Every two seconds the serial line still looks like:

```text
timing n=40 compose 1.2 stamp 0.8 push 3.5 fill 0.0 flush 0.0 gc 0.3 total 5.5 fps 20.0 hold 28 hud 0.0 sonic 0.4 rings 0.3 shift 0 full 12
```

`total` is still compose + stamp + push. `hud`, `sonic`, and `rings`
split the stamp. If the USB CDC has not drained, the print is skipped
and those counters stay. `gc` is still one `gc.collect()` on that
cadence. While native rendering is on, `shift` stays 0: a frame that
changed is rebuilt in C rather than band-shifted. `full` counts those
rebuilds. `hold` counts frames whose scroll offsets, sprites, boost
frame, and ring count did not change. `hud` includes the ring digits.
Boost time sits in `stamp`.

Build the module from `native/` with `xtensa-esp32-elf-gcc` on `PATH`
and a MicroPython v1.27.0 tree (Espressif xtensa-esp-elf 14.2.0 provides
that compiler):

```text
make -f $MPY_DIR/py/dynruntime.mk MPY_DIR=$MPY_DIR \
  MOD=gh_render SRC="gh_mod.c gh_blit.c" ARCH=xtensawin
cp gh_render.mpy ..
```

`ARCH=x64` builds a module the unix port can import. The same
`gh_blit.c` is what `host_compare_v16.py` links for the pixel compare.
That script does not run on the device. LCD push, PSRAM, and SPI are
unchecked here.

If a firmware will not import native .mpy files, the same C can be
built in as a user module. That requires flashing a firmware image:

```text
make USER_C_MODULES=/path/to/atom-s3r-ota/native
```

Host check, from this repo:

```text
python3 host_compare_v16.py
```

`pixidx.bin` keeps the same PIX6 pixels (including the HUD ring highlight).
Flag bit 1 adds a horizontal-run table after the pixel blob so the C
stamp path does not scan indices every frame. The file is 216653 bytes,
sha256 `f33a0418a01743d405be8cee372a3ad0b8c3d72efbc8e2f0f95720468cb2c1c0`.
`tools/pix6_runs.py` rebuilds that table and writes the saved hud_ring
map back first. Device secrets are never stored here.
