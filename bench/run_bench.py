"""Score the pipeline on test music whose melody is known.

    .venv/bin/python bench/run_bench.py            # all sets
    .venv/bin/python bench/run_bench.py A --limit 3

For every piece: analyze the audio (cached in out/bench/), pick the melody,
arrange it for flute (solo and duet), run the playability checks, and compare
with the true melody:
  melody F1  raw melody notes (seconds) vs truth, onset within 80 ms, same pitch class
  flute F1   final flute part (ticks -> seconds) vs truth, same pitch class
Writes bench/results.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import mir_eval
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sheetswap import pipeline  # noqa: E402
from sheetswap.config import OUT_DIR, TICKS_PER_BEAT  # noqa: E402
from sheetswap.model import ArrangeOptions  # noqa: E402

GT = ROOT / "samples" / "gt"
SETS = {"A": ("A_piano.wav", "piano"), "B": ("B_mix.wav", "auto"), "C": ("C_arpeggio.wav", "piano")}
ONSET_TOL = 0.08


def _arrays(rows: list[tuple[int, float, float]]):
    rows = [r for r in rows if r[2] > r[1]]
    if not rows:
        return np.zeros((0, 2)), np.zeros(0)
    iv = np.array([[s, e] for _, s, e in rows])
    # fold to one octave so an octave move still counts as the right note
    hz = np.array([mir_eval.util.midi_to_hz(60 + p % 12) for p, _, _ in rows])
    return iv, hz


def f1(truth: list[tuple[int, float, float]], est: list[tuple[int, float, float]]) -> float:
    ri, rp = _arrays(truth)
    ei, ep = _arrays(est)
    if len(ri) == 0 or len(ei) == 0:
        return 0.0
    _, _, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ri, rp, ei, ep, onset_tolerance=ONSET_TOL, pitch_tolerance=50, offset_ratio=None)
    return float(f)


def song_dir_for(set_name: str, piece: str, wav: Path) -> Path:
    d = OUT_DIR / "bench" / f"{set_name}_{piece}"
    if not d.exists():
        d.mkdir(parents=True)
        shutil.copy(wav, d / "input.wav")
        (d / "meta.json").write_text(json.dumps({"title": piece, "status": "analyzing"}))
    return d


def run_piece(set_name: str, piece: str) -> dict:
    fname, kind = SETS[set_name]
    wav = GT / piece / fname
    truth = [tuple(r) for r in json.loads((GT / piece / "truth.json").read_text())]
    d = song_dir_for(set_name, piece, wav)
    t0 = time.time()
    an = pipeline.analyze(d, kind)
    t_an = time.time() - t0

    melody, used, _ = pipeline.pick_melody(an, "auto")
    mel_rows = [(n.pitch, n.start, n.end) for n in melody]

    row = {"set": set_name, "piece": piece, "kind": an.kind, "source": used,
           "melody_f1": f1(truth, mel_rows), "analyze_s": round(t_an, 1)}
    for mode in ("solo", "duet"):
        arr = pipeline.arrange(an, ArrangeOptions(mode=mode))
        g = an.grid
        flute_rows = [(q.pitch, g.to_seconds(q.start / TICKS_PER_BEAT),
                       g.to_seconds(q.end / TICKS_PER_BEAT)) for q in arr.flute]
        if mode == "solo":
            row["flute_f1"] = f1(truth, flute_rows)
            row["meter"] = f"{arr.beats_per_bar}/4"
            row["bpm"] = arr.bpm
        row[f"{mode}_problems"] = list(arr.warnings)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="*", default=list(SETS))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    index = json.loads((GT / "index.json").read_text())
    rows = []
    for s in a.sets:
        for p in index[s][: a.limit or None]:
            r = run_piece(s, p)
            rows.append(r)
            print(f"{s} {p:14s} kind={r['kind']:5s} src={r['source']:6s} "
                  f"melody F1={r['melody_f1']:.2f} flute F1={r['flute_f1']:.2f} "
                  f"{r['meter']} {r['bpm']}bpm "
                  f"problems={len(r['solo_problems']) + len(r['duet_problems'])} "
                  f"({r['analyze_s']}s)", flush=True)

    lines = ["# Bench results", "",
             "Melody F1: picked tune vs the true soprano line (onset within 80 ms, same pitch class).",
             "Flute F1: the final flute part, same comparison. Check failures: playability checks.", "",
             "| set | pieces | mean melody F1 | mean flute F1 | min flute F1 | check failures |",
             "|---|---|---|---|---|---|"]
    for s in a.sets:
        rs = [r for r in rows if r["set"] == s]
        if not rs:
            continue
        ff = [r["flute_f1"] for r in rs]
        probs = sum(len(r["solo_problems"]) + len(r["duet_problems"]) for r in rs)
        lines.append(f"| {s} | {len(rs)} | {np.mean([r['melody_f1'] for r in rs]):.2f} | "
                     f"{np.mean(ff):.2f} | {min(ff):.2f} | {probs} |")
    lines += ["", "| set | piece | kind | tune from | melody F1 | flute F1 | meter | tempo |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['set']} | {r['piece']} | {r['kind']} | {r['source']} | "
                     f"{r['melody_f1']:.2f} | {r['flute_f1']:.2f} | {r['meter']} | {r['bpm']} |")
    problems = [(r["piece"], p) for r in rows for p in r["solo_problems"] + r["duet_problems"]]
    if problems:
        lines += ["", "Check failures:", ""] + [f"- {p}: {w}" for p, w in problems]
    (ROOT / "bench" / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
