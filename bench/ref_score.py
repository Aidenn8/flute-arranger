"""Score the flute part against REAL sheet music, and listen to it.

    heat run .venv/bin/python bench/ref_score.py                  # everything (first analysis is slow)
    .venv/bin/python bench/ref_score.py --no-analyze              # reuse cached analyses (fast)
    .venv/bin/python bench/ref_score.py --piece fur_elise         # one piece (repeatable)
    .venv/bin/python bench/ref_score.py --no-listen               # skip the audio checks
    heat run .venv/bin/python bench/ref_score.py --analyze-only   # only build the caches
    heat run .venv/bin/python bench/ref_score.py --reanalyze      # after changes to the analysis

The reference set is samples/ref/<piece>/ (bench/make_refs.py builds it; sources in
bench/REFERENCES.md): a real recording and ref.json, the melody a flutist would play,
taken from the published score (quarter-note beats from the first downbeat).

For each piece the recording is analysed like an upload (cached in out/ref/<piece>/),
arranged with the default options (flute alone) and the flute part compared with
ref.json:

1. Alignment. The two note lists are aligned (dynamic programming, in order) on pitch
   class, so any octave and one key transposition for the whole piece are allowed.
   A second pass only lets notes pair up near where the first pass says they should be.
2. Tempo and bars. The tempo ratio is how many of our quarter notes one written
   quarter takes (2.0 = our beat is twice as fast, every note value halved), from
   the onset gaps of matched notes, rounded to a plain ratio. Positions are then put
   on the reference's scale and our bar lines are lined up with the real ones in
   whole beats of ours (locally, so a skipped beat only moves the bar lines from
   there on). What remains is how far each note sits from its written place.
   "raw" columns skip all of this: our quarter = their quarter, bar 1 = bar 1, so a
   wrong meter or downbeat shows up there.
3. Notes found, wrong notes, extra notes; onset in the bar exactly right / within a
   16th; the same note length; the same gap to the next note; meter, downbeat and
   tempo; ties per bar and notes shorter than an 8th (on our page vs. in the score);
   "timing feels off" = mean |onset error| in quarter beats after the corrections.
4. Listening. Our part (flute, at the printed tempo) and the reference melody (flute,
   at the recording's average tempo) are synthesized (sheetswap.synth, GM flute) and
   aligned by DTW on chroma + onset strength (smoothed). Each of our notes is paired
   with the reference note of the same pitch class nearest to where the alignment puts
   it; its timing deviation is how far that pair sits off a line through its neighbours
   (ms and beats, median and 90th percentile). Pitch agreement: our pitch class is the
   loudest there. The same against the original recording, using the notes heard in
   it. A floor check (the reference against itself 15% slower) should give 0. Finally
   our rendered flute is transcribed back (Basic Pitch) as a check that the audio is
   our score.

Writes out/ref/<piece>/ (score, page-1.png, ours.wav, ref.wav, result.json) and
bench/ref_results.md.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from sheetswap import pipeline  # noqa: E402
from sheetswap.config import FLUTE_PROGRAM, OUT_DIR, TICKS_PER_BEAT as T  # noqa: E402
from sheetswap.model import ArrangeOptions  # noqa: E402

REF = ROOT / "samples" / "ref"
WORK = OUT_DIR / "ref"
MD = ROOT / "bench" / "ref_results.md"
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
SCALES = (1 / 4, 1 / 3, 1 / 2, 2 / 3, 3 / 4, 1.0, 4 / 3, 3 / 2, 2.0, 3.0, 4.0)
SR, HOP = 22050, 2048
# "Exact" = within HALF of our ticks: half of a 12th of a quarter, the tolerance used
# since the first runs (when ticks were 12 a quarter), so results stay comparable.
HALF = T / 24


def nm(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def pct(a: float, b: float) -> float | None:
    return round(100.0 * a / b, 1) if b else None


def pieces_registry() -> dict:
    from make_refs import PIECES
    return PIECES


# ---------------------------------------------------------------- the pipeline side

def song_dir(slug: str) -> Path:
    """out/ref/<piece>/ laid out like an upload: input.<ext> + meta.json (analysis cached there)."""
    d = WORK / slug
    rec = REF / slug / "rec"
    meta = json.loads((rec / "source.json").read_text())
    src = rec / meta["file"]
    d.mkdir(parents=True, exist_ok=True)
    if not any(d.glob("input.*")):
        shutil.copy(src, d / ("input" + src.suffix.lower()))
    if not (d / "meta.json").exists():
        title = pieces_registry()[slug]["title"]
        (d / "meta.json").write_text(json.dumps({"title": title, "status": "analyzing"}))
    return d


def analyze(slug: str, allow: bool):
    d = song_dir(slug)
    if not (d / "analysis.json").exists() and not allow:
        return None
    t0 = time.time()
    an = pipeline.analyze(d, pieces_registry()[slug]["kind"])
    if time.time() - t0 > 5:
        print(f"{slug}: analysed ({an.kind}, {time.time() - t0:.0f} s)", flush=True)
    return an


def beat_ticks(obj) -> int:
    return int(getattr(obj, "beat_ticks", T))


def bar_ticks(arr) -> int:
    return arr.beats_per_bar * beat_ticks(arr)


def time_sig(arr) -> tuple[int, int]:
    ts = getattr(arr, "time_signature", None)
    if callable(ts):
        ts = ts()
    return tuple(ts) if ts else (arr.beats_per_bar, 4)


def quarter_bpm(arr) -> float:
    """Our printed tempo in quarter notes per minute."""
    return round(arr.bpm) * beat_ticks(arr) / T


def arrange(slug: str, an) -> dict:
    """Default options, flute alone; the score files and page 1 as PNG in out/ref/<piece>/."""
    from sheetswap.render import render_svg
    from sheetswap.score import write_outputs

    opts = ArrangeOptions(mode="solo")
    arr = pipeline.arrange(an, opts)
    melody, _, _ = pipeline.pick_melody(an, opts.melody_source)
    grid = pipeline.bar_grid(an, opts, melody)
    out = WORK / slug
    paths = write_outputs(arr, pieces_registry()[slug]["title"] + " (flute)", out)
    svgs = render_svg(paths["musicxml"])
    (out / "page-1.svg").write_text(svgs[0] if svgs else "")
    png = out / "page-1.png"
    png.unlink(missing_ok=True)
    if svgs:
        subprocess.run(["qlmanage", "-t", "-s", "1400", "-o", str(out), str(out / "page-1.svg")],
                       capture_output=True, timeout=120)
        made = out / "page-1.svg.png"
        if made.exists():
            made.rename(png)
    return dict(arr=arr, grid=grid, xml=paths["musicxml"], pages=len(svgs))


# ---------------------------------------------------------------- alignment

MATCH, SUB, GAP_REF, GAP_OURS = 2.0, -1.0, -1.0, -0.5


def dp_align(ref_pc: np.ndarray, our_pc: np.ndarray, allowed: np.ndarray | None = None):
    """Every reference note against any stretch of ours (free ends on our side).
    Returns (score, [(i, j)] diagonal pairs) with i, j indexes into ref / ours."""
    n, m = len(ref_pc), len(our_pc)
    S = np.where(ref_pc[:, None] == our_pc[None, :], MATCH, SUB)
    if allowed is not None:
        S = np.where(allowed, S, -1e9)
    D = np.zeros((n + 1, m + 1))
    D[:, 0] = GAP_REF * np.arange(n + 1)
    A = np.full((n + 1, m + 1), -np.inf)
    diag_used = np.zeros((n + 1, m + 1), dtype=bool)
    idx = np.arange(m + 1)
    for i in range(1, n + 1):
        diag = D[i - 1, :-1] + S[i - 1]
        up = D[i - 1, 1:] + GAP_REF
        a = np.maximum(diag, up)
        A[i, 1:] = a
        diag_used[i, 1:] = diag >= up
        row = np.concatenate([[D[i, 0]], a])
        D[i] = np.maximum.accumulate(row - GAP_OURS * idx) + GAP_OURS * idx
    j = int(np.argmax(D[n]))
    score, i, pairs = float(D[n, j]), n, []
    while i > 0:
        if j > 0 and abs(D[i, j] - A[i, j]) < 1e-9:
            if diag_used[i, j]:
                pairs.append((i - 1, j - 1))
                j -= 1
            i -= 1
        elif j > 0:
            j -= 1
        else:
            i -= 1
    return score, pairs[::-1]


def running(values: np.ndarray, half: int, fn) -> np.ndarray:
    return np.array([fn(values[max(0, k - half): k + half + 1]) for k in range(len(values))])


def mode(xs) -> float:
    return Counter(np.round(np.asarray(xs), 6)).most_common(1)[0][0]


def tempo_scale(q: np.ndarray, tau: np.ndarray) -> float:
    """Median of our quarters (ticks / T) per reference quarter over spans of >= 2 quarters."""
    ratios, k = [], 0
    for a in range(len(q)):
        while k < len(q) and q[k] - q[a] < 2:
            k += 1
        if k < len(q) and q[k] - q[a] <= 8:
            r = (tau[k] - tau[a]) / T / (q[k] - q[a])
            if r > 0:
                ratios.append(r)
    return float(np.median(ratios)) if ratios else 1.0


def align(ref: dict, arr) -> dict:
    rn = ref["notes"]
    flute = sorted(arr.flute, key=lambda x: x.start)
    rp = np.array([n["pitch"] for n in rn])
    rq = np.array([n["onset_beats"] for n in rn], dtype=float)
    fp = np.array([x.pitch for x in flute])
    ft = np.array([x.start for x in flute], dtype=float)
    if len(fp) == 0:
        return dict(pairs=[], trans=0)
    best = None
    for t in range(12):
        sc, pairs = dp_align((rp + t) % 12, fp % 12)
        if best is None or sc > best[0]:
            best = (sc, t, pairs)
    _, trans, pairs = best
    rpc, fpc = (rp + trans) % 12, fp % 12

    def hits_of(prs):
        h = [(i, j) for i, j in prs if rpc[i] == fpc[j]]
        return rq[[i for i, _ in h]], ft[[j for _, j in h]]

    hq, ht = hits_of(pairs)
    if len(hq) >= 8:
        # Repeated passages can pair up with the wrong repetition. Second pass: only
        # near one straight line (our ticks against written quarters) that most matches
        # agree on; third pass: near the local course of the second pass's matches.
        line = robust_line(hq, ht, tol=2 * bar_ticks(arr))
        if line is not None:
            allowed = np.abs(ft[None, :] - np.polyval(line, rq)[:, None]) <= 6 * bar_ticks(arr)
            _, pairs = dp_align(rpc, fpc, allowed)
            hq, ht = hits_of(pairs)
    if len(hq) >= 8:
        s = tempo_scale(hq, ht)
        off = running(ht - s * T * hq, 15, np.median)
        expect = s * T * rq + np.interp(rq, hq, off)
        width = max(2 * bar_ticks(arr), 4 * T * s)
        allowed = np.abs(ft[None, :] - expect[:, None]) <= width
        _, pairs = dp_align(rpc, fpc, allowed)
    return dict(pairs=pairs, trans=trans, flute=flute)


def robust_line(x: np.ndarray, y: np.ndarray, tol: float, tries: int = 400):
    """y ~ a*x + b through the most points within tol (random pairs, then least squares)."""
    rng = np.random.default_rng(0)
    best, best_n = None, 0
    for _ in range(tries):
        i, j = rng.choice(len(x), 2, replace=False)
        if abs(x[j] - x[i]) < 4:
            continue
        a = (y[j] - y[i]) / (x[j] - x[i])
        if a <= 0:
            continue
        b = y[i] - a * x[i]
        n = int(np.sum(np.abs(y - (a * x + b)) <= tol))
        if n > best_n:
            best, best_n = (a, b), n
    if best is None:
        return None
    ok = np.abs(y - (best[0] * x + best[1])) <= tol
    return np.polyfit(x[ok], y[ok], 1) if ok.sum() >= 2 else np.array(best)


# ---------------------------------------------------------------- the measures

def notation_stats(xml: Path) -> dict:
    """Ties per bar and notes shorter than an 8th as printed in our flute part."""
    from music21 import converter
    part = converter.parse(str(xml)).parts[0]
    notes = [n for n in part.recurse().notes]
    bars = len({n.measureNumber for n in notes}) or 1
    ties = sum(1 for n in notes if n.tie is not None and n.tie.type in ("start", "continue"))
    short = sum(1 for n in notes if n.duration.quarterLength < 0.5 - 1e-6 and not n.duration.tuplets)
    return dict(ties_per_bar=round(ties / bars, 2), short_pct=pct(short, len(notes)))


def ref_notation_stats(ref: dict) -> dict:
    """The same counts for the real score: ties = notes held over a bar line (the score's
    own ties inside a bar are not in the MIDI, so this is a lower bound)."""
    L = ref["bar_beats"]
    rn = ref["notes"]
    ties = sum(max(0, math.floor((n["onset_beats"] + n["dur_beats"] - 1e-6) / L)
                   - math.floor(n["onset_beats"] / L + 1e-9)) for n in rn)
    bars = len({math.floor(n["onset_beats"] / L + 1e-9) for n in rn}) or 1
    short = sum(1 for n in rn if n["dur_beats"] < 0.5 - 1e-6 and abs(n["dur_beats"] * 3 - 1) > 1e-3)
    return dict(ties_per_bar=round(ties / bars, 2), short_pct=pct(short, len(rn)))


def measure(ref: dict, arr, al: dict) -> dict:
    rn, flute, trans = ref["notes"], al.get("flute", []), al["trans"]
    pairs = al["pairs"]
    L = ref["bar_beats"]
    bt, bar = beat_ticks(arr), bar_ticks(arr)
    hits = [(i, j) for i, j in pairs if (rn[i]["pitch"] + trans) % 12 == flute[j].pitch % 12]
    paired_j = {j for _, j in pairs}
    out = dict(n_ref=len(rn), n_ours=len(flute), transpose=trans,
               found_pct=pct(len(hits), len(rn)),
               pitch_pct=pct(len(hits), len(pairs)),
               extra_pct=pct(len(flute) - len(paired_j), len(flute)))
    if len(hits) < 4:
        return out
    octs = Counter((flute[j].pitch - rn[i]["pitch"] - trans) // 12 for i, j in hits)
    oc, ocn = octs.most_common(1)[0]
    out.update(octave=int(oc), same_octave_pct=pct(ocn, len(hits)))
    q = np.array([rn[i]["onset_beats"] for i, _ in hits])
    qd = np.array([rn[i]["dur_beats"] for i, _ in hits])
    tau = np.array([flute[j].start for _, j in hits], dtype=float)
    dur = np.array([flute[j].dur for _, j in hits], dtype=float)

    # raw: our quarter = their quarter, our bar 1 = their bar 1
    our_q = tau / T
    in_bar = np.abs(((our_q % (bar / T)) - (q % L))) < HALF / T
    in_beat = np.abs(((tau % T) / T) - (q % 1.0)) < HALF / T
    down = np.abs(q % L) < 1e-6
    on_line = (tau % bar) == 0
    out["raw"] = dict(meter=f"{time_sig(arr)[0]}/{time_sig(arr)[1]}",
                      onset_in_bar_pct=pct(in_bar.sum(), len(q)),
                      onset_in_beat_pct=pct(in_beat.sum(), len(q)),
                      downbeats_on_barline_pct=pct((down & on_line).sum(), down.sum()))

    # tempo ratio and the reference's scale
    s_raw = tempo_scale(q, tau)
    s = min(SCALES, key=lambda c: abs(math.log(c / s_raw)))
    k = T * s                                   # our ticks per written quarter
    u = tau / k                                 # our onsets in written quarters
    d = u - q
    beat_q = bt / k                             # one of our beats, in written quarters
    snapped = np.round(d / beat_q) * beat_q
    phase = running(snapped, 8, mode)           # where our bar 1 sits, locally
    e = d - phase
    slips = int(np.sum(np.abs(np.diff(phase)) > 1e-6))
    bar_off = Counter(np.round(phase % L, 4)).most_common(1)[0]
    meter_ok = abs(bar / k - L) < 1e-6
    off = bar_off[0] if bar_off[0] <= L / 2 else bar_off[0] - L
    out.update(
        tempo_ratio=round(s_raw, 3), tempo_ratio_snapped=round(s, 3),
        printed_qbpm=round(quarter_bpm(arr), 1),
        meter_ok=meter_ok, bar_written_quarters=round(bar / k, 3), ref_bar_quarters=L,
        bar_offset_quarters=round(off, 3), bar_offset_share_pct=pct(bar_off[1], len(phase)),
        downbeat_ok=bool(meter_ok and abs(off) < 1e-6), beat_slips=slips,
        onset_exact_pct=pct(np.sum(np.abs(e * k) < HALF), len(e)),
        onset_16th_pct=pct(np.sum(np.abs(e) <= 0.25 + 1e-6), len(e)),
        onset_mae_beats=round(float(np.mean(np.abs(e))), 3),
        onset_bias_beats=round(float(np.mean(e)), 3),
        early_pct=pct(np.sum(e * k <= -HALF), len(e)), late_pct=pct(np.sum(e * k >= HALF), len(e)),
        plain_tempo_ratio=bool(abs(math.log(s_raw / s)) < 0.05),
        dur_exact_pct=pct(np.sum(np.abs(dur / k - qd) < HALF / k), len(qd)),
        dur_16th_pct=pct(np.sum(np.abs(dur / k - qd) <= 0.25 + 1e-6), len(qd)),
    )
    # same gap to the next note (rhythm), for neighbours matched on both sides
    ioi = [(rn[i2]["onset_beats"] - rn[i1]["onset_beats"], (flute[j2].start - flute[j1].start) / k)
           for (i1, j1), (i2, j2) in zip(hits, hits[1:]) if i2 == i1 + 1]
    if ioi:
        a = np.array(ioi)
        out.update(ioi_exact_pct=pct(np.sum(np.abs(a[:, 0] - a[:, 1]) < HALF / k), len(a)),
                   ioi_16th_pct=pct(np.sum(np.abs(a[:, 0] - a[:, 1]) <= 0.25 + 1e-6), len(a)))
    # what the errors look like, for the report
    err_kinds = Counter()
    for x in e:
        if abs(x) < HALF / k:
            continue
        err_kinds[f"{x:+.3g}"] += 1
    out["common_onset_errors"] = dict(err_kinds.most_common(4))
    worst = np.argsort(-np.abs(e))[:5]
    out["worst_examples"] = [
        f"{nm(rn[hits[w][0]]['pitch'])} written bar {int(q[w] // L) + 1} beat {q[w] % L + 1:g} "
        f"-> ours {e[w]:+.2f} q" for w in worst if abs(e[w]) > HALF / k]
    out["_hits"] = hits
    out["_scale"] = k
    return out


# ---------------------------------------------------------------- listening

def flute_midi(notes: list[tuple[int, float, float]]):
    import pretty_midi
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=FLUTE_PROGRAM, name="Flute")
    for p, s, e in notes:
        if e > s:
            inst.notes.append(pretty_midi.Note(84, int(p), float(s), float(e)))
    pm.instruments.append(inst)
    return pm


def render(notes: list[tuple[int, float, float]], wav: Path) -> np.ndarray:
    import librosa
    from sheetswap.synth import synthesize, write_wav
    audio = synthesize(flute_midi(notes), sr=SR)
    write_wav(audio, wav, sr=SR)
    y = audio.mean(axis=1) if audio.ndim == 2 else audio
    return librosa.util.normalize(y.astype(np.float32))


def features(y: np.ndarray):
    import librosa
    c = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    o = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    n = min(c.shape[1], len(o))
    c, o = c[:, :n], o[:n]
    c = c / np.maximum(np.linalg.norm(c, axis=0, keepdims=True), 1e-9)
    o = o / max(float(np.percentile(o, 99)), 1e-9)
    return c, np.clip(o, 0, 1.5)


def warp(ya: np.ndarray, yb: np.ndarray):
    """Time in a -> time in b: DTW on chroma (cosine) + onset strength, then a running
    median over +-1.5 s so single notes can still be early or late."""
    import librosa
    ca, oa = features(ya)
    cb, ob = features(yb)
    C = 1.0 - ca.T @ cb + ONSET_W * np.abs(oa[:, None] - ob[None, :])
    _, wp = librosa.sequence.dtw(C=C)
    wp = wp[::-1] * HOP / SR
    grid = np.arange(0.0, wp[-1, 0] + 0.1, 0.1)
    tb = np.array([np.median(wp[np.abs(wp[:, 0] - t) <= SMOOTH_S, 1]) for t in grid])
    tb = np.maximum.accumulate(tb)
    fine = librosa.feature.chroma_cqt(y=yb, sr=SR, hop_length=FINE_HOP)   # for the pitch check
    return (lambda t: np.interp(t, grid, tb)), fine


FINE_HOP = 512
ONSET_W = 0.5        # weight of the onset-strength difference in the DTW cost
SMOOTH_S = 1.5       # +- seconds of the running median over the DTW path


def compare_to(our_notes, warp_fn, chroma_b, other_onsets: dict[int, np.ndarray], beat_s: float) -> dict:
    """Each of our notes against the other audio at the place the warp puts it."""
    devs, agree, top3, total = [], 0, 0, 0
    nfr = chroma_b.shape[1]
    for p, s, e in our_notes:
        tb = float(warp_fn(s))
        te = float(warp_fn(max(e, s + 0.05)))
        a = int((tb + 0.3 * (te - tb)) * SR / FINE_HOP)       # past the attack
        b = max(a + 1, int(te * SR / FINE_HOP))
        if a >= nfr:
            continue
        total += 1
        m = chroma_b[:, a:min(b, nfr)].mean(axis=1)
        order = np.argsort(-m)
        agree += order[0] == p % 12
        top3 += (p % 12) in order[:3]
        cand = other_onsets.get(p % 12)
        if cand is not None and len(cand):
            k = int(np.argmin(np.abs(cand - tb)))
            if abs(cand[k] - tb) <= 0.5 * beat_s:
                devs.append((s, cand[k]))
    devs = local_residuals(np.array(devs)) if len(devs) >= 8 else np.zeros(0)
    out = dict(pitch_top1_pct=pct(agree, total), pitch_top3_pct=pct(top3, total),
               timed_notes_pct=pct(len(devs), total))
    if len(devs):
        out.update(dev_median_ms=round(1000 * float(np.median(devs)), 0),
                   dev_p90_ms=round(1000 * float(np.percentile(devs, 90)), 0),
                   off_50ms_pct=pct(np.sum(devs > 0.05), len(devs)),
                   dev_median_beats=round(float(np.median(devs)) / beat_s, 3),
                   dev_p90_beats=round(float(np.percentile(devs, 90)) / beat_s, 3))
    return out


def local_residuals(pairs: np.ndarray, half: int = 6) -> np.ndarray:
    """|timing deviation| of each paired note: its onset in the other audio against a
    straight line (local tempo and offset) through its neighbours' pairs, itself left out.
    A whole passage played slower or later is not an error; one note early or late is."""
    pairs = pairs[np.argsort(pairs[:, 0])]
    out = []
    for k in range(len(pairs)):
        nb = np.r_[pairs[max(0, k - half):k], pairs[k + 1:k + 1 + half]]
        if len(nb) < 4:
            continue
        # Theil-Sen: a wrongly paired neighbour doesn't tilt the line
        i, j = np.triu_indices(len(nb), 1)
        dx = nb[j, 0] - nb[i, 0]
        ok = dx > 1e-6
        if not ok.any():
            continue
        a = float(np.median((nb[j, 1] - nb[i, 1])[ok] / dx[ok]))
        b = float(np.median(nb[:, 1] - a * nb[:, 0]))
        out.append(pairs[k, 1] - (a * pairs[k, 0] + b))
    return np.abs(np.array(out))


def onsets_by_pc(rows) -> dict[int, np.ndarray]:
    d: dict[int, list[float]] = {}
    for p, s, *_ in rows:
        d.setdefault(int(p) % 12, []).append(float(s))
    return {k: np.array(sorted(v)) for k, v in d.items()}


def listen(slug: str, ref: dict, res: dict, an, m: dict) -> dict:
    import librosa
    import mir_eval
    from sheetswap import transcribe

    arr, grid = res["arr"], res["grid"]
    out_dir = WORK / slug
    spt = 60.0 / round(arr.bpm) / beat_ticks(arr)           # seconds per tick, as printed
    lead = -min((q.start for q in arr.flute), default=0)
    lead = max(lead, 0)
    ours = [(q.pitch, (q.start + lead) * spt, (q.end + lead) * spt) for q in arr.flute]
    # the recording's average tempo (written quarters per minute), from matched notes
    hits, k = m.get("_hits", []), m.get("_scale", T)
    flute = sorted(arr.flute, key=lambda x: x.start)
    secs = [(ref["notes"][i]["onset_beats"], grid.to_seconds(flute[j].start / beat_ticks(grid)))
            for i, j in hits]
    real_qbpm = ref.get("bpm_marking") or 60.0
    if len(secs) >= 8:
        a = np.array(secs)
        lo, hi = np.percentile(a[:, 0], [5, 95])
        sel = (a[:, 0] >= lo) & (a[:, 0] <= hi)
        slope = np.polyfit(a[sel, 0], a[sel, 1], 1)[0]
        if slope > 0:
            real_qbpm = 60.0 / slope
    qs = 60.0 / real_qbpm
    pick = ref["pickup_beats"]
    refn = []
    for n in ref["notes"]:
        p = n["pitch"]
        while p < 60:
            p += 12
        refn.append((p, (n["onset_beats"] + pick) * qs, (n["onset_beats"] + pick + n["dur_beats"]) * qs))
    y_ours = render(ours, out_dir / "ours.wav")
    y_ref = render(refn, out_dir / "ref.wav")
    out = dict(real_qbpm=round(real_qbpm, 1))

    w, cb = warp(y_ours, y_ref)
    out["vs_reference"] = compare_to(ours, w, cb, onsets_by_pc(refn), qs)
    # noise floor of this check: the reference against itself played 15% slower
    slow = [(p, s * 1.15, e * 1.15) for p, s, e in refn]
    y_slow = render(slow, out_dir / "ref_slow.wav")
    w0, cb0 = warp(y_ref, y_slow)
    floor = compare_to(refn, w0, cb0, onsets_by_pc(slow), qs * 1.15)
    out["floor"] = {k: floor.get(k) for k in ("dev_median_ms", "dev_p90_ms", "off_50ms_pct", "pitch_top1_pct")}
    (out_dir / "ref_slow.wav").unlink(missing_ok=True)
    y_orig, _ = librosa.load(str(out_dir / "audio.wav"), sr=SR)
    w2, cb2 = warp(y_ours, y_orig)
    heard = [(n.pitch, n.start) for src in an.sources.values() for n in src]
    out["vs_recording"] = compare_to(ours, w2, cb2, onsets_by_pc(heard), qs)

    # sanity: our rendered flute transcribed back should be our score
    back = transcribe.general(out_dir / "ours.wav")
    ri = np.array([[s, e] for _, s, e in ours if e > s])
    rp = np.array([librosa.midi_to_hz(p) for p, s, e in ours if e > s])
    ei = np.array([[n.start, n.end] for n in back if n.end > n.start])
    ep = np.array([librosa.midi_to_hz(n.pitch) for n in back if n.end > n.start])
    if len(ri) and len(ei):
        _, _, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
            ri, rp, ei, ep, onset_tolerance=0.05, pitch_tolerance=50, offset_ratio=None)
        out["transcribed_back_f1"] = round(float(f), 3)
    return out


# ---------------------------------------------------------------- running + report

def run_piece(slug: str, an, do_listen: bool) -> dict:
    ref = json.loads((REF / slug / "ref.json").read_text())
    res = arrange(slug, an)
    arr = res["arr"]
    al = align(ref, arr)
    m = measure(ref, arr, al)
    row = dict(piece=slug, title=ref["title"], instrument=ref["instrument"],
               ref_meter=f"{ref['meter'][0]}/{ref['meter'][1]}", ref_key=ref["key"],
               kind=an.kind, ours_key=f"{arr.key_fifths:+d} {arr.key_mode}",
               notation=notation_stats(res["xml"]), ref_notation=ref_notation_stats(ref),
               pages=res["pages"], **m)
    if do_listen:
        row["listen"] = listen(slug, ref, res, an, m)
    flute_ref = REF / slug / "ref_flute.json"
    if flute_ref.exists():
        fr = json.loads(flute_ref.read_text())
        fm = measure(fr, arr, align(fr, arr))
        row["vs_flute_arrangement"] = {k: v for k, v in fm.items() if not k.startswith("_")}
    clean = {k: v for k, v in row.items() if not k.startswith("_")}
    (WORK / slug / "result.json").write_text(json.dumps(clean, indent=1, ensure_ascii=False))
    return clean


def g(d: dict, *path, digits=None, default="–"):
    for p in path:
        if not isinstance(d, dict) or p not in d or d[p] is None:
            return default
        d = d[p]
    if digits is not None and isinstance(d, (int, float)) and not isinstance(d, bool):
        return f"{d:.{digits}f}"
    if isinstance(d, bool):
        return "yes" if d else "no"
    return d


def mean_of(rows, *path):
    vals = [g(r, *path, default=None) for r in rows]
    vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return f"{np.mean(vals):.1f}" if vals else "–"


def write_md(rows: list[dict]) -> None:
    L = ["# Timing against real sheet music", "",
         "`bench/ref_score.py` on the reference set in `samples/ref/` (sources: `bench/REFERENCES.md`).",
         "Default options, flute alone. All beats are written quarter notes.", "",
         "## Notes and timing", "",
         "| piece | instr. | notes found | pitch right | extra | onset exact | onset ±16th | timing off (mean, beats) | early / late | same length | same gap to next | meter ours / real | bar = real bar | bar lines off by (q) | beat slips | tempo ratio | raw: onset in bar | raw: downbeats on bar lines |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ratio = f"{g(r, 'tempo_ratio')}" + ("" if r.get("plain_tempo_ratio", True) else " (not plain)")
        L.append(f"| {r['piece']} | {r['instrument']} | {g(r, 'found_pct')}% | {g(r, 'pitch_pct')}% | "
                 f"{g(r, 'extra_pct')}% | {g(r, 'onset_exact_pct')}% | {g(r, 'onset_16th_pct')}% | "
                 f"{g(r, 'onset_mae_beats')} | {g(r, 'early_pct')}% / {g(r, 'late_pct')}% | "
                 f"{g(r, 'dur_exact_pct')}% | "
                 f"{g(r, 'ioi_exact_pct')}% | {g(r, 'raw', 'meter')} / {r['ref_meter']} | "
                 f"{g(r, 'meter_ok')} | {g(r, 'bar_offset_quarters')} ({g(r, 'bar_offset_share_pct')}%) | "
                 f"{g(r, 'beat_slips')} | {ratio} | {g(r, 'raw', 'onset_in_bar_pct')}% | "
                 f"{g(r, 'raw', 'downbeats_on_barline_pct')}% |")
    L.append(f"| **mean** | | {mean_of(rows, 'found_pct')}% | {mean_of(rows, 'pitch_pct')}% | "
             f"{mean_of(rows, 'extra_pct')}% | {mean_of(rows, 'onset_exact_pct')}% | "
             f"{mean_of(rows, 'onset_16th_pct')}% | {mean_of(rows, 'onset_mae_beats')} | "
             f"{mean_of(rows, 'early_pct')}% / {mean_of(rows, 'late_pct')}% | "
             f"{mean_of(rows, 'dur_exact_pct')}% | {mean_of(rows, 'ioi_exact_pct')}% | | "
             f"{sum(1 for r in rows if r.get('meter_ok'))}/{len(rows)} | "
             f"{sum(1 for r in rows if r.get('downbeat_ok'))}/{len(rows)} right | "
             f"{mean_of(rows, 'beat_slips')} | {sum(1 for r in rows if r.get('plain_tempo_ratio'))}/{len(rows)} plain | "
             f"{mean_of(rows, 'raw', 'onset_in_bar_pct')}% | "
             f"{mean_of(rows, 'raw', 'downbeats_on_barline_pct')}% |")
    L += ["", "How to read it: *tempo ratio* = our quarter notes per written quarter (2 = every "
          "note value doubled, the tempo mark doubled; *not plain* = no simple ratio fits, the beat "
          "we wrote doesn't follow the music's pulse). *bar = real bar*: our bar holds as much "
          "music as the real one, after the tempo ratio. *bar lines off by*: where our bar 1 "
          "falls against the real one, in written quarters (share of notes with that offset). "
          "*beat slips*: times that offset jumps by one of our beats along the piece. *onset*: "
          "each matched note's place after those corrections (16th = 0.25). *early / late*: "
          "notes at least half a tick early or late. *raw*: no corrections at all."]
    L += ["", "## Reading the page", "",
          "| piece | ties per bar, ours / score | notes shorter than an 8th, ours / score | printed tempo (quarter) | played at (quarter) | marking |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['piece']} | {g(r, 'notation', 'ties_per_bar')} / {g(r, 'ref_notation', 'ties_per_bar')} | "
                 f"{g(r, 'notation', 'short_pct')}% / {g(r, 'ref_notation', 'short_pct')}% | "
                 f"{g(r, 'printed_qbpm')} | {g(r, 'listen', 'real_qbpm')} | "
                 f"{g(json.loads((REF / r['piece'] / 'ref.json').read_text()), 'bpm_marking')} |")
    L += ["", "## Listening", "",
          "Our flute part (at its printed tempo) vs the reference melody (at the recording's "
          "average tempo), both synthesized as flute, and vs the original recording. DTW on "
          "chroma + onset strength finds where each of our notes lands; it is paired with the "
          "nearest note of the same pitch class there (the score's notes, or the notes heard in "
          "the recording). Deviation = how far that pair sits off a straight line through its "
          "six neighbours on each side (local tempo and offset), so a passage played slower is "
          "not an error and one note early or late is (against the recording it also holds the "
          "performer's own note-to-note rubato, which a steady flute part can't follow; against "
          "the score it is ours alone). *pitch = loudest*: our pitch class is the "
          "strongest one in the other audio there. *floor*: the score against itself played 15% "
          "slower (should be 0). *transcribed back*: Basic Pitch on our rendered flute vs our "
          "score (onset 50 ms), a check that the audio is the score.", "",
          "| piece | vs score: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch = loudest | floor (ms) | vs recording: median / 90% dev (ms) | (beats) | notes off > 50 ms | pitch in top 3 | transcribed back F1 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        a, b = ("listen", "vs_reference"), ("listen", "vs_recording")
        L.append(f"| {r['piece']} | {g(r, *a, 'dev_median_ms')} / {g(r, *a, 'dev_p90_ms')} | "
                 f"{g(r, *a, 'dev_median_beats')} / {g(r, *a, 'dev_p90_beats')} | {g(r, *a, 'off_50ms_pct')}% | "
                 f"{g(r, *a, 'pitch_top1_pct')}% | "
                 f"{g(r, 'listen', 'floor', 'dev_median_ms')} / {g(r, 'listen', 'floor', 'dev_p90_ms')} | "
                 f"{g(r, *b, 'dev_median_ms')} / {g(r, *b, 'dev_p90_ms')} | "
                 f"{g(r, *b, 'dev_median_beats')} / {g(r, *b, 'dev_p90_beats')} | {g(r, *b, 'off_50ms_pct')}% | "
                 f"{g(r, *b, 'pitch_top3_pct')}% | {g(r, 'listen', 'transcribed_back_f1')} |")
    L.append(f"| **mean** | {mean_of(rows, 'listen', 'vs_reference', 'dev_median_ms')} / "
             f"{mean_of(rows, 'listen', 'vs_reference', 'dev_p90_ms')} | | "
             f"{mean_of(rows, 'listen', 'vs_reference', 'off_50ms_pct')}% | "
             f"{mean_of(rows, 'listen', 'vs_reference', 'pitch_top1_pct')}% | "
             f"{mean_of(rows, 'listen', 'floor', 'dev_median_ms')} / "
             f"{mean_of(rows, 'listen', 'floor', 'dev_p90_ms')} | "
             f"{mean_of(rows, 'listen', 'vs_recording', 'dev_median_ms')} / "
             f"{mean_of(rows, 'listen', 'vs_recording', 'dev_p90_ms')} | | "
             f"{mean_of(rows, 'listen', 'vs_recording', 'off_50ms_pct')}% | "
             f"{mean_of(rows, 'listen', 'vs_recording', 'pitch_top3_pct')}% | "
             f"{mean_of(rows, 'listen', 'transcribed_back_f1')} |")
    flute_rows = [r for r in rows if "vs_flute_arrangement" in r]
    if flute_rows:
        L += ["", "## Against a published flute arrangement", "",
              "| piece | notes found | onset exact | timing off (beats) | same length |", "|---|---|---|---|---|"]
        for r in flute_rows:
            f = r["vs_flute_arrangement"]
            L.append(f"| {r['piece']} | {g(f, 'found_pct')}% | {g(f, 'onset_exact_pct')}% | "
                     f"{g(f, 'onset_mae_beats')} | {g(f, 'dur_exact_pct')}% |")
    L += ["", "## Largest onset errors (after tempo and bar-line correction)", ""]
    for r in rows:
        ex = "; ".join(r.get("worst_examples", [])[:3]) or "–"
        L.append(f"- **{r['piece']}**: commonest errors (quarters: count) {r.get('common_onset_errors', {})}; {ex}")
    MD.write_text("\n".join(L) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--piece", action="append", default=[])
    ap.add_argument("--no-analyze", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--no-listen", action="store_true")
    ap.add_argument("--reanalyze", action="store_true", help="drop the cached analyses first")
    a = ap.parse_args()
    slugs = a.piece or [s for s in pieces_registry() if (REF / s / "ref.json").exists()
                        and (REF / s / "rec" / "source.json").exists()]
    rows = []
    for slug in slugs:
        if a.reanalyze:
            (WORK / slug / "analysis.json").unlink(missing_ok=True)
        an = analyze(slug, not a.no_analyze)
        if an is None:
            print(f"{slug}: no cached analysis (run without --no-analyze)", flush=True)
            continue
        if a.analyze_only:
            continue
        r = run_piece(slug, an, not a.no_listen)
        rows.append(r)
        print(f"{slug}: found {g(r, 'found_pct')}% pitch {g(r, 'pitch_pct')}% onset exact "
              f"{g(r, 'onset_exact_pct')}% ±16th {g(r, 'onset_16th_pct')}% off {g(r, 'onset_mae_beats')} q, "
              f"meter {g(r, 'raw', 'meter')} vs {r['ref_meter']} ok={g(r, 'meter_ok')}, bar off "
              f"{g(r, 'bar_offset_quarters')} q, tempo x{g(r, 'tempo_ratio')}, listen dev "
              f"{g(r, 'listen', 'vs_reference', 'dev_median_ms')} ms", flush=True)
    if rows and not a.piece:
        write_md(rows)
    elif rows:
        # one piece: refresh its row in the report from all saved results
        saved = [json.loads((WORK / s / "result.json").read_text()) for s in pieces_registry()
                 if (WORK / s / "result.json").exists()]
        write_md(saved)


if __name__ == "__main__":
    main()
