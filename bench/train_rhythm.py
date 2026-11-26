"""Train the learned rhythm reader (sheetswap.rhythm.quantize_line_learned) on ASAP.

Every step writes into data/rhythm/ (git-ignored) and can be run again on its own:

    .venv/bin/python -I bench/train_rhythm.py prepare            # ASAP: score <-> performance notes, splits
    heat run .venv/bin/python -I bench/train_rhythm.py render    # a subset: synth -> piano model -> Beat This!
    .venv/bin/python -I bench/train_rhythm.py stats              # what transcription and the beat grid get wrong
    heat run .venv/bin/python -I bench/train_rhythm.py build     # training lines (simulated + real transcriptions)
    heat run .venv/bin/python -I bench/train_rhythm.py train     # the model -> models/rhythm/
    .venv/bin/python -I bench/train_rhythm.py evaluate           # learned vs rules on held-out pieces
    heat run .venv/bin/python -I bench/train_rhythm.py judge     # bench/ref_score.py with each reader

Data: ASAP (github.com/fosfrancesco/asap-dataset, CC BY-NC-SA 4.0), unzipped into
data/asap/asap-dataset-master/. Download:

    mkdir -p data/asap_zip data/asap
    curl -L -o data/asap_zip/asap-master.zip \\
        https://github.com/fosfrancesco/asap-dataset/archive/refs/heads/master.zip
    (cd data/asap && unzip -q ../asap_zip/asap-master.zip)

What the model learns (bench/TRAINING.md has the details): for each note of the tune
line, as our pipeline sees it (transcribed notes, picked by melody.piano_melody +
fill_tune, timed against a beat grid that is a little off), where the score writes it
inside the beat (and which beat), and how long a rest follows it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
import random
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sheetswap import rhythm  # noqa: E402
from sheetswap.model import BeatGrid, Note  # noqa: E402

ASAP = ROOT / "data" / "asap" / "asap-dataset-master"
DATA = ROOT / "data" / "rhythm"
MODEL_DIR = ROOT / "models" / "rhythm"

warnings.filterwarnings("ignore", message=".*non-zero tracks.*")

# ---------------------------------------------------------------- splits

# Pieces held out of training whatever the hash says: the same work as a piece of the
# judge (bench/ref_score.py: Moonlight's 1st movement there, ASAP has the 3rd).
FORCE_TEST = {("Beethoven", "Piano_Sonatas_14")}
VAL_SHARE, TEST_SHARE = 0.1, 0.1


def piece_key(composer: str, title: str) -> tuple[str, str]:
    """One piece, however many score versions (repeats played or not) ASAP keeps of it."""
    t = re.sub(r"_(no_2_repeat|no_repeat|repeat)$", "", title)
    return composer, t


def split_of(composer: str, title: str) -> str:
    key = piece_key(composer, title)
    for c, prefix in FORCE_TEST:
        if key[0] == c and key[1].startswith(prefix):
            return "test"
    h = int(hashlib.sha1(f"{key[0]}/{key[1]}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < TEST_SHARE:
        return "test"
    if h < TEST_SHARE + VAL_SHARE:
        return "val"
    return "train"


# ---------------------------------------------------------------- beats at our level

# How the app writes a time signature's beat: quarter (simple), dotted (compound) or
# eighth; and how many of our beats one ASAP beat holds (half-note beats split in two).
def beat_kind(ts: str) -> tuple[int, int]:
    """(our beat_ticks, our beats per ASAP beat) for an ASAP time signature string."""
    num, den = (int(x) for x in ts.split("/"))
    if den == 2:
        return rhythm.QUARTER, 2
    if num % 3 == 0 and num > 3 and den in (4, 8, 16):
        return rhythm.DOTTED_BEAT, 1
    if den == 8 or den == 16:
        return rhythm.EIGHTH_BEAT, 1
    return rhythm.QUARTER, 1


def our_beats(score_beats, perf_beats, downbeats_score, ts_changes):
    """ASAP's beats (score and performance seconds) at the level the app writes, as
    (score_s, perf_s, unit, downbeat, beats_per_bar) arrays. Half-note beats get a
    beat between them (halfway, in score and in performance time)."""
    sb, pb = np.asarray(score_beats, float), np.asarray(perf_beats, float)
    changes = sorted((float(t), v) for t, v in ts_changes.items())
    downs = np.asarray(sorted(float(x) for x in downbeats_score))
    out = []
    for k in range(len(sb)):
        cur = changes[0][1] if changes else ["4/4", 4]
        for t, v in changes:
            if t <= sb[k] + 1e-6:
                cur = v
        unit, split = beat_kind(cur[0])
        bpb = int(cur[1]) * split
        is_down = bool(len(downs)) and np.min(np.abs(downs - sb[k])) < 1e-4
        out.append((sb[k], pb[k], unit, is_down, bpb))
        if split == 2 and k + 1 < len(sb):
            out.append(((sb[k] + sb[k + 1]) / 2, (pb[k] + pb[k + 1]) / 2, unit, False, bpb))
    a = np.array([(s, p) for s, p, *_ in out])
    return (a[:, 0], a[:, 1], np.array([o[2] for o in out]), np.array([o[3] for o in out]),
            np.array([o[4] for o in out]))


def beat_pos(t, beats):
    """Seconds -> fractional beat index (linear past the ends)."""
    t = np.asarray(t, float)
    idx = np.interp(t, beats, np.arange(len(beats)))
    lo, hi = t < beats[0], t > beats[-1]
    if len(beats) >= 2:
        idx = np.where(lo, (t - beats[0]) / (beats[1] - beats[0]), idx)
        idx = np.where(hi, len(beats) - 1 + (t - beats[-1]) / (beats[-1] - beats[-2]), idx)
    return idx


def pos_time(pos, beats):
    """Fractional beat index -> seconds (inverse of beat_pos)."""
    pos = np.asarray(pos, float)
    n = len(beats)
    t = np.interp(pos, np.arange(n), beats)
    if n >= 2:
        t = np.where(pos < 0, beats[0] + pos * (beats[1] - beats[0]), t)
        t = np.where(pos > n - 1, beats[-1] + (pos - n + 1) * (beats[-1] - beats[-2]), t)
    return t


# ---------------------------------------------------------------- score <-> performance

def _notes(pm):
    rows = [(n.pitch, n.start, n.end, n.velocity, k) for k, i in enumerate(pm.instruments)
            if not i.is_drum for n in i.notes]
    rows.sort(key=lambda r: (r[1], -r[0]))
    return np.array(rows, dtype=float).reshape(-1, 5)


def _pedal(pm) -> list[tuple[float, float]]:
    """(down, up) seconds of the sustain pedal (CC64 >= 64)."""
    ev = sorted((c.time, c.value) for i in pm.instruments for c in i.control_changes if c.number == 64)
    spans, down = [], None
    for t, v in ev:
        if v >= 64 and down is None:
            down = t
        elif v < 64 and down is not None:
            spans.append((down, t))
            down = None
    if down is not None:
        spans.append((down, max(t for t, _ in ev) + 1.0))
    return spans


def match_notes(score, perf, s_pos, sb_perf, tol_beats=0.45, tol_s=0.25):
    """Each performance note's score note (index or -1): same pitch, the score note's
    expected performance time (through the annotated beats) nearest, one to one."""
    expect = pos_time(s_pos, sb_perf)
    beat_len = np.interp(s_pos, np.arange(len(sb_perf) - 1) + 0.5, np.diff(sb_perf)) if len(sb_perf) > 1 \
        else np.full(len(s_pos), 0.5)
    match = np.full(len(perf), -1)
    for p in np.unique(perf[:, 0]):
        pi = np.flatnonzero(perf[:, 0] == p)
        si = np.flatnonzero(score[:, 0] == p)
        if not len(si):
            continue
        cand = []
        for j in si:
            tol = min(tol_s, tol_beats * beat_len[j]) + 0.05
            d = np.abs(perf[pi, 1] - expect[j])
            for k in np.flatnonzero(d <= tol):
                cand.append((d[k], pi[k], j))
        cand.sort()
        used_s = set()
        for _, i, j in cand:
            if match[i] < 0 and j not in used_s:
                match[i] = j
                used_s.add(j)
    return match


def prepare() -> None:
    """ASAP -> data/rhythm/asap.pkl: per performance its notes, the score place of each
    (in our beats), the annotated beats at our level, pedal, split."""
    import pandas as pd
    import pretty_midi
    ann = json.loads((ASAP / "asap_annotations.json").read_text())
    df = pd.read_csv(ASAP / "metadata.csv")
    out, skipped = [], 0
    t0 = time.time()
    score_cache: dict[str, np.ndarray] = {}
    for _, row in df.iterrows():
        a = ann.get(row.midi_performance)
        if a is None or not a.get("score_and_performance_aligned"):
            skipped += 1
            continue
        try:
            if row.midi_score not in score_cache:
                score_cache[row.midi_score] = _notes(pretty_midi.PrettyMIDI(str(ASAP / row.midi_score)))
            score = score_cache[row.midi_score]
            pm = pretty_midi.PrettyMIDI(str(ASAP / row.midi_performance))
        except Exception as e:  # a broken file: leave it out
            print("skip", row.midi_performance, e, flush=True)
            skipped += 1
            continue
        perf = _notes(pm)
        sbs, sbp, unit, down, bpb = our_beats(a["midi_score_beats"], a["performance_beats"],
                                              a["midi_score_downbeats"], a["midi_score_time_signatures"])
        s_pos = beat_pos(score[:, 1], sbs)
        e_pos = beat_pos(score[:, 2], sbs)
        match = match_notes(score, perf, s_pos, sbp)
        out.append(dict(
            perf=row.midi_performance, composer=row.composer, title=row.title,
            piece="/".join(piece_key(row.composer, row.title)), split=split_of(row.composer, row.title),
            notes=perf[:, :4].astype(np.float32), match=match.astype(np.int32),
            score_pos=s_pos.astype(np.float64), score_end=e_pos.astype(np.float64),
            score_pitch=score[:, 0].astype(np.int16), score_hand=score[:, 4].astype(np.int8),
            beats_perf=sbp, unit=unit.astype(np.int16), down=down, bpb=bpb.astype(np.int16),
            pedal=np.array(_pedal(pm), dtype=np.float32).reshape(-1, 2)))
        if len(out) % 100 == 0:
            print(f"{len(out)} performances, {time.time() - t0:.0f} s", flush=True)
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "asap.pkl", "wb") as f:
        pickle.dump(out, f)
    by = {s: sum(1 for o in out if o["split"] == s) for s in ("train", "val", "test")}
    pieces = {s: len({o["piece"] for o in out if o["split"] == s}) for s in ("train", "val", "test")}
    matched = np.mean([np.mean(o["match"] >= 0) for o in out])
    print(f"{len(out)} performances ({skipped} skipped: not aligned to the score), "
          f"splits {by}, pieces {pieces}, perf notes matched to the score {matched:.1%}, "
          f"{time.time() - t0:.0f} s")


def load_asap() -> list[dict]:
    with open(DATA / "asap.pkl", "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------- render + transcribe a subset

REAL = DATA / "real"
RENDER_SPLITS = {"train": 40, "val": 12, "test": 12}
EXCERPT_S = 90.0


def render_pick(data: list[dict]) -> list[dict]:
    """One performance per piece, RENDER_SPLITS of them per split (fixed order)."""
    rng = random.Random(7)
    picked = []
    for split, n in RENDER_SPLITS.items():
        by_piece: dict[str, list[dict]] = {}
        for o in data:
            if o["split"] == split:
                by_piece.setdefault(o["piece"], []).append(o)
        pieces = sorted(by_piece)
        rng.shuffle(pieces)
        for p in pieces[:n]:
            picked.append(sorted(by_piece[p], key=lambda o: o["perf"])[0])
    return picked


def _excerpt_midi(o: dict, t0: float, t1: float):
    """The performance between t0 and t1 s as a PrettyMIDI starting at 0 (notes cut at
    t1, the pedal kept)."""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(str(ASAP / o["perf"]))
    out = pretty_midi.PrettyMIDI(resolution=pm.resolution)
    inst = pretty_midi.Instrument(program=0)
    for i in pm.instruments:
        for n in i.notes:
            if t0 <= n.start < t1:
                inst.notes.append(pretty_midi.Note(n.velocity, n.pitch, n.start - t0, min(n.end, t1 + 2) - t0))
        last = {}
        for c in i.control_changes:
            if c.time < t0:
                last[c.number] = c.value
            elif c.time < t1 + 2:
                inst.control_changes.append(pretty_midi.ControlChange(c.number, c.value, c.time - t0))
        for num, v in last.items():
            inst.control_changes.insert(0, pretty_midi.ControlChange(num, v, 0.0))
    out.instruments.append(inst)
    return out


def render(limit: int | None = None) -> None:
    """Synthesize EXCERPT_S of each picked performance (sheetswap.synth, MuseScore_General
    piano), transcribe it with the app's piano model and find its beats with Beat This!
    (as pipeline.find_beats does). -> data/rhythm/real/<n>.json"""
    from sheetswap import synth, transcribe
    from sheetswap.neural_beats import track_beats_neural
    data = load_asap()
    REAL.mkdir(parents=True, exist_ok=True)
    picked = render_pick(data)[:limit]
    t_all = time.time()
    for k, o in enumerate(picked):
        name = re.sub(r"[^0-9A-Za-z]+", "_", o["perf"])[:80]
        dest = REAL / f"{name}.json"
        if dest.exists():
            continue
        t0 = max(0.0, float(o["notes"][:, 1].min()) - 0.5)
        t1 = t0 + EXCERPT_S
        tic = time.time()
        pm = _excerpt_midi(o, t0, t1)
        wav = DATA / "tmp_render.wav"
        synth.render_midi(pm, wav)
        notes = transcribe.piano(wav)
        grid = track_beats_neural(wav, notes=notes)
        wav.unlink(missing_ok=True)
        dest.write_text(json.dumps(dict(
            perf=o["perf"], split=o["split"], piece=o["piece"], t0=t0, t1=t1,
            notes=[[n.pitch, round(n.start + t0, 4), round(n.end + t0, 4), n.velocity] for n in notes],
            grid=dict(beats=[round(b + t0, 4) for b in grid.beats], beats_per_bar=grid.beats_per_bar,
                      first_downbeat=grid.first_downbeat, beat_ticks=grid.beat_ticks))))
        print(f"[{k + 1}/{len(picked)}] {o['perf']}: {len(notes)} notes, "
              f"{time.time() - tic:.0f} s (total {time.time() - t_all:.0f} s)", flush=True)


def load_real(split: str | None = None) -> list[dict]:
    out = []
    for p in sorted(REAL.glob("*.json")):
        d = json.loads(p.read_text())
        if split is None or d["split"] == split:
            out.append(d)
    return out


# ---------------------------------------------------------------- what transcription and the grid get wrong

STATS = DATA / "error_stats.json"
ONSET_TOL = 0.08          # s: a transcribed note is a played note of the same pitch this close


def match_transcribed(trans: np.ndarray, perf: np.ndarray, tol: float = ONSET_TOL) -> np.ndarray:
    """Each transcribed note's performance note (index or -1): same pitch, onsets within
    tol, nearest first, one to one."""
    out = np.full(len(trans), -1)
    used = set()
    cand = []
    for p in np.unique(trans[:, 0]):
        ti = np.flatnonzero(trans[:, 0] == p)
        pi = np.flatnonzero(perf[:, 0] == p)
        if not len(pi):
            continue
        d = np.abs(trans[ti, 1][:, None] - perf[pi, 1][None, :])
        for a, b in zip(*np.nonzero(d <= tol)):
            cand.append((d[a, b], ti[a], pi[b]))
    for _, i, j in sorted(cand):
        if out[i] < 0 and j not in used:
            out[i] = j
            used.add(j)
    return out


def pedal_down(pedal: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Whether the sustain pedal is down at times t; and when it comes up (else t)."""
    down = np.zeros(len(t), dtype=bool)
    up = np.array(t, dtype=float)
    for a, b in pedal:
        inside = (t >= a) & (t < b)
        down |= inside
        up = np.where(inside, b, up)
    return down, up


def real_window(r: dict, o: dict):
    """A rendered excerpt's performance notes (in the window) and transcribed notes."""
    perf = o["notes"].astype(float)
    win = (perf[:, 1] >= r["t0"]) & (perf[:, 1] < r["t1"] - 1.0)
    trans = np.array(r["notes"], dtype=float).reshape(-1, 4)
    trans = trans[trans[:, 1] < r["t1"] - 1.0]
    return perf, win, trans


def grid_errors(r: dict, o: dict) -> dict:
    """Beat This! against the annotated beats (at our level) inside the excerpt: the level
    ratio, each tracked beat's offset from the nearest annotated beat (beats), slips."""
    ab = o["beats_perf"]
    bt = np.array(r["grid"]["beats"])
    inside = (bt >= max(ab[0], r["t0"] + 2)) & (bt <= min(ab[-1], r["t1"] - 2))
    pos = beat_pos(bt[inside], ab)
    if len(pos) < 8:
        return dict(ratio=None)
    ratio = float(np.median(np.diff(pos)))
    err = pos - np.round(pos)
    jumps = np.diff(np.round(pos) - np.arange(len(pos)))
    return dict(ratio=ratio, err=err.tolist(), slips=int(np.sum(jumps != 0)), n=len(pos))


def stats() -> None:
    """Error statistics of the rendered training excerpts -> data/rhythm/error_stats.json."""
    data = {o["perf"]: o for o in load_asap()}
    reals = load_real("train")
    rec = dict(n_files=len(reals), perf_notes=0, found=0, extra=0, extra_kind={}, vel_bins={},
               onset_dev=[], end_dev_up=[], end_dev_down=[], end_vs_pedal=[], vel_pairs=[],
               short_found=[0, 0], extra_offsets={})
    grid = dict(ratios=[], err_sd=[], err_mean=[], ac1=[], far=[], slips_per_100=[])
    rng = np.random.default_rng(0)
    for r in reals:
        o = data[r["perf"]]
        perf, win, trans = real_window(r, o)
        m = match_transcribed(trans, perf)
        found = np.zeros(len(perf), dtype=bool)
        found[m[m >= 0]] = True
        rec["perf_notes"] += int(win.sum())
        rec["found"] += int((found & win).sum())
        for lo, hi in ((0, 30), (30, 50), (50, 70), (70, 128)):
            sel = win & (perf[:, 3] >= lo) & (perf[:, 3] < hi)
            b = rec["vel_bins"].setdefault(f"{lo}-{hi}", [0, 0])
            b[0] += int(sel.sum())
            b[1] += int((found & sel).sum())
        short = win & (perf[:, 2] - perf[:, 1] < 0.08)
        rec["short_found"][0] += int(short.sum())
        rec["short_found"][1] += int((found & short).sum())
        ok = m >= 0
        pi = m[ok]
        rec["onset_dev"] += (trans[ok, 1] - perf[pi, 1]).tolist()
        down, up = pedal_down(o["pedal"], perf[pi, 2])
        dev = trans[ok, 2] - perf[pi, 2]
        rec["end_dev_up"] += dev[~down].tolist()
        rec["end_dev_down"] += dev[down].tolist()
        rec["end_vs_pedal"] += (trans[ok, 2][down] - up[down]).tolist()
        rec["vel_pairs"] += np.stack([perf[pi, 3], trans[ok, 3]], 1).tolist()
        # extras: what kind, and where (s) from the played note they come from
        for i in np.flatnonzero(~ok):
            p, s = trans[i, 0], trans[i, 1]
            sounding = (perf[:, 1] <= s + 0.03) & (perf[:, 2] + 0.3 >= s)
            near = np.abs(perf[:, 1] - s) <= 0.1
            if np.any(sounding & (perf[:, 0] == p)):
                kind, src = "repeat", np.flatnonzero(sounding & (perf[:, 0] == p))[-1]
            elif np.any(sounding & np.isin(p - perf[:, 0], (12, 19, 24, -12))):
                kind, src = "harmonic", np.flatnonzero(sounding & np.isin(p - perf[:, 0], (12, 19, 24, -12)))[-1]
            elif np.any(near & (np.abs(perf[:, 0] - p) <= 2)):
                kind, src = "neighbour", np.flatnonzero(near & (np.abs(perf[:, 0] - p) <= 2))[0]
            else:
                kind, src = "other", None
            rec["extra"] += 1
            rec["extra_kind"][kind] = rec["extra_kind"].get(kind, 0) + 1
            if src is not None:
                rec["extra_offsets"].setdefault(kind, []).append(
                    [float(s - perf[src, 1]), float(trans[i, 2] - trans[i, 1]), float(trans[i, 3]),
                     float(perf[src, 3]), int(p - perf[src, 0])])
        g = grid_errors(r, o)
        if g["ratio"] is None:
            continue
        grid["ratios"].append(g["ratio"])
        if 0.85 < g["ratio"] < 1.15:
            e = np.array(g["err"])
            core = e[np.abs(e) < 0.25]
            grid["err_sd"].append(float(np.std(core)))
            grid["err_mean"].append(float(np.mean(core)))
            grid["ac1"].append(float(np.corrcoef(core[:-1], core[1:])[0, 1]) if len(core) > 3 else 0.0)
            grid["far"].append(float(np.mean(np.abs(e) >= 0.25)))
            grid["slips_per_100"].append(100.0 * g["slips"] / g["n"])
    vp = np.array(rec["vel_pairs"])
    a, b = np.polyfit(vp[:, 0], vp[:, 1], 1)
    resid = float(np.std(vp[:, 1] - (a * vp[:, 0] + b)))

    def sample(xs, k=4000):
        xs = np.asarray(xs, dtype=float)
        return (rng.choice(xs, size=min(k, len(xs)), replace=False)).round(4).tolist() if len(xs) else []

    out = dict(
        n_files=rec["n_files"], perf_notes=rec["perf_notes"],
        recall=rec["found"] / max(rec["perf_notes"], 1),
        recall_by_velocity={k: v[1] / max(v[0], 1) for k, v in rec["vel_bins"].items()},
        recall_short=rec["short_found"][1] / max(rec["short_found"][0], 1),
        extra_per_note=rec["extra"] / max(rec["perf_notes"], 1),
        extra_kind={k: v / max(rec["perf_notes"], 1) for k, v in rec["extra_kind"].items()},
        extra_examples={k: v[:3000] for k, v in rec["extra_offsets"].items()},
        onset_dev=sample(rec["onset_dev"]), end_dev_up=sample(rec["end_dev_up"]),
        end_dev_down=sample(rec["end_dev_down"]), end_vs_pedal=sample(rec["end_vs_pedal"]),
        pedal_share=len(rec["end_dev_down"]) / max(len(rec["end_dev_down"]) + len(rec["end_dev_up"]), 1),
        velocity_fit=[float(a), float(b), resid],
        grid=dict(files=len(grid["ratios"]), ratios=[round(x, 3) for x in grid["ratios"]],
                  level_ok=float(np.mean([0.85 < x < 1.15 for x in grid["ratios"]])) if grid["ratios"] else 0,
                  err_sd=grid["err_sd"], err_mean=grid["err_mean"], ac1=grid["ac1"], far=grid["far"],
                  slips_per_100=grid["slips_per_100"]))
    STATS.write_text(json.dumps(out))
    pct = lambda xs: np.percentile(xs, [5, 25, 50, 75, 95]).round(3).tolist() if len(xs) else []  # noqa: E731
    print(f"{out['n_files']} excerpts, {out['perf_notes']} played notes")
    print(f"recall {out['recall']:.3f}, by velocity {({k: round(v, 3) for k, v in out['recall_by_velocity'].items()})}, "
          f"notes under 80 ms {out['recall_short']:.3f}")
    print(f"extra notes per played note {out['extra_per_note']:.3f}: "
          f"{({k: round(v, 4) for k, v in out['extra_kind'].items()})}")
    print(f"onset error (s) pct 5/25/50/75/95 {pct(rec['onset_dev'])}")
    print(f"end error, pedal up {pct(rec['end_dev_up'])}; pedal down {pct(rec['end_dev_down'])}, "
          f"vs pedal release {pct(rec['end_vs_pedal'])}; pedal down at {out['pedal_share']:.2f} of releases")
    print(f"velocity: transcribed = {a:.2f} played + {b:.1f} (sd {resid:.1f})")
    gg = out["grid"]
    print(f"grid: {gg['files']} excerpts, level right {gg['level_ok']:.2f} (ratios {sorted(gg['ratios'])}); "
          f"beat error sd (beats) {pct(gg['err_sd'])}, mean {pct(gg['err_mean'])}, lag-1 corr {pct(gg['ac1'])}, "
          f"beats off by >= 1/4 {pct(gg['far'])}, slips per 100 beats {pct(gg['slips_per_100'])}")


# ---------------------------------------------------------------- training lines

SEQS = DATA / "seqs"
IGNORE = -100
MIN_BEATS = 16            # shortest stretch of one beat kind made into a line
AUGMENT = 3               # simulated transcriptions per training performance (the third
                          # picks its line with top_voice instead of piano_melody)
# Rendered piano is easier than a real recording (no room, one piano, the synth's
# velocity curve): each simulated performance scales the measured error rates by a
# factor drawn from ERROR_SCALE, and velocities keep the played ones (the synth
# squeezes them: transcribed = 0.28 x played + 70) with VEL_NOISE of noise.
ERROR_SCALE = (0.7, 3.0)
VEL_NOISE = 8.0
# Beat This! wrote the beat at another level in half the rendered excerpts (twice or
# half as fast, or 3:4); a quarter-beat line gets a grid twice as fast / half as fast
# this often (labels follow the grid's beats).
LEVEL_ERRORS = (0.15, 0.10)
# ... and the beat half a beat off (the tracker on the off-beats) this often, so that a
# line played off the beat throughout is read off the beat, as the grid has it.
PHASE_ERRORS = 0.1


def _sample(xs, rng, n):
    xs = np.asarray(xs, dtype=float)
    return xs[rng.integers(0, len(xs), n)] if len(xs) else np.zeros(n)


def sim_transcription(o: dict, st: dict, rng) -> tuple[list[Note], list[int]]:
    """The performance as the piano model would hear it, from the measured errors:
    soft notes missed, onsets and ends moved by measured amounts (ends after the key
    comes up, whether or not the pedal is down), velocities with noise, and extra notes
    of the measured kinds (repeats inside held notes, overtones, neighbours, others)."""
    perf = o["notes"].astype(float)
    n = len(perf)
    scale = rng.uniform(*ERROR_SCALE)
    miss = np.zeros(n)
    for k, rate in st["recall_by_velocity"].items():
        lo, hi = (int(v) for v in k.split("-"))
        sel = (perf[:, 3] >= lo) & (perf[:, 3] < hi)
        miss[sel] = min(0.9, (1 - rate) * scale)
    keep = rng.random(n) >= miss
    med = float(np.median(st["onset_dev"]))
    jit = med + (_sample(st["onset_dev"], rng, n) - med) * rng.uniform(1.0, 1.0 + scale) \
        + rng.normal(0, 0.005 * scale, n)
    down, _ = pedal_down(o["pedal"], perf[:, 2])
    end_dev = np.where(down, _sample(st["end_dev_down"], rng, n), _sample(st["end_dev_up"], rng, n))
    gain, offset = rng.uniform(0.8, 1.2), rng.normal(0, 6)
    vel = np.clip(np.round(perf[:, 3] * gain + offset + rng.normal(0, VEL_NOISE, n)), 1, 127)
    notes, ids = [], []
    for i in np.flatnonzero(keep):
        s = perf[i, 1] + jit[i]
        e = max(perf[i, 2] + end_dev[i], s + 0.03)
        notes.append(Note(int(perf[i, 0]), float(s), float(e), int(vel[i])))
        ids.append(int(i))
    for kind, rate in st["extra_kind"].items():
        k = rng.poisson(rate * scale * n)
        ex = st["extra_examples"].get(kind) or []
        for _ in range(k):
            src = int(rng.integers(0, n))
            if ex:
                off, dur, v, _, dp = ex[int(rng.integers(0, len(ex)))]
            else:
                off, dur, v, dp = rng.uniform(-0.1, 0.4), rng.uniform(0.05, 0.4), rng.uniform(20, 60), \
                    int(rng.integers(-7, 8))
            p = int(perf[src, 0] + dp)
            if 21 <= p <= 108:
                s = float(perf[src, 1] + off)
                notes.append(Note(p, s, s + max(float(dur), 0.03), int(np.clip(v, 1, 127))))
                ids.append(-1)
    order = sorted(range(len(notes)), key=lambda j: (notes[j].start, notes[j].pitch))
    return [notes[j] for j in order], [ids[j] for j in order]


def sim_beats(beats: np.ndarray, st: dict, rng) -> np.ndarray:
    """The annotated beats moved the way Beat This! misses them: an AR(1) error in
    beats (its spread and lag-1 correlation drawn from one measured excerpt) times the
    local beat length."""
    g = st["grid"]
    k = int(rng.integers(0, len(g["err_sd"])))
    sd, phi, mean = g["err_sd"][k], float(np.clip(g["ac1"][k], 0, 0.95)), g["err_mean"][k]
    e = np.zeros(len(beats))
    e[0] = rng.normal(0, sd)
    for i in range(1, len(beats)):
        e[i] = phi * e[i - 1] + rng.normal(0, sd * math.sqrt(1 - phi ** 2))
    ibi = np.diff(beats)
    ibi = np.concatenate([ibi, ibi[-1:]]) if len(ibi) else np.full(len(beats), 0.5)
    out = beats + (e + mean) * ibi
    return np.maximum.accumulate(out + np.arange(len(out)) * 1e-4)


def segments(o: dict) -> list[tuple[int, int, int]]:
    """Runs of annotated beats of one kind: (first, last + 1, beat_ticks)."""
    u = o["unit"]
    out, a = [], 0
    for i in range(1, len(u) + 1):
        if i == len(u) or u[i] != u[a]:
            if i - a >= MIN_BEATS:
                out.append((a, i, int(u[a])))
            a = i
    return out


def tune_line(notes: list[Note], picker: str = "piano") -> list[Note]:
    from sheetswap.melody import fill_tune, piano_melody, top_voice
    if picker == "top":
        return top_voice(notes)
    return fill_tune(piano_melody(notes), notes)


def label_line(line: list[Note], src_ids: dict, o: dict, grid: BeatGrid, frame) -> dict | None:
    """Features and labels of one tune line on one grid. frame(k) = (where grid beat k,
    counted from the grid's first beat, should sit in annotated beats; annotated beats per
    grid beat), or None where the grid can't be trusted there (labels left out). A score
    place sp is then grid beat k + (sp - where) / per."""
    unit = grid.beat_ticks
    merged, grace = rhythm._merged(line, grid)
    if len(merged) < 8:
        return None
    rule = rhythm.rule_reading(merged, grace, grid)
    feats = rhythm.learned_features(merged, grace, grid, rule)
    n = len(merged)
    place = np.full(n, IGNORE, dtype=np.int16)
    rest = np.full(n, IGNORE, dtype=np.int16)
    extra = np.full(n, -1, dtype=np.int8)
    written = np.full(n, np.nan)
    written_end = np.full(n, np.nan)
    beats = np.array([math.floor(s / unit) for s, _, _ in merged])
    pmask, rmask = rhythm.place_mask(unit), rhythm.rest_mask(unit)
    for i, (s, _, m) in enumerate(merged):
        pid = src_ids.get((m.pitch, round(m.start, 4)), -1)
        j = o["match"][pid] if pid >= 0 else -1
        if j < 0:
            extra[i] = 1
            continue
        fr = frame(beats[i] + grid.first_downbeat)
        if fr is None:
            continue
        where, per = fr
        sp, ep = o["score_pos"][j], o["score_end"][j]
        w = (beats[i] + (sp - where) / per) * unit
        tw = (sp - math.floor(sp)) * 12
        if ep - sp < 1 / 12 and abs(tw - round(tw)) > 0.25:
            extra[i] = 1                       # a grace note in the score
            continue
        c = rhythm.place_class(w, int(beats[i]), unit)
        if c is None or not pmask[c]:
            continue
        wt = rhythm.place_ticks(c, int(beats[i]), unit)
        if i and written[i - 1] == wt:
            extra[i] = 1                       # the same written note again
            continue
        place[i], extra[i], written[i] = c, 0, wt
        # the written end in whole ticks (the score MIDI's times are floats)
        written_end[i] = round((beats[i] + (ep - where) / per) * unit)
    dur = np.full(n, np.nan)
    for i in range(n - 1):
        if place[i] != IGNORE and place[i + 1] != IGNORE:
            end = min(written_end[i], written[i + 1])
            dur[i] = end - written[i]
            r = rhythm.rest_class((written[i + 1] - end) * 12 / unit)
            if r is not None and rmask[r]:
                rest[i] = r
    if (place != IGNORE).sum() < 4:
        return None
    return dict(feats=feats, place=place, rest=rest, extra=extra, written=written, dur=dur,
                rule=np.array([np.nan if t is None else t for t in rule]), beats=beats, unit=unit,
                line=np.array([[m.pitch, m.start, m.end, m.velocity] for m in line]),
                grid=dict(beats=list(grid.beats), beats_per_bar=grid.beats_per_bar,
                          first_downbeat=grid.first_downbeat, beat_ticks=unit),
                piece=o["piece"])


def sim_examples(o: dict, st: dict, seed: int, picker: str = "piano") -> list[dict]:
    """Lines of one performance: simulated transcription, beat grid and bar lines."""
    rng = np.random.default_rng(seed)
    notes, ids = sim_transcription(o, st, rng)
    src = {(m.pitch, round(m.start, 4)): i for m, i in zip(notes, ids)}
    line = tune_line(notes, picker)
    out = []
    for a, b, unit in segments(o):
        ann = o["beats_perf"][a:b]
        downs = np.flatnonzero(o["down"][a:b])
        first = int(downs[0]) if len(downs) else 0
        bpb = int(o["bpb"][a])
        per, phase, half = 1.0, 0, 0.0
        level = rng.random()
        if unit == rhythm.QUARTER and level < LEVEL_ERRORS[0]:
            # the tracker on the 8ths: a beat between each two
            ann = np.sort(np.concatenate([ann, (ann[:-1] + ann[1:]) / 2]))
            per, first, bpb = 0.5, 2 * first, 2 * bpb
        elif unit == rhythm.QUARTER and level < sum(LEVEL_ERRORS) and bpb % 2 == 0:
            # the tracker on the half notes: every other beat
            phase = first % 2
            ann = ann[phase::2]
            per, first, bpb = 2.0, (first - phase) // 2, bpb // 2
        elif unit == rhythm.QUARTER and level < sum(LEVEL_ERRORS) + PHASE_ERRORS and len(ann) > 2:
            # every beat half way to the next one
            half = 0.5
            ann = (ann[:-1] + ann[1:]) / 2
        beats = sim_beats(ann, st, rng)
        if bpb > 1 and rng.random() < 0.2:      # bar lines in the wrong place
            first = (first + int(rng.integers(1, bpb))) % bpb
        if rng.random() < 0.1:                  # the wrong meter
            bpb = int(rng.choice([2, 3, 4]))
        grid = BeatGrid(list(beats), max(bpb, 1), first, unit, "neural")
        lo, hi = beats[0], beats[-1]
        seg = [m for m in line if lo - 0.05 <= m.start < hi]
        ex = label_line(seg, src, o, grid, lambda k, a=a + phase + half, per=per: (a + k * per, per))
        if ex:
            out.append(ex)
    return out


def real_examples(r: dict, o: dict) -> list[dict]:
    """The line of one rendered excerpt: its transcription and Beat This! grid. Grid
    beats are tied to the annotated beat they sit on; labels only where that is clear
    (within a quarter beat, the same beat offset as both neighbours)."""
    perf, _, trans = real_window(r, o)
    m = match_transcribed(trans, perf)
    notes = [Note(int(p), float(s), float(e), int(v)) for p, s, e, v in trans]
    src = {(nn.pitch, round(nn.start, 4)): int(i) for nn, i in zip(notes, m)}
    ab = o["beats_perf"]
    bt = np.array(r["grid"]["beats"])
    pos = beat_pos(bt, ab)
    mid = (bt > r["t0"] + 2) & (bt < r["t1"] - 2)
    ratio = np.median(np.diff(pos[mid]))
    inside = np.clip(np.round(pos).astype(int), 0, len(ab) - 1)
    unit = int(np.bincount(o["unit"][inside[mid]]).argmax())
    per = next((p for p in (1.0, 0.5, 2.0) if abs(ratio / p - 1) < 0.15), None)
    if per is None or (per != 1.0 and unit != rhythm.QUARTER):
        return []                              # Beat This! on no level we can label
    if per == 2.0:                             # every other beat: which ones
        phase = int(np.bincount(np.mod(np.round(pos[mid]).astype(int), 2)).argmax())
        ideal = 2 * np.round((pos - phase) / 2) + phase
    else:
        ideal = np.round(pos / per) * per
    ok = (np.abs(pos - ideal) < 0.25 * per) & (pos >= 0) & (pos <= len(ab) - 1)
    steps = np.diff(ideal)
    ok[1:-1] &= (np.abs(steps[:-1] - per) < 1e-6) & (np.abs(steps[1:] - per) < 1e-6)
    ok &= o["unit"][inside] == unit

    def frame(k):
        k = int(k)
        return (float(ideal[k]), per) if 0 <= k < len(ideal) and ok[k] else None

    grid = BeatGrid(list(bt), r["grid"]["beats_per_bar"], r["grid"]["first_downbeat"], unit, "neural")
    line = tune_line(notes)
    ex = label_line(line, src, o, grid, frame)
    return [ex] if ex else []


def _seed(name: str, k: int) -> int:
    return int(hashlib.sha1(f"{name}#{k}".encode()).hexdigest()[:8], 16)


def _build_one(args):
    o, st, seeds = args
    out = []
    for k, seed in enumerate(seeds):
        out += sim_examples(o, st, seed, "top" if k == 2 else "piano")
    return out


def build(workers: int = 3) -> None:
    """Simulated lines for every performance (AUGMENT per training performance, one per
    held-out one) and the rendered excerpts' real lines -> data/rhythm/seqs/*.pkl."""
    from concurrent.futures import ProcessPoolExecutor
    st = json.loads(STATS.read_text())
    data = load_asap()
    SEQS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for split in ("train", "val", "test"):
        jobs = [(o, st, [_seed(o["perf"], k) for k in range(AUGMENT if split == "train" else 1)])
                for o in data if o["split"] == split]
        seqs = []
        with ProcessPoolExecutor(workers) as ex:
            for k, res in enumerate(ex.map(_build_one, jobs, chunksize=4)):
                seqs += res
                if (k + 1) % 100 == 0:
                    print(f"{split}: {k + 1}/{len(jobs)} performances, {time.time() - t0:.0f} s", flush=True)
        with open(SEQS / f"sim_{split}.pkl", "wb") as f:
            pickle.dump(seqs, f)
        print(f"{split}: {len(seqs)} simulated lines, {sum(len(s['place']) for s in seqs)} notes, "
              f"{sum(int((s['place'] != IGNORE).sum()) for s in seqs)} labelled", flush=True)
    by_perf = {o["perf"]: o for o in data}
    for split in ("train", "val", "test"):
        seqs = []
        for r in load_real(split):
            seqs += real_examples(r, by_perf[r["perf"]])
        with open(SEQS / f"real_{split}.pkl", "wb") as f:
            pickle.dump(seqs, f)
        print(f"real {split}: {len(seqs)} lines (of {len(load_real(split))} excerpts), "
              f"{sum(int((s['place'] != IGNORE).sum()) for s in seqs)} labelled notes", flush=True)
    print(f"built in {time.time() - t0:.0f} s")


def load_seqs(name: str) -> list[dict]:
    with open(SEQS / f"{name}.pkl", "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------- training

WINDOW = 256              # notes per training window
EXTRA_WEIGHT = 3.0        # weight of log p(extra) when leaving a note out (best on val: 3 > 1)
REAL_REPEAT = 4           # the rendered excerpts' real lines count this many times


def _windows(seqs: list[dict], rng, win: int = WINDOW) -> list[tuple[int, int, int]]:
    """(sequence, start, end) crops covering every sequence, at random offsets."""
    out = []
    for k, s in enumerate(seqs):
        n = len(s["place"])
        if n <= win:
            out.append((k, 0, n))
            continue
        off = int(rng.integers(0, win))
        starts = list(range(-off, n, win))
        for a in starts:
            a = max(0, a)
            out.append((k, a, min(n, a + win)))
    return out


ZEROED: tuple[int, ...] = ()   # input columns the network doesn't get (set by train())
# Absolute pitch (rhythm.learned_features column 21) is left out: with it the network
# learned pieces by heart (ASAP has ~5 performances of each piece).
NO_PITCH = (21,)


def _batch(seqs, wins, torch):
    L = max(b - a for _, a, b in wins)
    x = np.zeros((len(wins), L, rhythm.N_FEATURES), dtype=np.float32)
    yp = np.full((len(wins), L), IGNORE, dtype=np.int64)
    yr = np.full((len(wins), L), IGNORE, dtype=np.int64)
    ye = np.full((len(wins), L), -1.0, dtype=np.float32)
    mp = np.zeros((len(wins), L, rhythm.N_PLACES), dtype=bool)
    mr = np.zeros((len(wins), L, rhythm.N_RESTS), dtype=bool)
    for j, (k, a, b) in enumerate(wins):
        s = seqs[k]
        x[j, :b - a] = s["feats"][a:b]
        yp[j, :b - a] = s["place"][a:b]
        yr[j, :b - a] = s["rest"][a:b]
        ye[j, :b - a] = s["extra"][a:b]
        mp[j, :b - a] = rhythm.place_mask(s["unit"])
        mr[j, :b - a] = rhythm.rest_mask(s["unit"])
    x[..., list(ZEROED)] = 0.0
    t = torch.from_numpy
    return t(x), t(yp), t(yr), t(ye), t(mp), t(mr)


def _loss(net, batch, torch):
    x, yp, yr, ye, mp, mr = batch
    lp, lr, le = net(x)
    lp = lp.masked_fill(~mp, -1e4)
    lr = lr.masked_fill(~mr, -1e4)
    ce = torch.nn.functional.cross_entropy
    loss_p = ce(lp.reshape(-1, lp.shape[-1]), yp.reshape(-1), ignore_index=IGNORE)
    loss_r = ce(lr.reshape(-1, lr.shape[-1]), yr.reshape(-1), ignore_index=IGNORE) \
        if (yr != IGNORE).any() else lp.sum() * 0
    sel = ye >= 0
    loss_e = torch.nn.functional.binary_cross_entropy_with_logits(le[sel], ye[sel]) if sel.any() else lp.sum() * 0
    acc_n = ((lp.argmax(-1) == yp) & (yp != IGNORE)).sum().item()
    return loss_p + 0.5 * loss_r + 0.5 * loss_e, acc_n, int((yp != IGNORE).sum().item())


def _val(net, seqs, torch, bs: int = 16) -> tuple[float, float]:
    """(mean loss, place accuracy of the network's single best guess) over whole lines."""
    net.eval()
    tot, right, n, k = 0.0, 0, 0, 0
    with torch.no_grad():
        wins = [(k, 0, len(s["place"])) for k, s in enumerate(seqs)]
        wins.sort(key=lambda w: w[2])
        for i in range(0, len(wins), bs):
            loss, r, c = _loss(net, _batch(seqs, wins[i:i + bs], torch), torch)
            tot += loss.item()
            k += 1
            right += r
            n += c
    net.train()
    return tot / max(k, 1), right / max(n, 1)


def train(hours: float = 1.5, epochs: int = 8, hidden: int = 96, layers: int = 2, dropout: float = 0.3,
          zeroed: tuple[int, ...] = NO_PITCH,
          lr: float = 2e-3, batch: int = 24, threads: int = 4, out: Path = MODEL_DIR) -> None:
    """Train on the simulated training lines plus the real ones (REAL_REPEAT times);
    keep the weights with the best place accuracy on the validation pieces (real and
    simulated lines equally) -> models/rhythm/weights.pt + config.json."""
    import torch
    torch.manual_seed(0)
    torch.set_num_threads(threads)
    rng = np.random.default_rng(0)
    real_tr = load_seqs("real_train")
    tr = load_seqs("sim_train") + real_tr * REAL_REPEAT
    va_sim, va_real = load_seqs("sim_val"), load_seqs("real_val")
    n_lab = sum(int((s["place"] != IGNORE).sum()) for s in tr)
    print(f"train: {len(tr)} lines ({len(real_tr)} real x{REAL_REPEAT}), {n_lab} labelled notes; "
          f"val: {len(va_sim)} simulated, {len(va_real)} real", flush=True)
    global ZEROED
    ZEROED = tuple(zeroed)
    net = rhythm.build_net(rhythm.N_FEATURES, hidden, layers, dropout)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    steps_per_epoch = math.ceil(len(_windows(tr, rng)) / batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * steps_per_epoch + 10,
                                                pct_start=0.1)
    best, t0, log = -1.0, time.time(), []
    out.mkdir(parents=True, exist_ok=True)
    for ep in range(epochs):
        wins = _windows(tr, rng)
        order = rng.permutation(len(wins))
        tot, right, cnt = 0.0, 0, 0
        for i in range(0, len(order), batch):
            b = _batch(tr, [wins[j] for j in order[i:i + batch]], torch)
            loss, r, c = _loss(net, b, torch)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            opt.step()
            sched.step()
            tot += loss.item()
            right += r
            cnt += c
        vs_loss, vs_acc = _val(net, va_sim, torch)
        vr_loss, vr_acc = _val(net, va_real, torch)
        score = (vs_acc + vr_acc) / 2
        log.append(dict(epoch=ep + 1, train_acc=right / max(cnt, 1), val_sim_acc=vs_acc, val_real_acc=vr_acc,
                        val_sim_loss=vs_loss, val_real_loss=vr_loss, minutes=(time.time() - t0) / 60))
        print(f"epoch {ep + 1}: train loss {tot / max(1, len(order) / batch):.3f} acc {right / max(cnt, 1):.3f} | "
              f"val sim {vs_acc:.3f} ({vs_loss:.3f}) real {vr_acc:.3f} ({vr_loss:.3f}) | "
              f"{(time.time() - t0) / 60:.1f} min", flush=True)
        if score > best:
            best = score
            torch.save(net.state_dict(), out / "weights.pt")
            (out / "config.json").write_text(json.dumps(dict(
                features=rhythm.N_FEATURES, hidden=hidden, layers=layers, epoch=ep + 1,
                zeroed=list(ZEROED), dropout=dropout,
                val_sim_acc=vs_acc, val_real_acc=vr_acc, extra_weight=EXTRA_WEIGHT,
                trained="bench/train_rhythm.py train", data="ASAP (CC BY-NC-SA 4.0)"), indent=1))
        if time.time() - t0 > hours * 3600:
            print("time limit reached", flush=True)
            break
    (DATA / "train_log.json").write_text(json.dumps(log, indent=1))
    print(f"best val (mean of simulated and real place accuracy) {best:.3f}; "
          f"{(time.time() - t0) / 60:.1f} min", flush=True)


# ---------------------------------------------------------------- evaluation

def _grid(ex: dict) -> BeatGrid:
    g = ex["grid"]
    return BeatGrid(list(g["beats"]), g["beats_per_bar"], g["first_downbeat"], g["beat_ticks"], "neural")


def _line(ex: dict) -> list[Note]:
    return [Note(int(p), float(s), float(e), int(v)) for p, s, e, v in ex["line"]]


def score_line(ex: dict, model, extra_weight: float = 1.0, rule_bonus: float = 0.0) -> dict:
    """Counts for one line: labelled notes; onsets exactly right / within a 16th for the
    rules and the model; durations right (where the label has one)."""
    grid, line = _grid(ex), _line(ex)
    unit = ex["unit"]
    lab = ex["place"] != IGNORE
    w = ex["written"]
    rule = ex["rule"]
    net, cfg = model
    lp, lr, lx, lk = rhythm.learned_outputs(net, ex["feats"], cfg.get("zeroed", ()))
    lp = rhythm.lean_to_rules(lp, [None if t != t else int(t) for t in rule], ex["beats"], unit, rule_bonus)
    learned = np.array([np.nan if t is None else t for t in
                        rhythm.decode_places(lp, lx, lk, ex["beats"], unit, False, extra_weight)])
    rq = {q.start: q.dur for q in rhythm.quantize_line(line, grid)}
    lq = {q.start: q.dur for q in rhythm.quantize_line_learned(
        line, grid, model=(net, dict(cfg, extra_weight=extra_weight, rule_bonus=rule_bonus)))}
    has_dur = lab & np.isfinite(ex["dur"])
    sixteenth = unit / 4 if unit == rhythm.QUARTER else unit / 6 if unit == rhythm.DOTTED_BEAT else unit / 2

    def durs(ticks, q):
        ok = 0
        for i in np.flatnonzero(has_dur):
            t = ticks[i]
            if np.isfinite(t) and t == w[i] and q.get(int(t)) == ex["dur"][i]:
                ok += 1
        return ok

    return dict(n=int(lab.sum()), n_dur=int(has_dur.sum()),
                rule_exact=int(np.sum(rule[lab] == w[lab])), learned_exact=int(np.sum(learned[lab] == w[lab])),
                rule_16=int(np.sum(np.abs(rule[lab] - w[lab]) <= sixteenth)),
                learned_16=int(np.sum(np.abs(learned[lab] - w[lab]) <= sixteenth)),
                rule_dropped=int(np.sum(~np.isfinite(rule[lab]))),
                learned_dropped=int(np.sum(~np.isfinite(learned[lab]))),
                extra_n=int(np.sum(ex["extra"] == 1)),
                rule_extra_kept=int(np.sum(np.isfinite(rule[ex["extra"] == 1]))),
                learned_extra_kept=int(np.sum(np.isfinite(learned[ex["extra"] == 1]))),
                rule_dur=durs(rule, rq), learned_dur=durs(learned, lq))


def evaluate(splits=("val", "test"), extra_weights=(1.0,), model_dir: Path = MODEL_DIR,
             rule_bonuses=(0.0,)) -> dict:
    model = rhythm.load_learned(model_dir)
    if model is None:
        raise SystemExit("no model in models/rhythm/ (run the train step)")
    res = {}
    for split in splits:
        for kind in ("real", "sim"):
            seqs = load_seqs(f"{kind}_{split}")
            if not seqs:
                continue
            for xw, rb in [(x, b) for x in extra_weights for b in rule_bonuses]:
                tot: dict = {}
                per_piece: dict = {}
                for ex in seqs:
                    c = score_line(ex, model, xw, rb)
                    for k, v in c.items():
                        tot[k] = tot.get(k, 0) + v
                        pp = per_piece.setdefault(ex["piece"], {})
                        pp[k] = pp.get(k, 0) + v
                n, nd = max(tot.get("n", 0), 1), max(tot.get("n_dur", 0), 1)
                row = dict(lines=len(seqs), notes=tot.get("n", 0),
                           rule_exact=tot["rule_exact"] / n, learned_exact=tot["learned_exact"] / n,
                           rule_16th=tot["rule_16"] / n, learned_16th=tot["learned_16"] / n,
                           rule_dropped=tot["rule_dropped"] / n, learned_dropped=tot["learned_dropped"] / n,
                           rule_dur=tot["rule_dur"] / nd, learned_dur=tot["learned_dur"] / nd,
                           extras=tot["extra_n"], rule_extras_kept=tot["rule_extra_kept"],
                           learned_extras_kept=tot["learned_extra_kept"],
                           pieces_better=sum(1 for p in per_piece.values()
                                             if p["learned_exact"] > p["rule_exact"] + 0.01 * p["n"]),
                           pieces_worse=sum(1 for p in per_piece.values()
                                            if p["learned_exact"] < p["rule_exact"] - 0.01 * p["n"]),
                           pieces=len(per_piece))
                res[f"{kind}_{split}_x{xw}_b{rb}"] = row
                print(f"{kind} {split} (extra weight {xw}, rule bonus {rb}): {row['notes']} notes in "
                      f"{row['lines']} lines, "
                      f"{row['pieces']} pieces | onset exact rules {row['rule_exact']:.3f} -> learned "
                      f"{row['learned_exact']:.3f} | within a 16th {row['rule_16th']:.3f} -> "
                      f"{row['learned_16th']:.3f} | same length {row['rule_dur']:.3f} -> {row['learned_dur']:.3f} | "
                      f"left out {row['rule_dropped']:.3f} / {row['learned_dropped']:.3f} | extras kept "
                      f"{row['rule_extras_kept']}/{row['extras']} -> {row['learned_extras_kept']}/{row['extras']} | "
                      f"pieces better {row['pieces_better']}, worse {row['pieces_worse']}", flush=True)
    (DATA / "eval.json").write_text(json.dumps(res, indent=1))
    return res


# ---------------------------------------------------------------- the judge, both readers

JUDGE = DATA / "judge"
JUDGE_COLS = [("found_pct", "found"), ("onset_exact_pct", "onset exact"), ("onset_16th_pct", "±16th"),
              ("dur_exact_pct", "same length"), (("notation", "ties_per_bar"), "ties/bar"),
              (("listen", "vs_reference", "dev_median_ms"), "listen dev ms")]


def _get(d: dict, key):
    for k in key if isinstance(key, tuple) else (key,):
        if not isinstance(d, dict) or d.get(k) is None:
            return None
        d = d[k]
    return d


def judge(readers=("rules", "learned"), run: bool = True, listen: bool = True) -> None:
    """bench/ref_score.py --no-analyze with each reader (SHEETSWAP_RHYTHM), its result.json
    files kept in data/rhythm/judge/<reader>/, and a table per piece. The last reader's run
    is what bench/ref_results.md shows afterwards."""
    import os
    import shutil
    import subprocess
    from sheetswap.config import OUT_DIR
    for reader in readers:
        dest = JUDGE / reader
        if run:
            cmd = [sys.executable, str(ROOT / "bench" / "ref_score.py"), "--no-analyze"]
            if not listen:
                cmd.append("--no-listen")
            t0 = time.time()
            subprocess.run(cmd, check=True, env=dict(os.environ, SHEETSWAP_RHYTHM=reader), cwd=ROOT)
            print(f"{reader}: judged in {time.time() - t0:.0f} s", flush=True)
            dest.mkdir(parents=True, exist_ok=True)
            for p in (OUT_DIR / "ref").glob("*/result.json"):
                shutil.copy(p, dest / f"{p.parent.name}.json")
    res = {r: {p.stem: json.loads(p.read_text()) for p in sorted((JUDGE / r).glob("*.json"))} for r in readers}
    pieces = sorted(set.intersection(*(set(v) for v in res.values())))
    head = "| piece | instr. | " + " | ".join(f"{name} ({'/'.join(readers)})" for _, name in JUDGE_COLS) + " |"
    lines = [head, "|" + "---|" * (2 + len(JUDGE_COLS))]
    means = {r: {k: [] for k, _ in JUDGE_COLS} for r in readers}
    for p in pieces:
        cells = []
        for key, _ in JUDGE_COLS:
            vals = [_get(res[r][p], key) for r in readers]
            for r, v in zip(readers, vals):
                if isinstance(v, (int, float)):
                    means[r][key].append(v)
            cells.append(" / ".join("–" if v is None else f"{v:g}" for v in vals))
        lines.append(f"| {p} | {res[readers[0]][p].get('instrument', '')} | " + " | ".join(cells) + " |")
    lines.append("| **mean** | | " + " | ".join(
        " / ".join(f"{np.mean(means[r][k]):.1f}" if means[r][k] else "–" for r in readers)
        for k, _ in JUDGE_COLS) + " |")
    table = "\n".join(lines)
    (JUDGE / "table.md").write_text(table + "\n")
    print(table)


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["prepare", "render", "stats", "build", "train", "evaluate", "judge"])
    ap.add_argument("--table-only", action="store_true", help="judge: tabulate the saved runs only")
    ap.add_argument("--no-listen", action="store_true", help="judge: skip the listening checks")
    ap.add_argument("--limit", type=int, default=None, help="render: only this many excerpts")
    ap.add_argument("--workers", type=int, default=3, help="build: processes")
    ap.add_argument("--hours", type=float, default=1.5, help="train: stop after this long")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--hidden", type=int, default=96)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--dropout", type=float, default=0.3)
    ap.add_argument("--zero", type=int, action="append", default=None,
                    help="train: an input column (rhythm.learned_features) the network doesn't get")
    ap.add_argument("--out", default=str(MODEL_DIR), help="train / evaluate: the model's folder")
    ap.add_argument("--rule-bonus", type=float, action="append", default=None,
                    help="evaluate: log-odds added to the rules' place before decoding")
    ap.add_argument("--extra-weight", type=float, action="append", default=None,
                    help="evaluate: weight of the 'not a score note' output when leaving notes out")
    a = ap.parse_args()
    if a.step == "prepare":
        prepare()
    elif a.step == "render":
        render(a.limit)
    elif a.step == "stats":
        stats()
    elif a.step == "build":
        build(a.workers)
    elif a.step == "train":
        train(hours=a.hours, epochs=a.epochs, hidden=a.hidden, layers=a.layers, threads=a.threads,
              dropout=a.dropout, zeroed=tuple(a.zero or NO_PITCH), out=Path(a.out))
    elif a.step == "evaluate":
        evaluate(extra_weights=tuple(a.extra_weight or (EXTRA_WEIGHT,)), model_dir=Path(a.out),
                 rule_bonuses=tuple(a.rule_bonus or (0.0,)))
    elif a.step == "judge":
        judge(run=not a.table_only, listen=not a.no_listen)


if __name__ == "__main__":
    main()
