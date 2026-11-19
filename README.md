# Atom S3R OTA artifacts

Public payload for the M5Stack Atom S3R GE8 dashboard.

This repository is the OTA host only. It contains `version.json` and
`main.py`. It does not contain device secrets.

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
or update.

`.nojekyll` keeps GitHub Pages from running Jekyll.

Current payload: **2026.11.21-b1** (version **2**).

Version 1 was the OTA-capable gauges fallback. Version 2 is the graphics
build (original drawn after-backdrop, boost FX, Chao, ring HUD). Device
secrets are never stored here.
