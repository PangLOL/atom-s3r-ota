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

## Version 13 is not published

This branch is **2026.11.21-v13** (version **13**). It is not the live
GitHub Pages payload. Do not treat this branch as an OTA update until it
is merged and the owner publishes it.

Version 13 is a driving benchmark. Packed Sonic, the HUD ring and bar,
field rings, and sparkles are still baked once when the pack loads and
stamped each frame. The packed 384×103 ocean, parallax bands, palm
stamps, and loop island are not allocated or drawn. The playfield behind
those sprites is the V2 Green Hill scene: clouds at 0.25, mountains at
0.4, water and islands at 0.6, ground and near palms at 1.0. That scene
is drawn into the 128×103 window, the baked sprites are stamped on top,
and one image is pushed. `ground_y` is the locked feet dest, so the grass
crown meets the shoes and palms still plant at `gy - GRASS_H + 2`.

Every two seconds the serial line looks like:

```text
timing n=40 compose 1.2 stamp 0.8 push 3.5 gc 0.3 total 5.5 fps 20.0 hold 0
```

`compose` is the V2 background, `stamp` is the baked sprites, and `push`
is the playfield image. `hold` stays 0 because that background is redrawn
every frame. Printing the line does not rebuild the sprite cache.

If the sprite cache or a stamp cannot be allocated, sprites fall back to
`fillRect` on the same V2 background. The packed ocean is not used as
that fallback. Aura, shockwave, and the gauges are unchanged.

`pixidx.bin` is unchanged
(`dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e`,
107656 bytes). The device only replaces `main.py`. Device secrets are
never stored here.
