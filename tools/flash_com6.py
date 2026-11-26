# USB flash of main.py onto the Atom at COM6.
#
# Sends ATOMFLASH\\n first. main.py answers "flash-quiet" and then stops
# timing, brk, pause, and auto-sweep prints. If that ack does not arrive
# within a short wait, this falls back to Ctrl-C / replug instead of
# aborting. Copy is tmp-then-swap. The expected size and sha256 are the
# local main.py being copied. Ctrl-B then Ctrl-D leaves raw REPL before
# the soft reset. The port is closed on exit.

import hashlib
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

PORT = "COM6"
BAUD = 115200
SRC = Path(__file__).resolve().parents[1] / "main.py"
PREAMBLE = b"ATOMFLASH\n"
ACK = b"flash-quiet"
CHUNK = 2048
LISTEN_S = 80
QUIET_WAIT_S = 1.5


def log(msg):
    print(msg, flush=True)


def com6_present():
    return any(p.device == PORT for p in list_ports.comports())


def drain(s, seconds=0.2, hard_max=0.6):
    start = time.time()
    end = start + seconds
    limit = start + max(seconds, hard_max)
    buf = b""
    while time.time() < end and time.time() < limit:
        try:
            chunk = s.read(4096)
        except Exception:
            break
        if chunk:
            buf += chunk
            end = min(time.time() + 0.1, limit)
        else:
            time.sleep(0.02)
    return buf


def safe_write(s, data):
    try:
        s.write(data)
        return True
    except (serial.SerialTimeoutException, serial.SerialException) as e:
        log("write failed: %r" % (e,))
        try:
            s.reset_output_buffer()
        except Exception:
            pass
        time.sleep(0.05)
        try:
            s.write(data)
            return True
        except (serial.SerialTimeoutException, serial.SerialException) as e2:
            log("write retry failed: %r" % (e2,))
            return False


def open_port():
    s = serial.Serial(PORT, BAUD, timeout=0.2, write_timeout=2, dsrdtr=False, rtscts=False)
    s.dtr = False
    s.rts = False
    return s


def request_quiet(s, timeout=QUIET_WAIT_S):
    """Ask the running dashboard to stop prints. False falls back to interrupt."""
    log("sending ATOMFLASH preamble")
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        if not safe_write(s, PREAMBLE):
            time.sleep(0.05)
            continue
        buf += drain(s, 0.25)
        if ACK in buf:
            log("board acknowledged flash-quiet")
            return True
    log("no flash-quiet ack within %.1fs; falling back to interrupt/replug" % timeout)
    return False


def enter_raw_repl(s, rounds=80):
    banner = b""
    for _ in range(rounds):
        if not safe_write(s, b"\x03"):
            time.sleep(0.05)
            continue
        time.sleep(0.04)
        banner += drain(s, 0.04)
    if not safe_write(s, b"\x01"):
        return banner
    time.sleep(0.25)
    banner += drain(s, 0.4)
    if b"raw REPL" not in banner:
        for _ in range(20):
            safe_write(s, b"\x03")
            time.sleep(0.05)
            banner += drain(s, 0.05)
        safe_write(s, b"\x01")
        time.sleep(0.3)
        banner += drain(s, 0.5)
    return banner


def raw_exec(s, src, timeout=8.0):
    if not src.endswith("\n"):
        src += "\n"
    if not safe_write(s, src.encode("utf-8") + b"\x04"):
        return b"WRITE_TIMEOUT"
    end = time.time() + timeout
    buf = b""
    while time.time() < end:
        chunk = s.read(4096)
        if chunk:
            buf += chunk
            if b"\x04>" in buf or (b"Traceback" in buf and b"\x04>" in buf):
                buf += drain(s, 0.15)
                break
        else:
            time.sleep(0.02)
    return buf


def wait_for_replug():
    log("NEED_REPLUG: Unplug the Atom, wait 2 seconds, then plug it back in.")
    deadline = time.time() + 180
    if com6_present():
        while com6_present():
            if time.time() > deadline:
                log("FAIL: still waiting for COM6 to drop (unplug the cable)")
                return False
            time.sleep(0.1)
    log("Cable out. Plug it back in now.")
    deadline = time.time() + 180
    while not com6_present():
        if time.time() > deadline:
            log("FAIL: COM6 did not return")
            return False
        time.sleep(0.05)
    log("COM6 is back, interrupting and copying...")
    time.sleep(0.2)
    return True


def listen_boot(s, seconds=LISTEN_S):
    log("--- listening %ss ---" % seconds)
    total = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            chunk = s.read(4096)
        except Exception as e:
            log("\n[serial error] %r" % (e,))
            try:
                s.close()
            except Exception:
                pass
            recovered = False
            give = time.time() + 8
            while time.time() < give:
                if com6_present():
                    try:
                        time.sleep(0.3)
                        s = open_port()
                        recovered = True
                        log("[reopened COM6]")
                        break
                    except Exception as e2:
                        log("[reopen failed] %r" % (e2,))
                        time.sleep(0.3)
                else:
                    time.sleep(0.1)
            if not recovered:
                log("[COM6 gone, stopping listen]")
                return 1, total
            continue
        if chunk:
            total += len(chunk)
            sys.stdout.write(chunk.decode("utf-8", "replace"))
            sys.stdout.flush()
        else:
            time.sleep(0.02)
    log("\n--- listen done, %s bytes ---" % total)
    return 0, total


def main():
    data = SRC.read_bytes()
    digest = hashlib.sha256(data).digest()
    size = len(data)
    log("local main.py: %s bytes %s" % (size, digest.hex()))

    s = None

    def try_raw_repl(label):
        nonlocal s
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
            s = None
            time.sleep(0.2)
        s = open_port()
        time.sleep(0.4)
        request_quiet(s)
        banner = enter_raw_repl(s, rounds=50)
        log("%s banner: %r" % (label, banner[-200:]))
        return b"raw REPL" in banner

    try:
        if not com6_present():
            log("COM6 not present yet.")
            if not wait_for_replug():
                return 9
        reached = False
        for attempt in range(1, 4):
            try:
                if try_raw_repl("try %d" % attempt):
                    reached = True
                    break
            except (serial.SerialTimeoutException, serial.SerialException) as e:
                log("try %d open/interrupt failed: %r" % (attempt, e))
            time.sleep(0.5)
        if not reached:
            log("did not reach raw REPL on the current plug; need another fresh plug-in")
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
                s = None
            if not wait_for_replug():
                return 3
            time.sleep(0.4)
            if not try_raw_repl("after replug"):
                log("FAIL: did not reach raw REPL after replug")
                return 3

        r = raw_exec(s, "f=open('main.py.tmp','wb')\n")
        log("open: %r" % (r[-120:],))
        if b"Traceback" in r or b"Error" in r or b"WRITE_TIMEOUT" in r:
            log("FAIL: could not open temp file")
            return 4

        for i in range(0, len(data), CHUNK):
            part = data[i : i + CHUNK]
            r = raw_exec(s, "f.write(%r)\n" % (part,), timeout=12.0)
            if b"Traceback" in r or b"Error" in r or b"OK" not in r:
                log("FAIL: write chunk at %s: %r" % (i, r[-200:]))
                return 5
            if i == 0 or (i // CHUNK) % 16 == 0:
                log("wrote %s/%s" % (min(i + CHUNK, len(data)), len(data)))

        r = raw_exec(s, "f.close()\n")
        log("close: %r" % (r[-120:],))

        verify = (
            "import os, hashlib\n"
            "n=os.stat('main.py.tmp')[6]\n"
            "h=hashlib.sha256(); f=open('main.py.tmp','rb')\n"
            "while True:\n"
            "    b=f.read(2048)\n"
            "    if not b: break\n"
            "    h.update(b)\n"
            "f.close()\n"
            "exp=%r\n"
            "print('VERIFY', n, h.digest()==exp)\n" % (digest,)
        )
        r = raw_exec(s, verify, timeout=20.0)
        log("verify: %r" % (r[-300:],))
        if b"VERIFY %d True" % size not in r or b"Traceback" in r:
            log("FAIL: on-device verify did not match")
            return 6

        swap = (
            "import os\n"
            "if 'main.py' in os.listdir():\n"
            "    os.remove('main.py')\n"
            "os.rename('main.py.tmp','main.py')\n"
            "print('SWAPPED', os.stat('main.py')[6])\n"
        )
        r = raw_exec(s, swap, timeout=10.0)
        log("swap: %r" % (r[-300:],))
        if b"SWAPPED %d" % size not in r or b"Traceback" in r:
            log("FAIL: swap did not complete")
            return 7

        # Exit raw REPL before soft reset (Ctrl-B, then Ctrl-D).
        safe_write(s, b"\x02")
        time.sleep(0.25)
        boot_prompt = drain(s, 0.4)
        log("friendly REPL: %r" % (boot_prompt[-200:],))
        safe_write(s, b"\x04")
        time.sleep(0.8)
        early = drain(s, 1.0)
        if early:
            sys.stdout.write(early.decode("utf-8", "replace"))
            sys.stdout.flush()
        log("FLASH_OK")
        listen_boot(s, LISTEN_S)
        return 0
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass
        log("COM6 released")


if __name__ == "__main__":
    sys.exit(main())
