"""Record what this Mac is playing, through tools/bin/sysaudio (ScreenCaptureKit).

One recording at a time. The recorder prints READY, then LEVEL <dBFS> about
twice a second, then DONE <seconds>; PERMISSION (exit 2) when macOS hasn't
allowed screen & system audio recording yet.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .config import MAX_SECONDS, ROOT

SYSAUDIO = ROOT / "tools" / "bin" / "sysaudio"
HEARD_DB = -50.0        # a LEVEL above this counts as "hearing something"
SILENCE_DB = -50.0      # trimming: quieter than this at the start/end is cut
PAD = 0.3               # seconds kept around the music when trimming


class Recorder:
    """Runs one recorder process and follows what it prints."""

    def __init__(self, command: list[str] | None = None):
        self.command = command or [str(SYSAUDIO)]
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._reset()

    def _reset(self) -> None:
        self.state = "idle"            # idle | starting | recording | done | error
        self.error = ""
        self.out: Path | None = None
        self.started = 0.0
        self.seconds = 0.0
        self.level = -120.0
        self.heard = False
        self.info: dict = {}           # what the user filled in (title, kind, mode)

    # -- permission -------------------------------------------------------------------
    def available(self) -> bool:
        return Path(self.command[-1]).exists() if len(self.command) == 1 else True

    def permission(self) -> str:
        """'ok', 'no', or 'missing' (the recorder hasn't been built)."""
        if not self.available():
            return "missing"
        try:
            out = subprocess.run(self.command + ["check"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return "missing"
        return "ok" if out.stdout.strip().startswith("OK") else "no"

    def request_permission(self) -> str:
        if not self.available():
            return "missing"
        try:
            out = subprocess.run(self.command + ["request"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return "no"
        return "ok" if out.stdout.strip().startswith("OK") else "no"

    # -- recording --------------------------------------------------------------------
    def busy(self) -> bool:
        return self.state in ("starting", "recording")

    def start(self, out_wav: Path, info: dict, max_seconds: int = MAX_SECONDS) -> None:
        with self._lock:
            if self.busy():
                raise RuntimeError("Already listening.")
            self._reset()
            self.info = dict(info)
            self.out = Path(out_wav)
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self.out.unlink(missing_ok=True)
            self.state = "starting"
            self.started = time.time()
            self._proc = subprocess.Popen(
                self.command + ["record", str(self.out), "--max-seconds", str(max_seconds)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1)
        threading.Thread(target=self._follow, args=(self._proc,), daemon=True).start()

    def _follow(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            word, _, rest = line.strip().partition(" ")
            if word == "READY":
                self.state, self.started = "recording", time.time()
            elif word == "LEVEL":
                try:
                    self.level = float(rest)
                except ValueError:
                    continue
                self.heard = self.heard or self.level > HEARD_DB
            elif word == "DONE":
                try:
                    self.seconds = float(rest)
                except ValueError:
                    pass
            elif word == "PERMISSION":
                self.state, self.error = "error", "permission"
            elif word == "ERROR":
                self.state, self.error = "error", rest or "The recorder stopped."
        code = proc.wait()
        if self.state != "error":
            if code == 0:
                self.state = "done"
            else:
                self.state, self.error = "error", self.error or f"The recorder stopped (code {code})."

    def elapsed(self) -> float:
        if self.state == "recording":
            return time.time() - self.started
        return self.seconds

    def stop(self, timeout: float = 15.0) -> None:
        """Ask the recorder to finish the file, and wait until it has."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        deadline = time.time() + 2
        while self.busy() and time.time() < deadline:     # let the reader thread catch up
            time.sleep(0.02)

    def cancel(self) -> None:
        self.stop()
        if self.out is not None:
            self.out.unlink(missing_ok=True)
        self._reset()


def trim_silence(src: Path, dst: Path, threshold_db: float = SILENCE_DB, pad: float = PAD) -> float:
    """Copy src to dst without the silence before and after the music.

    Returns the seconds kept (0.0 when the recording is silent; dst isn't written then).
    """
    import soundfile as sf

    y, sr = sf.read(str(src), always_2d=True)
    if len(y) == 0:
        return 0.0
    mono = y.mean(axis=1)
    hop = max(1, int(0.05 * sr))
    frames = len(mono) // hop
    if frames == 0:
        return 0.0
    rms = np.sqrt(np.mean(mono[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    loud = np.nonzero(20 * np.log10(rms + 1e-12) > threshold_db)[0]
    if len(loud) == 0:
        return 0.0
    a = max(0, loud[0] * hop - int(pad * sr))
    b = min(len(y), (loud[-1] + 1) * hop + int(pad * sr))
    tmp = Path(str(dst) + ".tmp.wav")
    sf.write(str(tmp), y[a:b], sr, subtype="PCM_16")
    os.replace(tmp, dst)
    return (b - a) / sr


recorder = Recorder()
