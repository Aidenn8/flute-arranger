"""Stand-in for tools/bin/sysaudio in tests: same commands and output, no real capture.

FAKE_PERMISSION=NO   -> check prints NO, record prints PERMISSION and exits 2
FAKE_SILENT=1        -> the recording is silence
"""
import os
import signal
import sys
import time

import numpy as np
import soundfile as sf

SR = 48000


def main() -> int:
    cmd = sys.argv[1]
    allowed = os.environ.get("FAKE_PERMISSION", "OK") == "OK"
    if cmd in ("check", "request"):
        print("OK" if allowed else "NO")
        return 0
    if cmd != "record":
        print("ERROR unknown command")
        return 1
    out = sys.argv[2]
    max_s = float(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else 720
    if not allowed:
        print("PERMISSION", flush=True)
        return 2
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    print("READY", flush=True)
    silent = os.environ.get("FAKE_SILENT") == "1"
    t0 = time.time()
    while not stop["now"] and time.time() - t0 < max_s:
        print(f"LEVEL {-120.0 if silent else -20.0:.1f}", flush=True)
        time.sleep(0.05)
    secs = time.time() - t0
    # 1 s of silence, 3 s of a 440 Hz tone, 1 s of silence
    t = np.arange(3 * SR) / SR
    tone = np.zeros_like(t) if silent else 0.2 * np.sin(2 * np.pi * 440 * t)
    y = np.concatenate([np.zeros(SR), tone, np.zeros(SR)])
    sf.write(out, np.stack([y, y], axis=1), SR, subtype="PCM_16")
    print(f"DONE {secs:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
