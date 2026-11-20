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

## Version 14 is not published

Live GitHub Pages still serve version **12**. This branch is
**2026.11.21-v14** (version **14**). It is not an OTA update until it is
merged and the owner publishes it. Do not flash it from this branch
unless you mean to.

Version 14 keeps the V12 packed ocean and scenery stamps. `pixidx.bin`
is the same file. Scenery blits go through viper when a startup self-test
matches the Python loops; otherwise the V12 Python blit stays. A parallax
band that moves by less than a screen is shifted in the 128-wide window
and only the new columns are copied. Stamps that did not move with that
band are erased and redrawn. The pixels match a full compose. Parked,
with nothing moving, the compose is still skipped.

When the playfield image is pushed onto the panel, the full-screen clear
and the second canvas flush are skipped. Gauges still repaint y 103–127.
The face button is polled through compose, blits, flushes, and the OTA
check. The OTA client is unchanged aside from a timeout on its HTTP fetch.
A machine watchdog is armed after the boot allocations.

Every two seconds the serial line looks like:

```text
timing n=40 compose 1.2 stamp 0.8 push 3.5 fill 0.0 flush 0.0 gc 0.3 total 5.5 fps 20.0 hold 28
```

`compose`, `stamp`, `push`, and `total` are the V12 averages
(`total` is still compose + stamp + push). `fill` is `fillScreen` and
`flush` is the full-canvas `push`. Both are 0 when the playfield image
already updated the panel. `gc` is one `gc.collect()` for that interval.
`hold` is how many frames left the backdrop untouched. Printing the line
does not rebuild the ocean or the sprite cache.

`pixidx.bin` is unchanged
(`dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e`,
107656 bytes). The device only replaces `main.py`. Device secrets are
never stored here.
