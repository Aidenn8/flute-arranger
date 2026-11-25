"""Listening to this computer, with a fake recorder (no real capture, no sound)."""
import json
import queue
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sheetswap import config, listen
from sheetswap.web import app as web

FAKE = [sys.executable, str(Path(__file__).parent / "fake_sysaudio.py")]


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def rec(monkeypatch):
    r = listen.Recorder(FAKE)
    yield r
    r.cancel()


@pytest.fixture
def client(tmp_path, monkeypatch, rec):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(web, "_jobs", queue.Queue())
    monkeypatch.setattr(web, "_ensure_worker", lambda: None)
    monkeypatch.setattr(web, "_recorder", lambda: rec)
    web.app.config["TESTING"] = True
    return web.app.test_client()


def test_trim_silence(tmp_path):
    sr = 48000
    tone = 0.2 * np.sin(2 * np.pi * 440 * np.arange(2 * sr) / sr)
    y = np.concatenate([np.zeros(sr), tone, np.zeros(sr)])
    sf.write(tmp_path / "a.wav", y, sr)
    kept = listen.trim_silence(tmp_path / "a.wav", tmp_path / "b.wav")
    assert abs(kept - 2.6) < 0.1                       # the tone plus 0.3 s on each side
    assert abs(sf.info(str(tmp_path / "b.wav")).duration - kept) < 0.01
    sf.write(tmp_path / "s.wav", np.zeros(sr), sr)
    assert listen.trim_silence(tmp_path / "s.wav", tmp_path / "t.wav") == 0.0
    assert not (tmp_path / "t.wav").exists()


def test_recorder_start_stop(rec, tmp_path):
    assert rec.permission() == "ok"
    rec.start(tmp_path / "r.wav", {"title": "x"})
    assert wait_for(lambda: rec.state == "recording")
    assert wait_for(lambda: rec.heard)
    rec.stop()
    assert rec.state == "done"
    assert sf.info(str(tmp_path / "r.wav")).duration == pytest.approx(5.0, abs=0.01)


def test_recorder_without_permission(rec, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PERMISSION", "NO")
    assert rec.permission() == "no"
    rec.start(tmp_path / "r.wav", {})
    assert wait_for(lambda: rec.state == "error")
    assert rec.error == "permission"


def test_listen_flow_makes_a_song(client, rec):
    page = client.get("/").get_data(as_text=True)
    assert "Start listening" in page
    resp = client.post("/listen/start", data={"title": "Moon River", "kind": "song", "mode": "duet"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/listen")
    assert wait_for(lambda: rec.state == "recording")
    page = client.get("/listen").get_data(as_text=True)
    assert 'http-equiv="refresh"' in page and "Listening:" in page
    assert wait_for(lambda: rec.heard)
    resp = client.post("/listen/stop")
    assert resp.status_code == 302
    song_id = resp.headers["Location"].rsplit("/", 1)[-1]
    d = config.OUT_DIR / song_id
    meta = json.loads((d / "meta.json").read_text())
    assert meta["title"] == "Moon River" and meta["kind"] == "song" and meta["mode"] == "duet"
    assert abs(sf.info(str(d / "input.wav")).duration - 3.6) < 0.1     # silence trimmed
    assert web._jobs.get_nowait() == song_id
    assert rec.state == "idle"
    assert not (config.OUT_DIR / "_listening" / "recording.wav").exists()


def test_listen_silence_makes_no_song(client, rec, monkeypatch):
    monkeypatch.setenv("FAKE_SILENT", "1")
    client.post("/listen/start", data={})
    assert wait_for(lambda: rec.state == "recording")
    page = client.post("/listen/stop").get_data(as_text=True)
    assert "didn't hear anything" in page
    assert [p.name for p in config.OUT_DIR.iterdir() if p.name != "_listening"] == []


def test_listen_without_permission_explains(client, monkeypatch):
    monkeypatch.setenv("FAKE_PERMISSION", "NO")
    page = client.get("/").get_data(as_text=True)
    assert "Screen &amp; System Audio Recording" in page and "Start listening" not in page
    assert client.post("/listen/start", data={}).status_code == 403


def test_cancel(client, rec):
    client.post("/listen/start", data={})
    assert wait_for(lambda: rec.state == "recording")
    assert client.post("/listen/cancel").status_code == 302
    assert rec.state == "idle"
    assert client.get("/listen").status_code == 302           # nothing to show: back home
