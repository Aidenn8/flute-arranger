"""The Flute Arranger web page.

Start it with ./run.sh, then visit http://localhost:5230

Each uploaded song gets a folder out/<id>/ holding:
  input.<ext>   the uploaded file
  meta.json     title, choices and progress (see new_meta)
  log.txt       what happened, including any error details
plus whatever the pipeline writes (analysis.json, arrangements).

The slow listening step (pipeline.analyze) runs on one background thread,
one song at a time. Arranging is quick and cached, so it runs inside the
page request.
"""
from __future__ import annotations

import html
import json
import os
import queue
import re
import secrets
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Mapping

from flask import (Flask, abort, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import config
from ..model import ArrangeOptions

UPLOAD_TYPES = ("mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "webm", "aiff", "aif",
                "mid", "midi")
MIDI_TYPES = ("mid", "midi")
MAX_UPLOAD_MB = 200
KINDS = ("auto", "piano", "ensemble", "song", "band")
MODES = ("solo", "duet")
BUSY = ("queued", "analyzing")
LOG_TAIL = 20
INTERRUPTED = "Interrupted — please upload it again."

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.before_request
def _same_site_posts_only():
    """Refuse form posts sent by other web sites (they could queue uploads here)."""
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            abort(403)

_ID_RE = re.compile(r"[0-9a-f]{6,32}")
_lock = threading.Lock()            # guards meta.json / log.txt writes and deletes
_arrange_lock = threading.Lock()    # one arrangement at a time
_jobs: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_guard = threading.Lock()

_FRIENDLY = {
    "not audio": "That file doesn't seem to be audio or MIDI that can be read.",
    "too long": f"That song is too long. The limit is {config.MAX_SECONDS // 60} minutes.",
}


def _pipeline():
    """Imported on first use: it loads the heavy music libraries."""
    from .. import pipeline
    return pipeline


# ---------------------------------------------------------------- song folders

def _out_dir() -> Path:
    return Path(config.OUT_DIR)


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def read_meta(d: Path) -> dict | None:
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    meta["id"] = d.name
    return meta


def _write_meta(d: Path, meta: dict) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "meta.json")


def update_meta(d: Path, **changes) -> dict | None:
    """Merge changes into meta.json. Does nothing if the song was deleted."""
    with _lock:
        meta = read_meta(d)
        if meta is None:
            return None
        meta.update(changes)
        try:
            _write_meta(d, meta)
        except OSError:
            return None
        return meta


def log(d: Path, text: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        if not d.is_dir():
            return
        try:
            with open(d / "log.txt", "a", encoding="utf-8") as f:
                f.write(f"{stamp}  {text.rstrip()}\n")
        except OSError:
            pass


def log_tail(d: Path, n: int = LOG_TAIL) -> str:
    try:
        lines = (d / "log.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def list_songs() -> list[dict]:
    out = _out_dir()
    songs = []
    if out.is_dir():
        for d in out.iterdir():
            if d.is_dir() and _ID_RE.fullmatch(d.name):
                meta = read_meta(d)
                if meta is not None:
                    songs.append(meta)
    songs.sort(key=lambda m: str(m.get("added", "")), reverse=True)
    return songs


def _require_song(song_id: str) -> tuple[Path, dict]:
    if not _ID_RE.fullmatch(song_id):
        abort(404)
    d = _out_dir() / song_id
    meta = read_meta(d)
    if meta is None:
        abort(404)
    return d, meta


def new_meta(song_id: str, filename: str, kind: str, mode: str) -> dict:
    title = filename.rsplit(".", 1)[0].strip() or "Untitled"
    return {
        "id": song_id, "title": title, "filename": filename, "kind": kind, "mode": mode,
        "added": datetime.now().isoformat(timespec="seconds"),
        "status": "queued", "step": 0, "total": 0, "message": "Waiting to start", "error": "",
    }


def _friendly_error(e: BaseException) -> str:
    text = str(e).strip()
    return _FRIENDLY.get(text.lower(), text or type(e).__name__)


# ---------------------------------------------------------------- background work

def _ensure_worker() -> None:
    global _worker
    with _worker_guard:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work_forever, name="analysis", daemon=True)
            _worker.start()


def _work_forever() -> None:
    while True:
        song_id = _jobs.get()
        try:
            run_job(song_id)
        except Exception:  # one bad song must not stop the queue
            traceback.print_exc()
        finally:
            _jobs.task_done()


def queue_song(song_id: str) -> None:
    _jobs.put(song_id)
    _ensure_worker()


def options_for(meta: Mapping, args: Mapping | None = None) -> ArrangeOptions:
    """Options from the page's query string; "Make" defaults to the upload choice."""
    form = dict(args or {})
    form.setdefault("mode", meta.get("mode", "solo"))
    return ArrangeOptions.from_form(form)


def run_job(song_id: str) -> None:
    """Listen through one song. Called on the worker thread."""
    d = _out_dir() / song_id
    meta = read_meta(d)
    if meta is None or meta.get("status") != "queued":
        return  # deleted, or already handled
    started = time.monotonic()
    update_meta(d, status="analyzing", step=0, total=0, message="Starting")
    log(d, f"Started on {meta.get('filename')} (what is it: {meta.get('kind')})")

    def progress(step: int, total: int, message: str) -> None:
        update_meta(d, status="analyzing", step=int(step), total=int(total), message=str(message))
        log(d, f"Step {step} of {total}: {message}")

    try:
        pipeline = _pipeline()
        pipeline.analyze(d, meta.get("kind", "auto"), progress)
    except Exception as e:
        log(d, "Failed:\n" + traceback.format_exc())
        update_meta(d, status="failed", error=_friendly_error(e))
    else:
        # Write the default sheet music now, so the first visit is quick.
        update_meta(d, message="Writing out the sheet music")
        try:
            with _arrange_lock:
                pipeline.arrange_and_render(d, options_for(meta))
        except Exception:
            log(d, "Could not write the sheet music yet:\n" + traceback.format_exc())
        update_meta(d, status="done", message="Done", error="")
        log(d, f"Done in {_took(started)}")
    finally:
        if read_meta(d) is None:          # deleted while it was being worked on
            shutil.rmtree(d, ignore_errors=True)


def _took(started: float) -> str:
    s = int(time.monotonic() - started)
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def mark_interrupted() -> int:
    """Songs left half-done by a stopped app can't resume: mark them failed."""
    n = 0
    for meta in list_songs():
        if meta.get("status") in BUSY:
            d = _out_dir() / meta["id"]
            update_meta(d, status="failed", error=INTERRUPTED)
            log(d, "Interrupted: the app was stopped while this song was being worked on.")
            n += 1
    return n


# ---------------------------------------------------------------- page helpers

_MAJOR = ["C♭", "G♭", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯"]
_MINOR = ["A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯", "G♯", "D♯", "A♯"]


def key_name(fifths: int, mode: str) -> str:
    i = max(-7, min(7, int(fifths))) + 7
    if mode == "minor":
        return f"{_MINOR[i]} minor"
    return f"{_MAJOR[i]} major"


def _query(opts: ArrangeOptions) -> dict:
    q = {"mode": opts.mode, "melody_source": opts.melody_source,
         "octave": opts.octave, "piano_style": opts.piano_style, "meter": opts.meter,
         "barshift": opts.barshift}
    if opts.simple:
        q["simple"] = "1"
    if opts.easy_key:
        q["easy_key"] = "1"
    return q


def _file_url(song_id: str, d: Path, path, download: bool = False) -> str | None:
    """Link to a file the pipeline wrote, only if it sits inside the song folder."""
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(d.resolve())
    except (TypeError, ValueError, OSError):
        return None
    if not (d / rel).is_file():
        return None
    extra = {"download": "1"} if download else {}
    return url_for("song_file", song_id=song_id, name=rel.as_posix(), **extra)


def _arrange(d: Path, opts: ArrangeOptions) -> dict:
    with _arrange_lock:
        return _pipeline().arrange_and_render(d, opts)


def _scalable(svg: str) -> str:
    """Give Verovio's outer <svg> a viewBox so CSS can shrink it to the paper."""
    m = re.search(r"<svg\b[^>]*>", svg)
    if not m or "viewBox" in m.group(0):
        return svg
    tag = m.group(0)
    w = re.search(r'\swidth="([\d.]+)(?:px)?"', tag)
    h = re.search(r'\sheight="([\d.]+)(?:px)?"', tag)
    if not (w and h):
        return svg
    tag = tag[:-1] + f' viewBox="0 0 {w.group(1)} {h.group(1)}">'
    return svg[:m.start()] + tag + svg[m.end():]


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", title).strip(" ._")
    return name[:100] or "song"


@app.template_filter("nicedate")
def nicedate(iso) -> str:
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso or "")
    hour = dt.hour % 12 or 12
    return f"{dt:%b} {dt.day}, {dt.year}, {hour}:{dt:%M} {'am' if dt.hour < 12 else 'pm'}"


@app.template_filter("status_text")
def status_text(meta: Mapping) -> str:
    status = meta.get("status")
    if status == "queued":
        return "waiting to start"
    if status == "analyzing":
        if meta.get("total"):
            return f"working (step {meta.get('step')} of {meta.get('total')})"
        return "working"
    if status == "done":
        return "ready"
    if status == "failed":
        return "didn't work"
    return str(status or "unknown")


# ---------------------------------------------------------------- pages

def _index(error: str | None = None) -> str:
    return render_template("index.html", songs=list_songs(), error=error,
                           accept=",".join("." + t for t in UPLOAD_TYPES),
                           max_mb=MAX_UPLOAD_MB, max_minutes=config.MAX_SECONDS // 60)


@app.get("/")
def index():
    return _index()


@app.post("/upload")
def upload():
    f = request.files.get("file")
    filename = ""
    if f is not None and f.filename:
        filename = f.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()[:200]
    if not filename:
        return _index(error="Please choose a file first."), 400
    ext = _ext(filename)
    if ext not in UPLOAD_TYPES:
        return _index(error=f'Sorry, "{filename}" isn\'t a file this can read. Please upload '
                            "audio (mp3, wav, m4a, aac, flac or ogg) or MIDI (mid or midi)."), 400
    kind = request.form.get("kind")
    kind = kind if kind in KINDS else "auto"
    mode = request.form.get("mode")
    mode = mode if mode in MODES else "solo"

    out = _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    while True:
        song_id = secrets.token_hex(4)
        try:
            (out / song_id).mkdir()
            break
        except FileExistsError:
            continue
    d = out / song_id
    dest = d / f"input.{ext}"
    try:
        f.save(dest)
        empty = dest.stat().st_size == 0
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    if empty:
        shutil.rmtree(d, ignore_errors=True)
        return _index(error=f'"{filename}" is empty. Please choose another file.'), 400

    _write_meta(d, new_meta(song_id, filename, kind, mode))
    log(d, f"Uploaded {filename}")
    queue_song(song_id)
    return redirect(url_for("song", song_id=song_id))


@app.get("/song/<song_id>")
def song(song_id: str):
    d, meta = _require_song(song_id)
    status = meta.get("status")
    if status in BUSY:
        others = [m for m in list_songs() if m["id"] != song_id and m.get("status") == "analyzing"]
        return render_template("song.html", meta=meta, state="busy", other_busy=bool(others))
    if status != "done":
        return render_template("song.html", meta=meta, state="failed", log_tail=log_tail(d))

    opts = options_for(meta, request.args.to_dict())
    ctx = {"meta": meta, "state": "done", "opts": opts,
           "print_url": url_for("print_page", song_id=song_id, **_query(opts))}
    try:
        result = _arrange(d, opts)
    except Exception as e:
        log(d, f"Could not make the sheet music ({opts.key()}):\n" + traceback.format_exc())
        return render_template("song.html", arrange_error=_friendly_error(e), **ctx), 500

    arr = result.get("arr")
    facts = None
    if arr is not None:
        facts = {"key": key_name(arr.key_fifths, arr.key_mode),
                 "time": f"{arr.beats_per_bar}/4", "tempo": round(arr.bpm)}
    ext = _ext(meta.get("filename", ""))
    original_url = None
    if ext not in MIDI_TYPES:
        original_url = _file_url(song_id, d, d / f"input.{ext}")
    return render_template(
        "song.html", **ctx,
        svgs=list(result.get("svgs") or []),
        warnings=list(result.get("warnings") or []),
        facts=facts,
        preview_url=_file_url(song_id, d, result.get("mp3")),
        original_url=original_url,
        original_is_midi=ext in MIDI_TYPES,
        musicxml_url=_file_url(song_id, d, result.get("musicxml"), download=True),
        midi_url=_file_url(song_id, d, result.get("midi"), download=True),
    )


@app.get("/song/<song_id>/print")
def print_page(song_id: str):
    d, meta = _require_song(song_id)
    if meta.get("status") != "done":
        return redirect(url_for("song", song_id=song_id))
    opts = options_for(meta, request.args.to_dict())
    back_url = url_for("song", song_id=song_id, **_query(opts))
    try:
        result = _arrange(d, opts)
    except Exception as e:
        log(d, f"Could not make the sheet music ({opts.key()}):\n" + traceback.format_exc())
        return render_template("message.html", title="Couldn't make the sheet music",
                               message=_friendly_error(e), back_url=back_url), 500
    svgs = [_scalable(s) for s in result.get("svgs") or []]
    # Verovio usually prints the title at the top of page 1; don't print it twice.
    title_text = ">" + html.escape(meta.get("title", ""), quote=False) + "<"
    show_title = not (svgs and title_text in svgs[0])
    return render_template("print.html", meta=meta, svgs=svgs, back_url=back_url,
                           show_title=show_title)


@app.get("/song/<song_id>/file/<path:name>")
def song_file(song_id: str, name: str):
    d, meta = _require_song(song_id)
    kwargs = {}
    if request.args.get("download") == "1":
        kwargs = {"as_attachment": True,
                  "download_name": _safe_filename(meta.get("title", "")) + Path(name).suffix}
    return send_from_directory(d, name, **kwargs)   # 404 for anything outside d


@app.post("/song/<song_id>/delete")
def delete(song_id: str):
    if _ID_RE.fullmatch(song_id):
        with _lock:
            shutil.rmtree(_out_dir() / song_id, ignore_errors=True)
    return redirect(url_for("index"))


@app.errorhandler(404)
def not_found(e):
    return render_template("message.html", title="Not found",
                           message="That page or song isn't here. It may have been deleted."), 404


@app.errorhandler(413)
def too_big(e):
    return _index(error=f"That file is too big. The limit is {MAX_UPLOAD_MB} MB."), 413


def main() -> None:
    n = mark_interrupted()
    if n:
        print(f"Marked {n} unfinished song(s) as interrupted.")
    print(f"Flute Arranger is running: open http://localhost:{config.PORT} in your browser.")
    print("Press Ctrl-C to stop.")
    app.run(host="127.0.0.1", port=config.PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
