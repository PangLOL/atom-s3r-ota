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

## Version 11 is not published

This branch is **2026.11.21-v11** (version **11**). It is ready to publish
later. It is not the live GitHub Pages payload. The live site and the
device stay on **2026.11.21-v10** until the owner says go and this branch
is merged. Do not treat this branch as an OTA update.

Version 11 keeps the V10 parallax ocean: at pack load the 384×103 backdrop
is baked to RGB565 once, then each frame composes sky/clouds at 0.25,
water and islands at 0.6, and ground plus near props at 1.0 into the
128×103 window. It also bakes the frames that still used `fillRect`:
Sonic's idle, idle-trigger, and run cycles, the HUD ring, the HUD bar,
the field rings, and the ring sparkles. Each of those is an RGB565 image
plus a 1-bit transparency mask. Each frame stamps the visible baked
sprites into the parallax window and pushes the playfield as one image.
Scroll, animation, boost, and the gauges stay live. Overheat still
breathes a red tint onto Sonic's blue pixels while compositing the frame
on screen. Aura and shockwave stay the V2 primitives, drawn after the
push. If the cache cannot be allocated, the firmware falls back to the
V10 `fillRect` interpreter and stays there.

`pixidx.bin` is unchanged
(`dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e`,
107656 bytes). The device only replaces `main.py`. Device secrets are
never stored here.
