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
    ota_client.boot_check()
except Exception as e:
    print("boot_check", e)
