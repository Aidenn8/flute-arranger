"""Web app: Flask test client, songs in a temp folder, the pipeline stubbed out."""
import io
import json
import queue
import time
import types
from datetime import datetime

import pytest

from sheetswap import config
from sheetswap.model import Arrangement
from sheetswap.web import app as web


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "out"
    monkeypatch.setattr(config, "OUT_DIR", d)
    return d


@pytest.fixture
def client(out, monkeypatch):
    """No worker thread: queued songs just wait in a fresh queue."""
    monkeypatch.setattr(web, "_jobs", queue.Queue())
    monkeypatch.setattr(web, "_ensure_worker", lambda: None)
    web.app.config["TESTING"] = True
    return web.app.test_client()


def make_song(out, song_id="abc12345", status="done", title=None, mode="solo", **extra):
    d = out / song_id
    d.mkdir(parents=True)
    meta = web.new_meta(song_id, "My Song.mp3", "auto", mode)
    meta.update(status=status, **extra)
    if title is not None:
        meta["title"] = title
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "input.mp3").write_bytes(b"ID3 pretend audio")
    return d


def read_meta(d):
    return json.loads((d / "meta.json").read_text())


def fake_arrange(calls):
    def arrange_and_render(song_dir, opts):
        calls.append(opts)
        a = song_dir / "arr" / opts.key()
        a.mkdir(parents=True, exist_ok=True)
        for name in ("score.musicxml", "score.mid", "preview.mp3"):
            (a / name).write_bytes(b"data")
        return {
            "svgs": ['<svg width="840px" height="141px"><text>page one</text></svg>',
                     '<svg width="840px" height="300px"><text>page two</text></svg>'],
            "musicxml": a / "score.musicxml", "midi": a / "score.mid", "mp3": a / "preview.mp3",
            "warnings": ["Two notes were too high & moved down an octave."],
            "arr": Arrangement(flute=[], beats_per_bar=4, bpm=92.4, key_fifths=2, key_mode="major"),
        }
    return arrange_and_render


def stub_pipeline(monkeypatch, analyze=None, arrange_and_render=None):
    def no_arrange(song_dir, opts):
        raise AssertionError("arrange_and_render should not be called here")
    fake = types.SimpleNamespace(
        analyze=analyze or (lambda song_dir, kind, progress: None),
        arrange_and_render=arrange_and_render or no_arrange,
    )
    monkeypatch.setattr(web, "_pipeline", lambda: fake)
    return fake


# ---------------------------------------------------------------- home page

def test_index_lists_songs(client, out):
    make_song(out, "aaaa1111", status="done", title="Moon River", added="2026-11-20T09:05:00")
    make_song(out, "bbbb2222", status="analyzing", title="<b>Bold</b>", step=2, total=5,
              added="2026-11-24T17:30:00")
    (out / "cccc3333").mkdir()                     # stray folder without meta.json
    html = client.get("/").get_data(as_text=True)
    assert "<h1>Flute Arranger</h1>" in html
    assert 'enctype="multipart/form-data"' in html
    assert 'name="kind" value="song"' in html and 'name="mode" value="duet"' in html
    assert '<table border="1" cellpadding="4">' in html
    assert "Moon River" in html and "ready" in html
    assert "&lt;b&gt;Bold&lt;/b&gt;" in html and "<b>Bold</b>" not in html
    assert "working (step 2 of 5)" in html
    assert 'href="/song/aaaa1111"' in html and 'action="/song/aaaa1111/delete"' in html
    assert "Nov 20, 2026, 9:05 am" in html
    assert html.index("bbbb2222") < html.index("aaaa1111")   # newest first
    assert "cccc3333" not in html


def test_index_empty(client, out):
    html = client.get("/").get_data(as_text=True)
    assert "No songs yet" in html


# ---------------------------------------------------------------- upload

def test_upload_redirects(client, out):
    resp = client.post("/upload", content_type="multipart/form-data", data={
        "file": (io.BytesIO(b"RIFF pretend"), "Moon River.MP3"), "kind": "song", "mode": "duet"})
    assert resp.status_code == 302
    song_id = resp.headers["Location"].rsplit("/", 1)[-1]
    assert resp.headers["Location"].endswith(f"/song/{song_id}")
    d = out / song_id
    meta = read_meta(d)
    assert meta["id"] == song_id
    assert meta["status"] == "queued"
    assert meta["title"] == "Moon River" and meta["filename"] == "Moon River.MP3"
    assert meta["kind"] == "song" and meta["mode"] == "duet"
    datetime.fromisoformat(meta["added"])
    assert (d / "input.mp3").read_bytes() == b"RIFF pretend"
    assert (d / "log.txt").exists()
    assert web._jobs.get_nowait() == song_id


def test_posts_from_other_sites_refused(client, out):
    data = lambda: {"file": (io.BytesIO(b"RIFF pretend"), "a.mp3"), "kind": "auto", "mode": "solo"}  # noqa: E731
    bad = client.post("/upload", content_type="multipart/form-data", data=data(),
                      headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403
    assert not out.exists() or list(out.iterdir()) == []
    ok = client.post("/upload", content_type="multipart/form-data", data=data(),
                     headers={"Origin": "http://localhost"})          # the test client's host
    assert ok.status_code == 302


def test_upload_bad_choices_fall_back(client, out):
    resp = client.post("/upload", content_type="multipart/form-data", data={
        "file": (io.BytesIO(b"MThd"), "tune.midi"), "kind": "opera", "mode": "trio"})
    meta = read_meta(out / resp.headers["Location"].rsplit("/", 1)[-1])
    assert meta["kind"] == "auto" and meta["mode"] == "solo"


def test_upload_rejects_bad_extension(client, out):
    resp = client.post("/upload", content_type="multipart/form-data",
                       data={"file": (io.BytesIO(b"hello"), "notes.txt")})
    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert "notes.txt" in html and "mp3, wav" in html
    assert not out.exists() or not any(out.iterdir())
    assert web._jobs.empty()


def test_upload_rejects_missing_and_empty_file(client, out):
    resp = client.post("/upload", content_type="multipart/form-data", data={})
    assert resp.status_code == 400 and "choose a file" in resp.get_data(as_text=True)
    resp = client.post("/upload", content_type="multipart/form-data",
                       data={"file": (io.BytesIO(b""), "silence.wav")})
    assert resp.status_code == 400 and "is empty" in resp.get_data(as_text=True)
    assert not any(out.iterdir())
    assert web._jobs.empty()


def test_upload_too_big(client, out, monkeypatch):
    monkeypatch.setitem(web.app.config, "MAX_CONTENT_LENGTH", 500)
    resp = client.post("/upload", content_type="multipart/form-data",
                       data={"file": (io.BytesIO(b"x" * 2000), "long.wav")})
    assert resp.status_code == 413
    assert "too big" in resp.get_data(as_text=True)


# ---------------------------------------------------------------- song page

def test_song_page_progress(client, out, monkeypatch):
    stub_pipeline(monkeypatch)
    make_song(out, "abc12345", status="analyzing", step=3, total=7, message="Finding the melody")
    html = client.get("/song/abc12345").get_data(as_text=True)
    assert '<meta http-equiv="refresh" content="2">' in html
    assert "Step 3 of 7: Finding the melody" in html
    assert "few minutes" in html
    make_song(out, "def67890", status="queued")
    html = client.get("/song/def67890").get_data(as_text=True)
    assert 'http-equiv="refresh"' in html and "Waiting to start" in html
    assert "another song is being worked on first" in html


def test_song_page_failed_shows_error_and_log_tail(client, out):
    d = make_song(out, status="failed", error="That song is too long.")
    (d / "log.txt").write_text("".join(f"log line {i:02d}\n" for i in range(30)))
    html = client.get("/song/abc12345").get_data(as_text=True)
    assert "That song is too long." in html
    assert "<pre>" in html and "log line 29" in html and "log line 10" in html
    assert "log line 09" not in html
    assert 'http-equiv="refresh"' not in html


def test_song_page_done_renders_svg_and_links(client, out, monkeypatch):
    calls = []
    stub_pipeline(monkeypatch, arrange_and_render=fake_arrange(calls))
    make_song(out, "abc12345", mode="duet")
    resp = client.get("/song/abc12345")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert calls[-1].mode == "duet"                              # default from the upload
    key = calls[-1].key()
    assert "<text>page one</text>" in html and "<text>page two</text>" in html
    assert 'http-equiv="refresh"' not in html
    assert "Key: D major" in html and "Time: 4/4" in html and "Tempo: 92" in html
    assert "too high &amp; moved down" in html
    assert f'src="/song/abc12345/file/arr/{key}/preview.mp3"' in html
    assert 'src="/song/abc12345/file/input.mp3"' in html
    assert f'href="/song/abc12345/file/arr/{key}/score.musicxml?download=1"' in html
    assert f'href="/song/abc12345/file/arr/{key}/score.mid?download=1"' in html
    assert 'href="/song/abc12345/print?' in html and "Print / save as PDF" in html
    assert 'name="mode" value="duet" checked' in html

    html = client.get("/song/abc12345?mode=solo&octave=1&simple=1&melody_source=vocals"
                      ).get_data(as_text=True)
    opts = calls[-1]
    assert (opts.mode, opts.octave, opts.simple, opts.melody_source) == ("solo", 1, True, "vocals")
    assert opts.easy_key is False
    assert 'name="octave" value="1" checked' in html
    assert 'name="simple" value="1" checked' in html
    assert "octave=1" in html and "simple=1" in html            # carried into the print link

    resp = client.get("/song/abc12345/print?mode=solo&octave=1")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "@media print" in page and "<text>page two</text>" in page
    assert 'viewBox="0 0 840 141"' in page
    assert page.count('class="page"') == 2
    assert 'href="/song/abc12345?' in page and "Back" in page
    assert calls[-1].octave == 1


def test_song_page_time_choices(client, out, monkeypatch):
    calls = []
    plain = fake_arrange(calls)

    def compound(song_dir, opts):
        res = plain(song_dir, opts)
        res["arr"] = Arrangement(flute=[], beats_per_bar=2, beat_ticks=18, bpm=60.2)
        return res
    stub_pipeline(monkeypatch, arrange_and_render=compound)
    make_song(out, "abc12345")
    html = client.get("/song/abc12345").get_data(as_text=True)
    assert calls[-1].meter == "auto" and 'name="meter" value="auto" checked' in html
    for m in ("2/4", "3/4", "4/4", "3/8", "6/8", "9/8", "12/8"):
        assert f'name="meter" value="{m}"' in html
    assert "Time: 6/8" in html and "Tempo: 60 (counting dotted quarters)" in html

    html = client.get("/song/abc12345?meter=6%2F8").get_data(as_text=True)
    assert calls[-1].meter == "6/8"
    assert 'name="meter" value="6/8" checked' in html
    assert "meter=6/8" in html                                      # carried into the print link
    client.get("/song/abc12345/print?meter=6/8")
    assert calls[-1].meter == "6/8"
    # a link from before 6/8 existed
    client.get("/song/abc12345?meter=3")
    assert calls[-1].meter == "3/4"
    assert web.tempo_text(92.4, 12) == "92" and web.tempo_text(150, 6) == "150 (counting eighths)"


def test_song_page_arrange_error(client, out, monkeypatch):
    def broken(song_dir, opts):
        raise RuntimeError("could not find a tune")
    stub_pipeline(monkeypatch, arrange_and_render=broken)
    d = make_song(out)
    resp = client.get("/song/abc12345")
    assert resp.status_code == 500
    html = resp.get_data(as_text=True)
    assert "could not find a tune" in html and 'value="Update"' in html
    assert "Traceback" in (d / "log.txt").read_text()


def test_unknown_song_is_404(client, out):
    assert client.get("/song/0123abcd").status_code == 404
    assert client.get("/song/NOT-AN-ID").status_code == 404


# ---------------------------------------------------------------- files

def test_file_route_blocks_traversal(client, out):
    d = make_song(out)
    (out / "secret.txt").write_text("TOPSECRET")
    (d / "arr").mkdir()
    (d / "arr" / "score.mid").write_bytes(b"MThd")

    ok = client.get("/song/abc12345/file/input.mp3")
    assert ok.status_code == 200 and ok.data == b"ID3 pretend audio"
    dl = client.get("/song/abc12345/file/arr/score.mid?download=1")
    assert dl.status_code == 200
    assert "attachment" in dl.headers["Content-Disposition"]
    assert "My Song.mid" in dl.headers["Content-Disposition"]

    for url in ["/song/abc12345/file/../secret.txt",
                "/song/abc12345/file/..%2fsecret.txt",
                "/song/abc12345/file/%2e%2e/secret.txt",
                "/song/abc12345/file/arr/../../secret.txt",
                "/song/abc12345/file/%2Fetc%2Fpasswd",
                "/song/..%2f/file/secret.txt",
                "/song/0123abcd/file/input.mp3"]:
        resp = client.get(url, follow_redirects=True)
        assert resp.status_code == 404, url
        assert b"TOPSECRET" not in resp.data and b"root:" not in resp.data


# ---------------------------------------------------------------- delete

def test_delete(client, out):
    make_song(out, "abc12345")
    keep = make_song(out, "def67890")
    resp = client.post("/song/abc12345/delete")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    assert not (out / "abc12345").exists()
    assert client.get("/song/abc12345").status_code == 404
    assert client.get("/song/def67890/delete").status_code == 405
    assert client.post("/song/zz/delete").status_code == 302      # bad id: nothing happens
    assert keep.exists()


# ---------------------------------------------------------------- background work

def test_run_job_success(out, monkeypatch):
    seen = []
    calls = []

    def analyze(song_dir, kind, progress):
        assert kind == "auto"
        progress(1, 2, "Separating the instruments")
        seen.append(read_meta(song_dir)["status"])
        progress(2, 2, "Finding the melody")

    stub_pipeline(monkeypatch, analyze=analyze, arrange_and_render=fake_arrange(calls))
    d = make_song(out, status="queued", mode="duet")
    web.run_job("abc12345")
    meta = read_meta(d)
    assert seen == ["analyzing"]
    assert meta["status"] == "done" and meta["step"] == 2 and meta["total"] == 2
    assert [c.mode for c in calls] == ["duet"]                     # default sheet made ahead
    log = (d / "log.txt").read_text()
    assert "Step 1 of 2: Separating the instruments" in log and "Done in" in log


def test_run_job_failure(out, monkeypatch):
    def analyze(song_dir, kind, progress):
        progress(1, 5, "Reading the file")
        raise ValueError("not audio")

    stub_pipeline(monkeypatch, analyze=analyze)
    d = make_song(out, status="queued")
    web.run_job("abc12345")
    meta = read_meta(d)
    assert meta["status"] == "failed"
    assert "doesn't seem to be audio" in meta["error"]
    log = (d / "log.txt").read_text()
    assert "Traceback" in log and "ValueError: not audio" in log


def test_run_job_skips_deleted_song(out, monkeypatch):
    stub_pipeline(monkeypatch)
    web.run_job("abc12345")
    assert not (out / "abc12345").exists()


def test_mark_interrupted(out):
    a = make_song(out, "aaaa1111", status="queued")
    b = make_song(out, "bbbb2222", status="analyzing", step=2, total=7)
    c = make_song(out, "cccc3333", status="done")
    assert web.mark_interrupted() == 2
    for d in (a, b):
        meta = read_meta(d)
        assert meta["status"] == "failed" and meta["error"] == web.INTERRUPTED
    assert read_meta(c)["status"] == "done"


def test_worker_thread_runs_upload(out, monkeypatch):
    calls = []
    stub_pipeline(monkeypatch, arrange_and_render=fake_arrange(calls))
    web.app.config["TESTING"] = True
    resp = web.app.test_client().post("/upload", content_type="multipart/form-data", data={
        "file": (io.BytesIO(b"RIFF pretend"), "song.wav")})
    d = out / resp.headers["Location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 5
    while read_meta(d)["status"] != "done" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert read_meta(d)["status"] == "done"
    assert len(calls) == 1
