"""Command line: arrange one file without the web page.

    .venv/bin/python -m sheetswap.cli song.mp3 --mode duet
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import pipeline
from .config import OUT_DIR
from .model import METERS, ArrangeOptions, normalize_meter


def new_song(src: Path, kind: str, mode: str, out_dir: Path = OUT_DIR) -> Path:
    song_id = secrets.token_hex(4)
    song_dir = out_dir / song_id
    song_dir.mkdir(parents=True)
    shutil.copy(src, song_dir / ("input" + src.suffix.lower()))
    meta = {"id": song_id, "title": src.stem, "filename": src.name, "kind": kind,
            "mode": mode, "added": datetime.now().isoformat(timespec="seconds"),
            "status": "analyzing", "step": 0, "total": 0, "message": "", "error": ""}
    (song_dir / "meta.json").write_text(json.dumps(meta))
    return song_dir


def _set_meta(song_dir: Path, **kw) -> None:
    p = song_dir / "meta.json"
    meta = json.loads(p.read_text()) if p.exists() else {}
    meta.update(kw)
    p.write_text(json.dumps(meta))


def meter_arg(text: str) -> str:
    """--meter: auto, 2/4, 3/4, 4/4, 3/8, 6/8, 9/8 or 12/8 (0, 3, 4 = the old auto, 3/4, 4/4)."""
    m = normalize_meter(text)
    if m == "auto" and text.strip() not in ("auto", "0"):
        raise argparse.ArgumentTypeError(
            f"unknown time {text!r}: use auto, {', '.join(METERS)}")
    return m


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Turn a song into flute sheet music.")
    ap.add_argument("file", type=Path, nargs="?")
    ap.add_argument("--kind", default="auto", choices=["auto", "piano", "song", "band"])
    ap.add_argument("--mode", default="solo", choices=["solo", "duet"])
    ap.add_argument("--melody", default="auto", choices=["auto", "vocals", "piano", "other"])
    ap.add_argument("--octave", type=int, default=0, choices=[-1, 0, 1])
    ap.add_argument("--simple", action="store_true")
    ap.add_argument("--easy-key", action="store_true")
    ap.add_argument("--piano-style", default="chords", choices=["chords", "cleaned"])
    ap.add_argument("--meter", type=meter_arg, default="auto",
                    help="time signature: auto (as heard), " + ", ".join(METERS))
    ap.add_argument("--barshift", type=int, default=0, choices=[0, 1, 2, 3],
                    help="move the bar lines this many beats later")
    ap.add_argument("--tempo", type=int, default=0, choices=[-1, 0, 1],
                    help="-1 = half as fast, 1 = twice as fast")
    ap.add_argument("--song-dir", type=Path, help="re-use an existing song folder")
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.song_dir and not a.file:
        ap.error("give a file or --song-dir")

    song_dir = a.song_dir or new_song(a.file, a.kind, a.mode)
    t0 = time.time()

    def progress(step: int, total: int, msg: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] step {step}/{total}: {msg}", flush=True)
        _set_meta(song_dir, step=step, total=total, message=msg)

    try:
        an = pipeline.analyze(song_dir, a.kind, progress)
    except BaseException as e:            # incl. Ctrl-C: don't leave the song "working" forever
        _set_meta(song_dir, status="failed", error=str(e) or type(e).__name__)
        raise
    _set_meta(song_dir, status="done", kind=an.kind)
    opts = ArrangeOptions(mode=a.mode, melody_source=a.melody, octave=a.octave,
                          simple=a.simple, easy_key=a.easy_key, piano_style=a.piano_style,
                          meter=a.meter, barshift=a.barshift, tempo=a.tempo)
    res = pipeline.arrange_and_render(song_dir, opts)
    arr = res["arr"]
    print(f"[{time.time() - t0:6.1f}s] done: kind={an.kind} flute notes={len(arr.flute)} "
          f"piano notes={len(arr.rh) + len(arr.lh)} key fifths={arr.key_fifths} {arr.key_mode} "
          f"{arr.time_signature[0]}/{arr.time_signature[1]} tempo {arr.bpm}")
    for w in res["warnings"]:
        print("  warning:", w)
    print("  folder:", song_dir)
    print("  musicxml:", res["musicxml"])
    print("  preview:", res["mp3"])


if __name__ == "__main__":
    main()
