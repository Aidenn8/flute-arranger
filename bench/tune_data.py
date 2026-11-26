"""Training data for the learned tune picker (bench/train_tune_picker.py).

Two public datasets, read from data/ (git-ignored; sources and licenses in
bench/TRAINING.md):

- POP909 (data/pop909/): 909 pop songs arranged for piano, each MIDI file with a
  MELODY track (the sung tune, played on the piano), a BRIDGE track (fills and
  counter-melodies) and a PIANO track (the accompaniment, with pedal).
  Tune = MELODY. BRIDGE notes are kept as input but not scored either way.
  The arrangers played the tune far louder than the rest (velocity ~110 over
  ~50); real pianists voice it much less, so each song gets a random voicing
  gap instead (VOICING_GAP), some songs none at all.
- ASAP (data/asap/): performances of classical piano pieces (MIDI from a
  Disklavier, real velocities and pedal) aligned beat by beat to their scores.
  Tune = the top line of the score's right-hand staff (as bench/make_refs.py
  takes the references: at each onset the highest new note, unless a note at
  least as high still sounds), carried over to the performance through the
  beat alignment. Performance notes with no score note (ornaments, slips) are
  input but not scored.

Splits are by piece (song id, or composer + title), so no piece is in two
splits. None of the pieces bench/ref_score.py judges is in either dataset
(checked by title in train_tune_picker.py).

What the app sees is not the MIDI but our piano transcription of a recording.
`render_and_transcribe` renders an excerpt with the piano soundfont (note ends
held by the pedal, as a piano rings, plus a little random room reverb and
noise) and runs the app's own transcriber on it; `match` carries the labels
over to the transcribed notes (ghost notes the model invents are "not tune").
`ErrorModel` measures what the transcriber did on those excerpts (missed
notes by loudness, onset jitter, velocity mapping, ends, ghost notes) and
`ErrorModel.corrupt` applies the same to whole pieces, which is how most of
the training set is made (rendering and transcribing everything would take a
day on this Mac).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POP909 = DATA / "pop909" / "POP909-Dataset-master" / "POP909"
ASAP = DATA / "asap" / "asap-dataset-master"
CACHE = Path(os.environ.get("TUNE_PICKER_CACHE") or DATA / "tune_cache")   # override: a scratch run

SR = 16000                 # the piano transcriber's own sample rate: no resampling
MATCH_TOL = 0.06           # s, transcribed note onset vs source onset, same pitch
ALIGN_TOL = 0.3            # s, score note (mapped through the beats) vs performance note
VOICING_GAP = (-4.0, 30.0) # POP909: tune velocity over the accompaniment's median
PEDAL_ON = 64

# Pieces bench/ref_score.py and bench/piano_tune.py judge: never trained on.
# (ASAP and POP909 hold none of them; this is checked against their titles.)
JUDGE_TITLES = [
    ("Beethoven", "Fur Elise"), ("Beethoven", "Sonata 14 1"), ("Satie", "Gymnopedie"),
    ("Chopin", "Nocturne 9 2"), ("Chopin", "Prelude 28"), ("Chopin", "Waltz 69"),
    ("Schumann", "Kinderszenen"), ("Schumann", "Traumerei"), ("Schumann", "Op 68"),
    ("Debussy", "Clair de lune"), ("Debussy", "Suite bergamasque"), ("Mozart", "Sonata 16"),
    ("Mozart", "545"), ("Brahms", "Op 39"), ("Brahms", "Waltz"), ("Tchaikovsky", "Op 39"),
]


@dataclass
class Piece:
    """Notes of one performance (seconds), as parallel arrays.

    label: 1 = tune note. weight: 0 = not scored (BRIDGE, unaligned notes).
    pedal: (down, up) intervals in seconds."""
    pid: str
    source: str
    split: str
    pitch: np.ndarray
    start: np.ndarray
    end: np.ndarray
    vel: np.ndarray
    label: np.ndarray
    weight: np.ndarray
    pedal: list = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.pitch)

    def window(self, t0: float, t1: float) -> "Piece":
        """Notes struck in [t0, t1), moved to start at 0 (ends cut at t1 + 2 s)."""
        k = (self.start >= t0) & (self.start < t1)
        ped = [(max(a, t0) - t0, min(b, t1 + 2.0) - t0) for a, b in self.pedal if b > t0 and a < t1]
        return Piece(self.pid + f"@{t0:.0f}", self.source, self.split, self.pitch[k],
                     self.start[k] - t0, np.minimum(self.end[k], t1 + 2.0) - t0, self.vel[k],
                     self.label[k], self.weight[k], ped)

    def to_json(self) -> dict:
        return dict(pid=self.pid, source=self.source, split=self.split,
                    pitch=self.pitch.tolist(), start=np.round(self.start, 4).tolist(),
                    end=np.round(self.end, 4).tolist(), vel=self.vel.tolist(),
                    label=self.label.tolist(), weight=self.weight.tolist(), pedal=self.pedal)

    @staticmethod
    def from_json(d: dict) -> "Piece":
        return Piece(d["pid"], d["source"], d["split"], np.array(d["pitch"], int),
                     np.array(d["start"], float), np.array(d["end"], float),
                     np.array(d["vel"], float), np.array(d["label"], int),
                     np.array(d["weight"], float), [tuple(x) for x in d["pedal"]])


def split_of(key: str, val: float = 0.15, test: float = 0.15) -> str:
    """Deterministic split by piece name."""
    h = zlib.crc32(key.encode()) % 1000 / 1000
    return "test" if h < test else "val" if h < test + val else "train"


def _sorted(p: Piece) -> Piece:
    o = np.lexsort((-p.pitch, p.start))
    return Piece(p.pid, p.source, p.split, p.pitch[o], p.start[o], p.end[o], p.vel[o],
                 p.label[o], p.weight[o], p.pedal)


def pedal_intervals(control_changes) -> list[tuple[float, float]]:
    out, down = [], None
    for cc in sorted((c for c in control_changes if c.number == 64), key=lambda c: c.time):
        if cc.value >= PEDAL_ON and down is None:
            down = cc.time
        elif cc.value < PEDAL_ON and down is not None:
            out.append((down, cc.time))
            down = None
    if down is not None:
        out.append((down, down + 4.0))
    return out


def pedal_extend(p: Piece) -> np.ndarray:
    """Note ends as a piano sounds them: held to the pedal's release if the key
    was let go while the pedal was down, but cut where the same key is struck
    again."""
    end = p.end.copy()
    if p.pedal:
        downs = np.array([a for a, _ in p.pedal])
        ups = np.array([b for _, b in p.pedal])
        k = np.searchsorted(downs, end, side="right") - 1
        held = (k >= 0) & (end < ups[np.maximum(k, 0)])
        end = np.where(held, np.maximum(end, ups[np.maximum(k, 0)]), end)
    nxt: dict[int, float] = {}
    for i in np.argsort(-p.start, kind="stable"):
        q = int(p.pitch[i])
        if q in nxt:
            end[i] = min(end[i], max(nxt[q] - 0.01, p.end[i]))
        nxt[q] = p.start[i]
    return end


# ---------------------------------------------------------------- POP909

def pop909_pieces(rng: np.random.Generator, limit: int | None = None) -> list[Piece]:
    import pretty_midi
    out = []
    dirs = sorted(d for d in POP909.iterdir() if d.is_dir())
    for d in dirs[:limit]:
        f = d / f"{d.name}.mid"
        if not f.exists():
            continue
        try:
            pm = pretty_midi.PrettyMIDI(str(f))
        except Exception:
            continue
        tracks = {i.name.upper(): i for i in pm.instruments}
        if "MELODY" not in tracks:
            continue
        rows, pedal = [], []
        acc_v = [n.velocity for name, i in tracks.items() if name != "MELODY" for n in i.notes]
        mel_v = [n.velocity for n in tracks["MELODY"].notes]
        if not acc_v or not mel_v:
            continue
        gap = rng.uniform(*VOICING_GAP)
        shift_mel = np.median(acc_v) + gap - np.median(mel_v)
        shift_bridge = 0.0
        if "BRIDGE" in tracks and tracks["BRIDGE"].notes:
            shift_bridge = (np.median(acc_v) + rng.uniform(-8, 15)
                            - np.median([n.velocity for n in tracks["BRIDGE"].notes]))
        for name, inst in tracks.items():
            pedal += pedal_intervals(inst.control_changes)
            lab, w, sh = (1, 1.0, shift_mel) if name == "MELODY" else \
                         (0, 0.0, shift_bridge) if name == "BRIDGE" else (0, 1.0, 0.0)
            for n in inst.notes:
                if n.end > n.start:
                    v = float(np.clip(n.velocity + sh + rng.normal(0, 4), 8, 127))
                    rows.append((n.pitch, n.start, n.end, v, lab, w))
        a = np.array(rows, dtype=float)
        sid = f"pop909/{d.name}"
        out.append(_sorted(Piece(sid, "pop909", split_of(sid), a[:, 0].astype(int), a[:, 1], a[:, 2],
                                 a[:, 3], a[:, 4].astype(int), a[:, 5], sorted(pedal))))
    return out


# ---------------------------------------------------------------- ASAP

def asap_index() -> list[dict]:
    """One row per performance, with the piece key (composer + title)."""
    rows = list(csv.DictReader(open(ASAP / "metadata.csv", encoding="utf-8")))
    for r in rows:
        r["key"] = f"asap/{r['composer']}/{r['title']}"
    return rows


def judged(composer: str, title: str) -> bool:
    t = title.replace("_", " ").lower()
    return any(c.lower() == composer.lower() and j.lower() in t for c, j in JUDGE_TITLES)


def score_top_line(notes) -> set[int]:
    """Indexes of the right-hand staff's top line (bench/make_refs.py's rule):
    at each onset the highest new note, unless a note at least as high still
    sounds (an inner voice under a held tune note)."""
    order = sorted(range(len(notes)), key=lambda i: (round(notes[i].start, 4), -notes[i].pitch))
    top, held = set(), []
    i = 0
    while i < len(order):
        t = round(notes[order[i]].start, 4)
        j = i
        while j < len(order) and round(notes[order[j]].start, 4) == t:
            j += 1
        h = order[i]
        held = [k for k in held if notes[k].end > t + 1e-3]
        if not any(notes[k].pitch >= notes[h].pitch for k in held):
            top.add(h)
        held += order[i:j]
        i = j
    return top


def asap_piece(row: dict, ann: dict) -> Piece | None:
    import pretty_midi
    a = ann.get(row["midi_performance"])
    if not a or not a.get("score_and_performance_aligned", False):
        return None
    sb = np.array(a["midi_score_beats"], float)
    pb = np.array(a["performance_beats"], float)
    if len(sb) < 8 or len(sb) != len(pb):
        return None
    try:
        score = pretty_midi.PrettyMIDI(str(ASAP / row["midi_score"]))
        perf = pretty_midi.PrettyMIDI(str(ASAP / row["midi_performance"]))
    except Exception:
        return None
    staves = [i for i in score.instruments if i.notes]
    if len(staves) < 2:
        return None
    rh = max(staves, key=lambda i: np.mean([n.pitch for n in i.notes]))
    top = score_top_line(rh.notes)
    snotes = [(n.pitch, float(np.interp(n.start, sb, pb)), i in top and inst is rh)
              for inst in staves for i, n in enumerate(inst.notes)]
    pnotes = [n for i in perf.instruments for n in i.notes if n.end > n.start]
    # one-to-one, nearest first, same pitch, within ALIGN_TOL
    by_pitch: dict[int, list[int]] = {}
    for k, (p, t, _) in enumerate(snotes):
        by_pitch.setdefault(p, []).append(k)
    cand = []
    for j, n in enumerate(pnotes):
        for k in by_pitch.get(n.pitch, []):
            dt = abs(snotes[k][1] - n.start)
            if dt <= ALIGN_TOL:
                cand.append((dt, j, k))
    cand.sort()
    used_s, lab, w = set(), np.zeros(len(pnotes), int), np.zeros(len(pnotes))
    done = set()
    for dt, j, k in cand:
        if j in done or k in used_s:
            continue
        done.add(j)
        used_s.add(k)
        lab[j] = int(snotes[k][2])
        w[j] = 1.0
    pedal = pedal_intervals([c for i in perf.instruments for c in i.control_changes])
    sid = row["key"]
    return _sorted(Piece(sid + "/" + Path(row["midi_performance"]).stem, "asap", split_of(sid),
                         np.array([n.pitch for n in pnotes]), np.array([n.start for n in pnotes]),
                         np.array([n.end for n in pnotes]), np.array([n.velocity for n in pnotes], float),
                         lab, w, pedal))


def asap_pieces(per_piece: dict[str, int]) -> list[Piece]:
    """Up to per_piece[split] performances of each piece (unique composer + title)."""
    ann = json.load(open(ASAP / "asap_annotations.json", encoding="utf-8"))
    taken: dict[str, int] = {}
    out = []
    for row in asap_index():
        if judged(row["composer"], row["title"]):
            continue
        sp = split_of(row["key"])
        if taken.get(row["key"], 0) >= per_piece.get(sp, 1):
            continue
        p = asap_piece(row, ann)
        if p is None or p.weight.sum() < 50:
            continue
        taken[row["key"]] = taken.get(row["key"], 0) + 1
        out.append(p)
    return out


# ---------------------------------------------------------------- render + transcribe

def _room(audio: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A little random reverb and noise, so the transcriber hears a room, not a soundfont."""
    from scipy.signal import fftconvolve
    rt60 = rng.uniform(0.3, 1.6)
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    ir = rng.normal(0, 1, n) * np.exp(-6.9 * t / rt60)
    ir[: int(0.005 * SR)] = 0
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    wet = fftconvolve(audio, ir)[: len(audio)]
    mix = rng.uniform(0.05, 0.35)
    y = (1 - mix) * audio + mix * wet * (np.std(audio) / (np.std(wet) + 1e-9))
    y = y + rng.normal(0, 1, len(y)) * np.std(y) * 10 ** (rng.uniform(-60, -40) / 20)
    return y.astype(np.float32)


LOUDNESS_RANGE_DB = 30.0   # velocity 20 -> 115 spans this many dB when rendered
VEL_NOISE = 6.0            # sd of the transcriber's velocity on a real piano (see heard_velocities)


def render_velocity(v: float) -> int:
    """The soundfont's velocity for a note played at v. tinysoundfont turns
    velocity into gain over a narrow range (about 12 dB from 30 to 127) and has
    no change of timbre, so a soft accompaniment would sound nearly as loud as
    the tune; this spreads velocity 20-115 over LOUDNESS_RANGE_DB, as a piano's
    pp to ff."""
    db = -LOUDNESS_RANGE_DB * (1 - (np.clip(v, 20, 115) - 20) / 95)
    return int(np.clip(round(127 * 10 ** (db / 20)), 1, 127))


def render_and_transcribe(p: Piece, wav: Path, rng: np.random.Generator) -> list:
    """Our piano transcription of piece p played by the soundfont piano."""
    import pretty_midi
    from sheetswap import transcribe
    from sheetswap.synth import synthesize, write_wav
    end = pedal_extend(p)
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(0)
    for q, s, e, v in zip(p.pitch, p.start, end, p.vel):
        inst.notes.append(pretty_midi.Note(render_velocity(v), int(q), float(s), float(e)))
    pm.instruments.append(inst)
    audio = synthesize(pm, sr=SR).mean(axis=1)
    write_wav(_room(audio, rng), wav, sr=SR)
    return transcribe.piano(wav)


def heard_velocities(tp: Piece, src: Piece, rng: np.random.Generator) -> Piece:
    """tp (a transcribed excerpt) with velocities as the transcriber reports
    them on a real piano. On the soundfont it hears velocity squeezed into a
    narrow band (it judges loudness partly by timbre, which the soundfont
    doesn't change); trained on Disklavier recordings, on a real piano it
    reports about the played velocity (real recordings here spread 40-95, as
    performances do). So: notes matched to a source note get its velocity plus
    VEL_NOISE; ghost notes are mapped back through the squeeze measured on the
    matched notes."""
    from sheetswap.model import Note
    trans = [Note(int(q), s, e, int(v)) for q, s, e, v in zip(tp.pitch, tp.start, tp.end, tp.vel)]
    _, _, k = match(trans, src)
    vel = tp.vel.astype(float).copy()
    hit = k >= 0
    if hit.sum() >= 10:
        a, b = np.polyfit(src.vel[k[hit]], tp.vel[hit], 1)
        if a > 0.05:
            vel[~hit] = (tp.vel[~hit] - b) / a
    vel[hit] = src.vel[k[hit]] + rng.normal(0, VEL_NOISE, int(hit.sum()))
    return Piece(tp.pid, tp.source, tp.split, tp.pitch, tp.start, tp.end, np.clip(np.round(vel), 1, 127),
                 tp.label, tp.weight, tp.pedal)


def match(trans, p: Piece) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each transcribed note: (label, weight, index of the source note or -1).
    Same pitch, onset within MATCH_TOL, one to one, nearest first. Notes the
    model invented are "not tune" (weight 1)."""
    by_pitch: dict[int, list[int]] = {}
    for k, q in enumerate(p.pitch):
        by_pitch.setdefault(int(q), []).append(k)
    cand = []
    for j, n in enumerate(trans):
        for k in by_pitch.get(n.pitch, []):
            dt = abs(p.start[k] - n.start)
            if dt <= MATCH_TOL:
                cand.append((dt, j, k))
    cand.sort()
    src = np.full(len(trans), -1)
    used = set()
    for _, j, k in cand:
        if src[j] >= 0 or k in used:
            continue
        src[j] = k
        used.add(k)
    lab = np.where(src >= 0, p.label[np.maximum(src, 0)], 0)
    w = np.where(src >= 0, p.weight[np.maximum(src, 0)], 1.0)
    return lab.astype(int), w, src


def transcribed_piece(trans, p: Piece) -> Piece:
    lab, w, _ = match(trans, p)
    a = np.array([(n.pitch, n.start, n.end, n.velocity) for n in trans], float).reshape(-1, 4)
    return _sorted(Piece(p.pid, p.source, p.split, a[:, 0].astype(int), a[:, 1], a[:, 2], a[:, 3],
                         lab, w, p.pedal))


# ---------------------------------------------------------------- error model

class ErrorModel:
    """What our transcriber does to a piece, measured on rendered excerpts and
    replayed on others: misses (by velocity), onset jitter, velocity as heard,
    note ends (against the pedal-held end), and ghost notes (pitch, time,
    loudness and length relative to the loudest note sounding near them)."""

    VBINS = np.array([0, 30, 45, 60, 75, 90, 128])

    def __init__(self, d: dict):
        self.d = d

    @staticmethod
    def measure(pairs: list[tuple[Piece, list]]) -> "ErrorModel":
        miss = np.zeros(len(ErrorModel.VBINS) - 1)
        tot = np.zeros(len(ErrorModel.VBINS) - 1)
        jit, vel_src, vel_out, end_ratio, ghosts, n_src = [], [], [], [], [], 0
        for p, trans in pairs:
            _, _, src = match(trans, p)
            ext = pedal_extend(p)
            hit = np.zeros(len(p), bool)
            hit[src[src >= 0]] = True
            b = np.clip(np.digitize(p.vel, ErrorModel.VBINS) - 1, 0, len(miss) - 1)
            np.add.at(tot, b, 1)
            np.add.at(miss, b, ~hit)
            n_src += len(p)
            for j, n in enumerate(trans):
                k = src[j]
                if k >= 0:
                    jit.append(n.start - p.start[k])
                    vel_src.append(p.vel[k])
                    vel_out.append(n.velocity)
                    end_ratio.append((n.end - n.start) / max(ext[k] - p.start[k], 0.02))
                    continue
                # a ghost: relative to the loudest source note sounding within 60 ms of it
                near = np.flatnonzero((p.start <= n.start + 0.06) & (ext >= n.start - 0.06))
                if len(near) == 0:
                    ghosts.append((None, 0.0, n.velocity, n.end - n.start))
                    continue
                k = near[np.argmax(p.vel[near])]
                ghosts.append((int(n.pitch - p.pitch[k]), float(n.start - p.start[k]),
                               float(n.velocity - p.vel[k]), float(n.end - n.start)))
        vs, vo = np.array(vel_src), np.array(vel_out)
        fit = np.polyfit(vs, vo, 1) if len(vs) > 10 else np.array([1.0, 0.0])
        return ErrorModel(dict(
            miss_rate=(miss / np.maximum(tot, 1)).tolist(), vbins=ErrorModel.VBINS.tolist(),
            jitter=np.round(jit, 4).tolist(), vel_fit=fit.tolist(),
            vel_resid=np.round(vo - np.polyval(fit, vs), 2).tolist(),
            end_ratio=np.round(end_ratio, 3).tolist(),
            ghost_rate=len(ghosts) / max(n_src, 1),
            ghosts=[g for g in ghosts if g[0] is not None]))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.d))

    @staticmethod
    def load(path: Path) -> "ErrorModel":
        return ErrorModel(json.loads(path.read_text()))

    def summary(self) -> dict:
        d = self.d
        j = np.abs(np.array(d["jitter"]))
        return dict(miss_rate_by_velocity=dict(zip([f"{a}-{b}" for a, b in zip(d["vbins"], d["vbins"][1:])],
                                                   np.round(d["miss_rate"], 3).tolist())),
                    onset_jitter_ms_median=round(1000 * float(np.median(j)), 1),
                    onset_jitter_ms_p90=round(1000 * float(np.percentile(j, 90)), 1),
                    velocity_fit=np.round(d["vel_fit"], 3).tolist(),
                    end_ratio_median=round(float(np.median(d["end_ratio"])), 3),
                    ghosts_per_source_note=round(d["ghost_rate"], 3),
                    ghost_intervals=dict(sorted(
                        {int(k): int(v) for k, v in zip(*np.unique([g[0] for g in d["ghosts"]],
                                                                   return_counts=True)) if v >= 3}.items(),
                        key=lambda kv: -kv[1])[:8]))

    def corrupt(self, p: Piece, rng: np.random.Generator) -> Piece:
        """Piece p as our transcriber would likely hear it."""
        d = self.d
        ext = pedal_extend(p)
        b = np.clip(np.digitize(p.vel, np.array(d["vbins"])) - 1, 0, len(d["miss_rate"]) - 1)
        keep = rng.random(len(p)) >= np.array(d["miss_rate"])[b]
        jit = np.array(d["jitter"])
        start = p.start + rng.choice(jit, len(p))
        vel = np.polyval(d["vel_fit"], p.vel) + rng.choice(np.array(d["vel_resid"]), len(p))
        ratio = rng.choice(np.array(d["end_ratio"]), len(p))
        end = start + np.maximum((ext - p.start) * ratio, 0.03)
        rows = [(p.pitch[i], start[i], end[i], vel[i], p.label[i], p.weight[i]) for i in np.flatnonzero(keep)]
        ghosts = d["ghosts"]
        n_ghost = rng.poisson(d["ghost_rate"] * len(p))
        if ghosts and n_ghost:
            # ghosts hang off loud notes: pick parents by velocity
            pw = np.maximum(p.vel, 1.0) ** 2
            parents = rng.choice(len(p), n_ghost, p=pw / pw.sum())
            for k, g in zip(parents, rng.integers(0, len(ghosts), n_ghost)):
                dp, dt, dv, du = ghosts[g]
                q = int(p.pitch[k] + dp)
                if 21 <= q <= 108:
                    rows.append((q, p.start[k] + dt, p.start[k] + dt + max(du, 0.03),
                                 p.vel[k] + dv, 0, 1.0))
        a = np.array(rows, float).reshape(-1, 6)
        a[:, 3] = np.clip(a[:, 3], 1, 127)
        return _sorted(Piece(p.pid, p.source, p.split, a[:, 0].astype(int), a[:, 1], a[:, 2],
                             np.round(a[:, 3]), a[:, 4].astype(int), a[:, 5], p.pedal))


def cache_key(*parts) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:10]
