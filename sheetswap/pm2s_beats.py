"""Beats, bars and note values read from a piano performance's notes, by PM2S.

PM2S ("Performance MIDI-to-Score Conversion by Neural Beat Tracking", Liu, Kong,
Morfi and Benetos, ISMIR 2022; github.com/cheriell/PM2S, MIT) reads a list of
played notes (pitch, onset, duration, velocity), not audio. Trained on ASAP
(classical piano performances aligned to their scores), it gives per note:
whether a beat and a downbeat fall on it, which hand plays it, the key, and the
note's place inside its beat and its written length (in 24ths of a beat); per
piece a time signature (a 4-based or 3-based bar). The beats are completed
between notes by PM2S's own dynamic programming (a beat every 1/2..1/4 of a
long gap, steady tempo).

Here it reads the piano transcription of a recording (Analysis.sources["piano"])
as the performance. `read` runs the models (CPU; the Apple GPU path crashes on
macOS 14) and `grid` turns the beats and downbeats into a BeatGrid (tracker
"pm2s"). The checkpoints live in models/pm2s/ (scripts/install_pm2s.sh).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from importlib.util import find_spec

import numpy as np

from .config import MODELS_DIR
from .model import QUARTER_BEAT, BeatGrid, Note

MODEL_DIR = MODELS_DIR / "pm2s"
FILES = {"beat": "RNNJointBeatModel.pth", "quantisation": "RNNJointQuantisationModel.pth",
         "hand_part": "RNNHandPartModel.pth", "key_signature": "RNNKeySignatureModel.pth",
         "time_signature": "CNNTimeSignatureModel.pth"}
PER_BEAT = 24            # PM2S places onsets and lengths in 24ths of a beat
MIN_NOTES = 16           # fewer notes than this: nothing to read


def available() -> bool:
    """PM2S is installed and its checkpoints are in models/pm2s/."""
    return find_spec("pm2s") is not None and all((MODEL_DIR / f).is_file() for f in FILES.values())


def _torch():
    """torch, imported safely: its first import scans every loaded module's source
    file, which trips over librosa's lazily loaded resampy (broken without
    pkg_resources), so that one is stubbed first as transcribe does for basic_pitch."""
    from .transcribe import _stub_resampy_if_broken
    _stub_resampy_if_broken()
    import torch
    return torch


@lru_cache(maxsize=None)
def _model(kind: str):
    torch = _torch()
    if kind == "beat":
        from pm2s.models.beat import RNNJointBeatModel as cls
    elif kind == "quantisation":
        from pm2s.models.quantisation import RNNJointQuantisationModel

        def cls():
            return RNNJointQuantisationModel(beat_model_state_dict=str(MODEL_DIR / FILES["beat"]))
    elif kind == "hand_part":
        from pm2s.models.hand_part import RNNHandPartModel as cls
    elif kind == "key_signature":
        from pm2s.models.key_signature import RNNKeySignatureModel as cls
    else:
        from pm2s.models.time_signature import CNNTimeSignatureModel as cls
    model = cls()
    model.load_state_dict(torch.load(MODEL_DIR / FILES[kind], map_location="cpu"))
    model.eval()
    return model


@dataclass
class Reading:
    """What PM2S makes of a performance. Per-note lists follow `order` (the notes
    sorted by onset, then pitch): order[k] is the index into the notes passed in."""
    beats: list[float]                  # seconds
    downbeats: list[float]              # seconds, a subset of beats
    beats_per_bar: int
    first_downbeat: int                 # index into beats
    time_signature: str                 # "4/4", "3/4", "2/4" or "6/8" (PM2S's choices)
    key: str = ""                       # the key signature as a major key's name ("Eb")
    order: list[int] = field(default_factory=list)
    hands: list[int] = field(default_factory=list)       # 0 or 1 per note (PM2S's hand classes)
    position: list[float] = field(default_factory=list)  # onset in beats from beats[0] (quantised)
    value: list[float] = field(default_factory=list)     # written length in beats


def _sequence(notes: list[Note]) -> tuple[np.ndarray, list[int]]:
    order = sorted(range(len(notes)), key=lambda i: (notes[i].start, notes[i].pitch))
    seq = np.array([[notes[i].pitch, notes[i].start, max(notes[i].end - notes[i].start, 0.0),
                     notes[i].velocity] for i in order], dtype=np.float64)
    return seq, order


def read(notes: list[Note], quantise: bool = True, extras: bool = True) -> Reading | None:
    """PM2S on `notes` (seconds); None when there are too few notes or PM2S is missing.
    `quantise`: also each note's place in its beat and written length; `extras`:
    hands and key."""
    if len(notes) < MIN_NOTES or not available():
        return None
    torch = _torch()
    from pm2s.constants import keyNumber2Name
    from pm2s.features.beat import RNNJointBeatProcessor

    seq, order = _sequence(notes)
    x = torch.tensor(seq).unsqueeze(0)
    onsets = seq[:, 1]
    with torch.no_grad():
        beat_p, down_p, _ = _model("beat")(x)
        ts_p = _model("time_signature")(x)
    beat_p, down_p = beat_p[0].numpy(), down_p[0].numpy()
    beats, downbeats = RNNJointBeatProcessor.pps(beat_p, down_p, onsets)
    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)
    if len(beats) < 4:
        return None

    # time signature as PM2S's own converter settles it (pm2s.pm2s.CRNNJointPM2S)
    three = np.bincount((ts_p[0].numpy() > 0.5).astype(int)).argmax() == 1
    per_bar = len(beats) / max(len(downbeats), 1)
    if three:
        ts, bpb = ("6/8", 6) if per_bar > 4.5 else ("3/4", 3)
    else:
        ts, bpb = ("2/4", 2) if per_bar < 3 else ("4/4", 4)
    # the bar phase that puts the most predicted downbeats on bar lines
    near = [np.min(np.abs(downbeats - b)) < 1e-6 if len(downbeats) else False for b in beats]
    phase = max(range(bpb), key=lambda k: (sum(near[k::bpb]), -k))
    out = Reading(beats=[float(b) for b in beats], downbeats=[float(b) for b in beats[phase::bpb]],
                  beats_per_bar=bpb, first_downbeat=phase, time_signature=ts, order=order)
    if extras:
        with torch.no_grad():
            hands = _model("hand_part")(x)[0].numpy()
            key = _model("key_signature")(x)[0].argmax(dim=0).numpy()
        out.hands = [int(h > 0.5) for h in hands]
        out.key = keyNumber2Name[int(np.bincount(key).argmax())]
    if quantise:
        with torch.no_grad():
            _, _, on_p, val_p = _model("quantisation")(x)
        pos = on_p[0].argmax(dim=0).numpy() / PER_BEAT
        val = val_p[0].argmax(dim=0).numpy() / PER_BEAT
        # the beat each onset belongs to: the last beat at or before it, or the next one
        # when it was played just early (within PM2S's tolerance) and placed early in a beat
        from pm2s.constants import tolerance
        idx = np.maximum(np.searchsorted(beats, onsets, side="right") - 1, 0)
        nxt = np.minimum(idx + 1, len(beats) - 1)
        early = (nxt > idx) & (beats[nxt] - onsets <= tolerance) & (pos < 0.5)
        idx = np.where(early, nxt, idx)
        out.position = [float(i + p) for i, p in zip(idx, pos)]
        out.value = [float(v) for v in val]
    return out


def grid(reading: Reading) -> BeatGrid:
    """PM2S's beats and bars as a BeatGrid (quarter beats; 6/8 as six 8th beats a bar
    goes to the pipeline as it is, and beats.beat_unit / the Time option regroup it)."""
    return BeatGrid(list(reading.beats), reading.beats_per_bar, reading.first_downbeat,
                    QUARTER_BEAT, tracker="pm2s")


def _seconds(reading: Reading, beats: float) -> float:
    """A position in PM2S beats (from beats[0]) in seconds, on its own beats."""
    b = reading.beats
    i = int(np.floor(beats))
    if i < 0:
        return b[0] + beats * (b[1] - b[0])
    if i >= len(b) - 1:
        return b[-1] + (beats - (len(b) - 1)) * (b[-1] - b[-2])
    return b[i] + (beats - i) * (b[i + 1] - b[i])


def quantize_tune(melody: list[Note], notes: list[Note], reading: Reading, grid: BeatGrid,
                  simple: bool = False):
    """The tune's notes on `grid` where PM2S places them: each melody note that is one
    of the performance's `notes` (the ones `reading` was made from) starts at PM2S's
    quantised onset and lasts its written length; other melody notes keep their
    played times. Then as rhythm.quantize_line writes a line: each beat on 16ths or
    8th triplets (whichever its onsets sit on), one note per onset (the louder),
    short gaps held through, nothing shorter than a 16th (an 8th when simple)."""
    from .model import QNote
    from .rhythm import EIGHTH, SIXTEENTH, TRIPLET, snap

    if not melody:
        return []
    unit = grid.beat_ticks
    min_dur = EIGHTH if simple else SIXTEENTH
    where = {}
    for k, i in enumerate(reading.order):
        n = notes[i]
        where.setdefault((n.pitch, round(n.start, 4)), k)
    raw = []
    for n in melody:
        k = where.get((n.pitch, round(n.start, 4)))
        if k is not None and reading.position:
            pos, val = reading.position[k], reading.value[k]
            s = grid.to_ticks(_seconds(reading, pos))
            e = grid.to_ticks(_seconds(reading, pos + max(val, 1.0 / PER_BEAT)))
        else:
            s, e = grid.to_ticks(n.start), grid.to_ticks(max(n.end, n.start))
        raw.append((s, e, n))
    raw.sort(key=lambda x: (x[0], -x[2].velocity))
    # each beat on one grid: 16ths or 8th triplets, whichever its onsets sit on
    by_beat: dict[int, list[float]] = {}
    for s, _, _ in raw:
        by_beat.setdefault(int(np.floor(s / unit)), []).append(s)
    steps = {}
    for b, xs in by_beat.items():
        if simple or unit != QUARTER_BEAT:
            steps[b] = EIGHTH if simple else SIXTEENTH
            continue
        err16 = sum(abs(x - snap(x, SIXTEENTH)) for x in xs)
        err3 = sum(abs(x - snap(x, TRIPLET)) for x in xs)
        steps[b] = TRIPLET if len(xs) >= 2 and err3 < 0.5 * err16 else SIXTEENTH

    def step(t: float) -> int:
        return steps.get(int(np.floor(t / unit)), EIGHTH if simple else SIXTEENTH)

    kept: dict[int, tuple[float, Note]] = {}
    for s, e, n in raw:
        st = snap(s, step(s))
        if st not in kept or n.velocity > kept[st][1].velocity:
            kept[st] = (e, n)
    starts = sorted(kept)
    out = []
    for i, st in enumerate(starts):
        e, n = kept[st]
        nxt = starts[i + 1] if i + 1 < len(starts) else None
        if nxt is not None and nxt - e < max(min_dur, unit):
            en = nxt                       # short gap (or overlap): hold to the next note
        else:
            en = max(snap(e, step(e)), st + min_dur)
            if nxt is not None and nxt - en < min_dur:
                en = nxt
        out.append(QNote(n.pitch, st, en - st, n.velocity))
    return out
