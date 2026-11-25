"""The system-audio recorder, tools/bin/sysaudio (built from tools/sysaudio/main.swift).

Nothing here plays sound. Real capture needs the Screen & System Audio Recording
permission: without it `record` must exit 2 with PERMISSION; with it, a 1-second
run must give READY ... DONE and a 1-second WAV. Everything else uses the
recorder's synthetic `--test-tone` source, which needs no permission.
"""
import queue
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "tools" / "bin" / "sysaudio"
SRC = ROOT / "tools" / "sysaudio" / "main.swift"
BUILD = ROOT / "scripts" / "build_sysaudio.sh"
SR = 48000

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def have_swift():
    # xcode-select first: a bare `xcrun` without developer tools pops up an install dialog.
    if shutil.which("xcode-select") is None or shutil.which("xcrun") is None:
        return False
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def sysaudio():
    if not BIN.exists() or BIN.stat().st_mtime < SRC.stat().st_mtime:
        if not have_swift():
            pytest.skip("xcrun swiftc not available")
        subprocess.run(["bash", str(BUILD)], check=True, capture_output=True, timeout=600)
    return BIN


class Recording:
    """A running `sysaudio record` with its stdout lines collected on a thread."""

    def __init__(self, binary, out, *args):
        self.proc = subprocess.Popen([str(binary), "record", str(out), *args],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.lines = queue.Queue()
        self.seen = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line.strip())
        self.lines.put(None)

    def expect(self, prefix, timeout=10):
        """Waits for the next line starting with `prefix`; returns it."""
        deadline = time.monotonic() + timeout
        while True:
            line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            assert line is not None, f"recorder ended before {prefix!r}: {self.seen}"
            self.seen.append(line)
            if line.startswith(prefix):
                return line

    def finish(self, timeout=10):
        rc = self.proc.wait(timeout=timeout)
        self.proc.stdin.close()
        return rc

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def recordings():
    started = []
    yield lambda *args: started.append(Recording(*args)) or started[-1]
    for rec in started:  # never leave a recorder running
        rec.kill()


def done_seconds(line):
    assert line.startswith("DONE ")
    return float(line.split()[1])


def wav_data_frames(path):
    """Frames according to the WAV header's data chunk, and frames actually in the file."""
    raw = path.read_bytes()
    pos = 12
    while pos + 8 <= len(raw):
        cid, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        if cid == b"data":
            return size // 4, (len(raw) - pos - 8) // 4
        pos += 8 + size + (size & 1)
    raise AssertionError("no data chunk")


def assert_wav(path, seconds):
    info = sf.info(path)
    assert (info.samplerate, info.channels, info.subtype, info.format) == (SR, 2, "PCM_16", "WAV")
    assert info.duration == pytest.approx(seconds, abs=0.06)
    header, actual = wav_data_frames(path)
    assert header == actual == info.frames  # the header was finished


def test_check_prints_ok_or_no(sysaudio):
    out = subprocess.run([str(sysaudio), "check"], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0
    assert out.stdout.strip() in ("OK", "NO")


@pytest.mark.parametrize("args", [[], ["nonsense"], ["record"], ["record", "a.wav", "b.wav"],
                                  ["record", "a.wav", "--max-seconds", "-1"],
                                  ["record", "a.wav", "--max-seconds"]])
def test_bad_arguments_are_errors(sysaudio, tmp_path, args):
    out = subprocess.run([str(sysaudio), *args], capture_output=True, text=True,
                         timeout=10, cwd=tmp_path, stdin=subprocess.DEVNULL)
    assert out.returncode == 1
    assert out.stdout.startswith("ERROR ")


def test_record_real_capture_or_permission(sysaudio, tmp_path, recordings):
    allowed = subprocess.run([str(sysaudio), "check"], capture_output=True,
                             text=True, timeout=10).stdout.strip() == "OK"
    out = tmp_path / "real.wav"
    rec = recordings(sysaudio, out, "--max-seconds", "1")
    if not allowed:
        assert rec.expect("PERMISSION") == "PERMISSION"
        assert rec.finish() == 2
        assert not out.exists()
        return
    assert rec.expect("") == "READY"
    seconds = done_seconds(rec.expect("DONE", timeout=15))
    assert rec.finish() == 0
    assert seconds == pytest.approx(1.0, abs=0.1)
    assert_wav(out, seconds)


def test_tone_records_levels_and_fills_missing_audio(sysaudio, tmp_path, recordings):
    out = tmp_path / "tone.wav"
    rec = recordings(sysaudio, out, "--test-tone", "440", "--max-seconds", "1.2")
    assert rec.expect("") == "READY"
    level = rec.expect("LEVEL")
    assert float(level.split()[1]) == pytest.approx(-23.0, abs=0.5)  # 0.1 amplitude sine
    assert rec.expect("DONE") == "DONE 1.2"
    assert rec.finish() == 0
    assert_wav(out, 1.2)

    x, _ = sf.read(out)
    left = x[:, 0]
    # The source sends nothing from 0.5 s to 0.8 s; that time comes back as silence...
    assert np.abs(left[int(0.52 * SR):int(0.78 * SR)]).max() == 0
    # ...so the tone after it lands exactly where it was played. (The last few
    # milliseconds, still on their way at the stop, are written as silence.)
    n = np.arange(len(left))
    after = slice(int(0.82 * SR), int(1.1 * SR))
    assert np.abs(left[after] - 0.1 * np.sin(2 * np.pi * 440 * n[after] / SR)).max() < 1e-3


def stop_by_signal(sig):
    return lambda rec: rec.proc.send_signal(sig)


def stop_by_line(rec):
    rec.proc.stdin.write("stop\n")
    rec.proc.stdin.flush()


def stop_by_eof(rec):
    rec.proc.stdin.close()


@pytest.mark.parametrize("stop", [stop_by_signal(signal.SIGINT), stop_by_signal(signal.SIGTERM),
                                  stop_by_line, stop_by_eof],
                         ids=["sigint", "sigterm", "stdin-stop", "stdin-eof"])
def test_every_way_of_stopping_finishes_the_wav(sysaudio, tmp_path, recordings, stop):
    out = tmp_path / "stopped.wav"
    rec = recordings(sysaudio, out, "--test-tone", "660")
    assert rec.expect("") == "READY"
    time.sleep(0.4)
    stop(rec)
    seconds = done_seconds(rec.expect("DONE"))
    assert rec.proc.wait(timeout=10) == 0
    assert seconds == pytest.approx(0.4, abs=0.15)
    assert_wav(out, seconds)
