# Runs before main.py is compiled. Draw the mark first. The OTA check
# stays after that paint and is not asked to clear the panel.
import M5
from M5 import Lcd

try:
    M5.begin({"clear_display": False})
except Exception:
    pass

try:
    import splash_mark
    splash_mark.show(Lcd)
except Exception as e:
    print("splash", e)

try:
    import ota_client
    # main.py owns OTA_ENABLED. A read error still boots; the check runs
    # only when that flag is explicitly True (fail open if the file is unreadable).
    try:
        _ota_on = b"OTA_ENABLED = True" in open("main.py", "rb").read(16384)
    except Exception:
        _ota_on = True
    if _ota_on:
        ota_client.boot_check()
except Exception as e:
    print("boot_check", e)
