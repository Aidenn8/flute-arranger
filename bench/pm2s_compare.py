"""PM2S against Beat This! for piano recordings, scored against real sheet music.

    heat run .venv/bin/python bench/pm2s_compare.py                 # all piano pieces, no listening
    heat run .venv/bin/python bench/pm2s_compare.py --listen        # also the listening check (slow)
    .venv/bin/python bench/pm2s_compare.py --piece gymnopedie_1 --variant pm2s

The piano pieces of the reference set (bench/REFERENCES.md), each from its cached
analysis (out/ref/<piece>/analysis.json; bench/ref_score.py makes them), arranged
with the default options (flute alone) in these ways:

  beatthis   the analysis as it is: Beat This! beats and bars (from the audio), and
             the rule-based rhythm reading (rhythm.quantize_line)
  pm2s       PM2S beats and bars (sheetswap.pm2s_beats, from the piano transcription's
             notes), then the same pipeline: beat unit and bar lines from the tune
             (beats.beat_unit, beats.melody_downbeat), rule-based rhythm
  pm2s_q     PM2S beats and bars, and the tune's onsets and lengths where PM2S's
             quantisation model puts them (pm2s_beats.quantize_tune)
  beatthis_q Beat This! beats and bars, the tune's onsets and lengths from PM2S's
             quantisation (placed on its own beats, then read onto Beat This!'s grid)

and scored exactly as bench/ref_score.py scores the app (bench/ref_score.run_piece):
notes found, onsets exactly right / within a 16th, lengths right, ties per bar,
meter, tempo ratio, and with --listen the listening deviation. Work files go to
out/pm2s_compare/<variant>/<piece>/; the table to bench/pm2s_results.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import ref_score  # noqa: E402
from sheetswap import pm2s_beats, rhythm  # noqa: E402
from sheetswap.config import OUT_DIR  # noqa: E402

OUT = OUT_DIR / "pm2s_compare"
MD = ROOT / "bench" / "pm2s_results.md"
VARIANTS = ("beatthis", "pm2s", "pm2s_q", "beatthis_q")
_quantize_line = rhythm.quantize_line


def piano_pieces() -> list[str]:
    out = []
    for slug in ref_score.pieces_registry():
        a = ref_score.WORK / slug / "analysis.json"
        if a.exists() and json.loads(a.read_text())["kind"] == "piano":
            out.append(slug)
    return out


def reading_for(slug: str, an):
    """PM2S on the piano transcription (cached per piece)."""
    cache = OUT / "readings" / f"{slug}.json"
    if cache.exists():
        return pm2s_beats.Reading(**json.loads(cache.read_text()))
    t0 = time.time()
    r = pm2s_beats.read(an.sources["piano"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(r.__dict__))
    print(f"{slug}: PM2S read {len(an.sources['piano'])} notes in {time.time() - t0:.1f} s "
          f"({r.time_signature}, {len(r.beats)} beats)", flush=True)
    return r


def run(slug: str, variant: str, listen: bool) -> dict:
    an = ref_score.analyze(slug, False)
    if variant != "beatthis":
        r = reading_for(slug, an)
    if variant.startswith("pm2s"):
        an = replace(an, grid=pm2s_beats.grid(r))
    rhythm.quantize_line = _quantize_line
    if variant in ("pm2s_q", "beatthis_q"):
        piano = an.sources["piano"]
        rhythm.quantize_line = lambda melody, grid, simple=False: pm2s_beats.quantize_tune(
            melody, piano, r, grid, simple)
    try:
        ref_score.WORK = OUT / variant
        d = ref_score.WORK / slug
        d.mkdir(parents=True, exist_ok=True)
        audio = OUT_DIR / "ref" / slug / "audio.wav"
        if not (d / "audio.wav").exists() and audio.exists():
            (d / "audio.wav").symlink_to(audio)
        return ref_score.run_piece(slug, an, listen)
    finally:
        rhythm.quantize_line = _quantize_line
        ref_score.WORK = OUT_DIR / "ref"


COLS = [("found", ("found_pct",)), ("onset exact", ("onset_exact_pct",)),
        ("±16th", ("onset_16th_pct",)), ("same length", ("dur_exact_pct",)),
        ("ties/bar", ("notation", "ties_per_bar")), ("meter", ("raw", "meter")),
        ("meter ok", ("meter_ok",)), ("tempo ratio", ("tempo_ratio",)),
        ("listen dev (ms)", ("listen", "vs_reference", "dev_median_ms"))]


def write_md(rows: dict[str, dict[str, dict]], listen: bool) -> None:
    g = ref_score.g
    L = ["# PM2S vs Beat This! on piano recordings", "",
         "`bench/pm2s_compare.py`: the piano pieces of the reference set, default options, flute",
         "alone, scored as `bench/ref_score.py` scores them. Variants: **beatthis** (the app",
         "as it is: Beat This! beats and bars, rule-based rhythm), **pm2s** (PM2S beats and",
         "bars from the piano transcription, then the same pipeline; keeping PM2S's own bar",
         "lines instead of moving them to the tune's long notes changed nothing on any piece),",
         "**pm2s_q** (PM2S beats and its quantised onsets and lengths), **beatthis_q** (Beat",
         "This! beats and bars, PM2S's quantised onsets and lengths).", ""]
    variants = [v for v in VARIANTS if any(v in r for r in rows.values())]
    for name, path in COLS:
        if path[0] == "listen" and not listen:
            continue
        L += [f"## {name}", "", "| piece | " + " | ".join(variants) + " |",
              "|---|" + "---|" * len(variants)]
        for slug, r in rows.items():
            L.append(f"| {slug} | " + " | ".join(str(g(r.get(v, {}), *path)) for v in variants) + " |")
        means = []
        for v in variants:
            vals = [g(r.get(v, {}), *path, default=None) for r in rows.values()]
            nums = [x for x in vals if isinstance(x, (int, float))]
            oks = [x for x in vals if x in ("yes", "no")]
            means.append(f"{np.mean(nums):.2f}" if nums
                         else f"{oks.count('yes')}/{len(oks)}" if oks else "–")
        L += ["| **mean** (yes: count) | " + " | ".join(means) + " |", ""]
    # what else PM2S reads: key signature and bar type, against the score and the app
    L += ["## Key and time signature", "",
          "| piece | score | app's key | PM2S key signature (as a major key) | PM2S bar |",
          "|---|---|---|---|---|"]
    for slug, r in rows.items():
        ref = json.loads((ref_score.REF / slug / "ref.json").read_text())
        cache = OUT / "readings" / f"{slug}.json"
        rd = json.loads(cache.read_text()) if cache.exists() else {}
        app = r.get("beatthis", {}).get("ours_key", "–")
        L.append(f"| {slug} | {ref['key']}, {ref['meter'][0]}/{ref['meter'][1]} | {app} | "
                 f"{rd.get('key', '–')} | {rd.get('time_signature', '–')} |")
    # the verdict, from the onset column
    L += ["", "## Verdict", ""]
    base = {slug: g(r.get("beatthis", {}), "onset_exact_pct", default=None) for slug, r in rows.items()}
    for v in variants[1:]:
        pairs = [(base[slug], g(r.get(v, {}), "onset_exact_pct", default=None)) for slug, r in rows.items()]
        pairs = [(a, b) for a, b in pairs if isinstance(a, (int, float)) and isinstance(b, (int, float))]
        better = sum(b > a + 1 for a, b in pairs)
        worse = sum(b < a - 1 for a, b in pairs)
        L.append(f"- **{v}**: tune notes exactly where the score has them {np.mean([b for _, b in pairs]):.1f}% "
                 f"on average against {np.mean([a for a, _ in pairs]):.1f}% for beatthis; more than a point "
                 f"better on {better} of {len(pairs)} pieces, worse on {worse}.")
    L += ["", "So the app keeps Beat This! and its own rhythm reading for piano recordings; PM2S stays",
          "installed for this comparison and as an opt-in (`SHEETSWAP_PIANO_BEATS=pm2s`, see",
          "`pipeline.find_beats`)."]
    MD.write_text("\n".join(L) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--piece", action="append", default=[])
    ap.add_argument("--variant", action="append", default=[])
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--report", action="store_true", help="only rewrite the table from saved results")
    a = ap.parse_args()
    if a.report:
        rows = {slug: {v: json.loads((OUT / v / slug / "result.json").read_text()) for v in VARIANTS
                       if (OUT / v / slug / "result.json").exists()} for slug in piano_pieces()}
        write_md(rows, any("listen" in r for vs in rows.values() for r in vs.values()))
        return
    slugs = a.piece or piano_pieces()
    variants = a.variant or list(VARIANTS)
    rows: dict[str, dict[str, dict]] = {}
    for slug in slugs:
        rows[slug] = {}
        for v in variants:
            r = run(slug, v, a.listen)
            rows[slug][v] = r
            print(f"{slug} {v}: found {r.get('found_pct')}% onset exact {r.get('onset_exact_pct')}% "
                  f"±16th {r.get('onset_16th_pct')}% length {r.get('dur_exact_pct')}% "
                  f"ties/bar {r.get('notation', {}).get('ties_per_bar')} meter {r.get('raw', {}).get('meter')} "
                  f"ok={r.get('meter_ok')} tempo x{r.get('tempo_ratio')} listen "
                  f"{(r.get('listen') or {}).get('vs_reference', {}).get('dev_median_ms')} ms", flush=True)
    if not a.piece and not a.variant:
        write_md(rows, a.listen)


if __name__ == "__main__":
    main()
