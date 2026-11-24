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

## Version 13

This branch is **2026.11.25-v13** (version **13**). It becomes the live
OTA payload when it is merged and published.

Version 13 keeps the V12 playfield. Official Sega artwork may be packed
into `pixidx.bin` and flashed. The device keeps a `pixidx.bin` already
on the device: hash, byte size, and sprite size do not reject a PIX6
pack, and the hosted file is not downloaded over it. Mouth frames (`mouth_closed`,
`mouth_open`, `mouth_locked`), Chao cycles (`chao_normal`, `chao_amy`,
`chao_knuckles`, `chao_sonic`, `chao_tails`), and boost FX cycles
(`aura`, `sw`, `bar`) are drawn when the pack contains them. The hosted
`pixidx.bin` in this repository is still the previous asset.

The 384×103 ocean and the sprite cache are still built once when the pack loads. Each frame still pushes
one 128×103 image. A parallax band is recopied only when
`int(scroll * factor)` changes. Scenery is restamped only inside the
rectangles a shifted band or a moved stamp actually dirtied, so the
pixels match a full V11 compose. Sprite frames that did not change stay
where they are. A small underlay (9630 bytes) restores the backdrop
under a sprite that did change: animation, spinning rings, sparkles, or
the overheat tint. Parked, with nothing in the playfield moving, the
parallax compose is skipped.

Every two seconds the serial line looks like:

```text
timing n=40 compose 1.2 stamp 0.8 push 3.5 gc 0.3 total 5.5 fps 20.0 hold 28
```

`compose`, `stamp`, `push`, and `total` are average milliseconds per
frame. `gc` is one `gc.collect()` for that interval. `hold` is how many
of those frames left the backdrop untouched. Printing the line does not
rebuild the ocean or the sprite cache.

If the underlay cannot be allocated, drawing stays on the V11 full
compose. If a later stamp fails, the bake flag is cleared and drawing
stays on the `fillRect` interpreter. Aura, shockwave, and the gauges are
unchanged.

`pixidx.bin` in this repository is unchanged
(`dda4ea1e1f3249adc8bccb8fc9fe07bb446fb569d5abb884bd1bf83cfe4ba47e`,
107656 bytes). A different PIX6 file flashed on the device is kept.
Device secrets are never stored here.
