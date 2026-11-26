"""Rules vs learned tune picker on the reference recordings (bench/ref_score.py's judge).

    .venv/bin/python bench/tune_picker_judge.py                 # all pieces, with listening
    .venv/bin/python bench/tune_picker_judge.py --no-listen --piece fur_elise

Both pickers run in one process on the cached analyses (out/ref/<piece>/analysis.json,
read only), so every other step (beats, rhythm, flute) is the same code for both. The
measures are bench/ref_score.py's own (align, measure, listen); its files are written to
a scratch folder (--work), not out/ref/, so a ref_score.py run elsewhere is not disturbed.
Prints one row per piece and writes bench/tune_picker_judge.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import ref_score as rs  # noqa: E402
from sheetswap import melody, pipeline  # noqa: E402
from sheetswap.model import ArrangeOptions  # noqa: E402

MD = ROOT / "bench" / "tune_picker_judge.md"
COLS = [("found_pct", "found"), ("extra_pct", "extra"), ("onset_exact_pct", "onset exact"),
        ("dur_exact_pct", "same length"), ("listen.vs_reference.dev_median_ms", "listen dev median ms"),
        ("listen.vs_reference.dev_p90_ms", "listen dev p90 ms")]


def get(row: dict, path: str):
    for k in path.split("."):
        if not isinstance(row, dict) or row.get(k) is None:
            return None
        row = row[k]
    return row


def arrange(slug: str, an, work: Path) -> dict:
    """ref_score.arrange without the page image (only the score files the measures read)."""
    from sheetswap.score import write_outputs
    opts = ArrangeOptions(mode="solo")
    arr = pipeline.arrange(an, opts)
    tune, _, _ = pipeline.pick_melody(an, opts.melody_source)
    grid = pipeline.bar_grid(an, opts, tune)
    paths = write_outputs(arr, rs.pieces_registry()[slug]["title"] + " (flute)", work / slug)
    return dict(arr=arr, grid=grid, xml=paths["musicxml"], pages=0, tune=tune)


def run(slug: str, picker: str, work: Path, listen: bool) -> dict:
    """One piece with one picker ("rules" or "learned"), measured as ref_score.py does."""
    os.environ[melody.PICKER_ENV] = picker
    src = rs.WORK / slug
    d = work / slug
    d.mkdir(parents=True, exist_ok=True)
    for name in ("analysis.json", "audio.wav"):
        if not (d / name).exists() and (src / name).exists():
            (d / name).symlink_to(src / name)
    an = pipeline.load_analysis(src)
    ref = json.loads((rs.REF / slug / "ref.json").read_text())
    old = rs.WORK
    rs.WORK = work
    try:
        res = arrange(slug, an, work)
        al = rs.align(ref, res["arr"])
        m = rs.measure(ref, res["arr"], al)
        used = pipeline.pick_melody(an, "auto")[1]
        row = dict(piece=slug, picker=picker, instrument=ref["instrument"], kind=an.kind,
                   tune_notes=len(res["arr"].flute),
                   learned=picker != "rules" and used == "piano" and melody.tune_model() is not None,
                   **{k: v for k, v in m.items() if not k.startswith("_")})
        if listen:
            row["listen"] = rs.listen(slug, ref, res, an, m)
    finally:
        rs.WORK = old
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--piece", action="append", default=[])
    ap.add_argument("--no-listen", action="store_true")
    ap.add_argument("--work", default=str(ROOT / "out" / "tune_picker_judge"))
    ap.add_argument("--md", default=str(MD))
    a = ap.parse_args()
    if melody.tune_model() is None:
        sys.exit("no trained model in models/tune_picker/ (run bench/train_tune_picker.py)")
    from sheetswap import rhythm
    reader = rhythm.rhythm_reader() if hasattr(rhythm, "rhythm_reader") else "rules"
    print(f"rhythm reader (same for both): {reader}", flush=True)
    work = Path(a.work)
    slugs = a.piece or [s for s in rs.pieces_registry() if (rs.WORK / s / "analysis.json").exists()
                        and (rs.REF / s / "ref.json").exists()]
    rows = []
    for slug in slugs:
        t0 = time.time()
        pair = [run(slug, p, work, not a.no_listen) for p in ("rules", "learned")]
        rows += pair
        r, l_ = pair
        cells = "  ".join(f"{label} {get(r, k)}->{get(l_, k)}" for k, label in COLS)
        print(f"{slug:20s} {r['instrument']:6s} learned={l_['learned']}  {cells}  ({time.time() - t0:.0f} s)",
              flush=True)
    write_md(rows, "learned", reader, Path(a.md))


def write_md(rows: list[dict], compare: str, reader: str, md: Path) -> None:
    import numpy as np
    L = [f"# Tune picker on the reference recordings: rules vs {compare}", "",
         "Written by `bench/tune_picker_judge.py` (bench/ref_score.py's measures, default options, flute",
         f"alone, cached analyses, rhythm reader: {reader}). Each cell: rules -> {compare}. *learned",
         "used*: whether the learned picker chose the tune (only piano recordings use it).", "",
         "| piece | instr. | learned used | " + " | ".join(lbl for _, lbl in COLS) + " |",
         "|---|---|---|" + "---|" * len(COLS)]
    by = {}
    for r in rows:
        by.setdefault(r["piece"], {})[r["picker"]] = r
    means = {p: {k: [] for k, _ in COLS} for p in ("rules", "learned")}
    for piece, d in by.items():
        r, l_ = d["rules"], d[compare]
        L.append(f"| {piece} | {r['instrument']} | {'yes' if l_['learned'] else 'no'} | "
                 + " | ".join(f"{get(r, k)} -> {get(l_, k)}" for k, _ in COLS) + " |")
        if r["instrument"] == "piano":
            for p, row in (("rules", r), ("learned", l_)):
                for k, _ in COLS:
                    if isinstance(get(row, k), (int, float)):
                        means[p][k].append(get(row, k))
    L.append("| **piano mean** | | | " + " | ".join(
        f"{np.mean(means['rules'][k]):.1f} -> {np.mean(means['learned'][k]):.1f}"
        if means["rules"][k] and means["learned"][k] else "–" for k, _ in COLS) + " |")
    md.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
