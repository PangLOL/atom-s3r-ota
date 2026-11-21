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

## Version 15 is not published

Live GitHub Pages still serve version **12**. This branch is
**2026.11.21-v15** (version **15**). It is based on the V14 packed-path
work (PR #10) plus the V15 compositor, and it targets `main` so one pull
request can carry both. V13 (PR #9) is not included. It is not an OTA
update until it is merged and the owner publishes it. Do not flash it,
do not merge it, and do not publish GitHub Pages unless you mean to.

Version 15 keeps the V14 packed ocean and scenery stamps. `pixidx.bin`
is the same file. The ocean window stays free of sprites. A band that
moves by less than a screen is shifted there, and scenery that did not
ride along is patched, matching a full compose. A second 128-wide
playfield is the buffer that is pushed. It is shifted the same way, then
only sprites that are no longer correct are copied from the clean
backdrop and stamped again: the HUD bar and ring icon while the sky
under them is still, Sonic when his frame, tint, or the backdrop under
him changes, and rings and sparkles in their own rects.

The viper scenery and sprite self-tests run while the caches are built,
not inside the first timed frame. When the playfield image is pushed,
the full-screen clear and the second canvas flush are still skipped.
Gauges still repaint y 103–127. The face button is polled through
compose, blits, flushes, and the OTA check. The OTA client is unchanged
aside from a timeout on its HTTP fetch. A machine watchdog is armed
after the boot allocations.

Every two seconds the serial line looks like:

```text
timing n=40 compose 1.2 stamp 0.8 push 3.5 fill 0.0 flush 0.0 gc 0.3 total 5.5 fps 20.0 hold 28 hud 0.0 sonic 0.4 rings 0.3 shift 38 full 1
```

The fields through `hold` are the V14 line (`total` is still compose +
stamp + push). `hud`, `sonic`, and `rings` split the stamp time. `shift`
and `full` count backdrop updates in that window. If the USB CDC has not
drained, the print is skipped and those counters stay, so a host that is
not reading cannot stall the loop. `gc` is still one `gc.collect()` on
that cadence, including while a print is waiting. Printing the line does
not rebuild the ocean or the sprite cache.

`pixidx.bin` is unchanged
(`dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e`,
107656 bytes). The device only replaces `main.py`. Device secrets are
never stored here.
