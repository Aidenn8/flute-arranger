"""Find the beats and the meter (3 or 4 beats per bar) of a recording or MIDI file.

The grid always covers the whole file: the first beat sits within one beat of
0 s and the last one at or after the end. Music without a clear beat (rubato
piano, long silence) gets a steady grid at the estimated tempo instead.

How long a beat is written (a quarter, a dotted quarter as in 6/8, or an
eighth as in 3/8) comes from where the tune's notes fall inside the beats:
see beat_subdivision and beat_unit. A MIDI file's own time signature is used
as it is.
"""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from .model import DOTTED_BEAT, EIGHTH_BEAT, QUARTER_BEAT, BeatGrid

SR = 22050
HOP = 512                 # 23 ms frames; beat times are smoothed afterwards
MIN_BPM, MAX_BPM = 60.0, 180.0
DEFAULT_BPM = 120.0
THREE_MARGIN = 1.1        # 3/4 must beat 4/4 by 10% (ties go to 4/4)
SILENCE = 0.01            # RMS below 1% of the peak (-40 dB) counts as silence
DRUMS_MIN = 0.1           # drums stem quieter than this share of the mix RMS is ignored
DOWNBEAT_MARGIN = 1.1     # the tune's long notes must favour one beat of the bar by 10%
NEURAL_DOWNBEAT_MARGIN = 1.5  # ... by 50% to overrule the Beat This! bar lines, which are usually right
SPARSE_BASS = 0.6         # a bass hitting fewer than this share of beats helps find beat 1
NOTE_BLEND = 0.7          # weight of the notes' onset curve vs the audio's, for beat tracking
BASS_WEIGHT = 1.5         # extra weight of low notes in the curve used to find the downbeats

# Beat subdivision (see beat_subdivision / beat_unit). Each tune onset off the
# beat is matched to the nearest of 1/4, 1/3, 1/2, 2/3, 3/4 of a beat.
PHASE_TOL = 0.07          # beats: farther than this from all of them, an onset is ignored
ALIGN_WINDOW = 0.2        # beats: onsets this close to a beat show how far the grid lags the tune
COMPOUND_MIN_BEATS = 6    # beats split in three needed to call the music compound (6/8)...
COMPOUND_SHARE = 0.6      # ...and their share of all the beats that split, in two or three
MELODY_MIN_SHARE = 0.4    # ...unless the tune alone splits fewer than this share in three
                          # (Ave Maria: a tune in 8ths over triplets stays 4/4; Chopin's
                          # Op. 9/2 tune alone is 0.57, its accompaniment makes it 12/8)
EIGHTH_BEAT_BPM = 130.0   # beats this fast or faster, 3 to a bar, split in two: 8th beats (3/8;
                          # a neural tracker puts Für Elise at 150)...
EIGHTH_MIN_SHARE = 0.25   # ...when at least this share of the beats with notes split in two
_THIRDS = (1 / 3, 2 / 3)
_QUARTERS = (1 / 4, 3 / 4)
_HALF = 1 / 2


def notes_envelope(notes, n_frames: int, bass_weight: float = 0.0) -> np.ndarray:
    """An onset-strength curve built from transcribed notes (one spike per onset).

    Each onset adds its loudness (and, with bass_weight, more for low notes,
    which mark downbeats and chord changes), spread over a couple of frames.
    Much cleaner than the audio's own onset curve for piano with pedal.
    """
    env = np.zeros(n_frames)
    for n in notes:
        f = int(round(n.start * SR / HOP))
        if 0 <= f < n_frames:
            w = (n.velocity / 127) * (1 + bass_weight * max(0.0, (72 - n.pitch) / 24))
            if bass_weight:
                w *= min(max(n.end - n.start, 0.05), 2.0)   # long notes start on strong beats
            env[f] += w
    kernel = np.array([0.25, 1.0, 0.5, 0.25])
    return np.convolve(env, kernel, mode="full")[:n_frames]


def _norm(x: np.ndarray) -> np.ndarray:
    peak = float(np.percentile(x, 99)) if x.size else 0.0
    return x / peak if peak > 0 else x


def track_beats(wav: Path, drums_wav: Path | None = None, notes=None) -> BeatGrid:
    """Beat grid of an audio file.

    Tracks on the drums stem when it has real drums; otherwise, when the
    transcribed notes are given, on their onsets blended with the audio's.
    """
    import librosa

    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    duration = len(y) / SR
    if len(y) < 4 * HOP or not np.any(y):
        return _finish(_steady(0.0, DEFAULT_BPM), duration, None, 0.0, duration)

    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    track_env = env
    drums = False
    if drums_wav is not None:
        d, _ = librosa.load(str(drums_wav), sr=SR, mono=True)
        if len(d) >= 4 * HOP and _rms(d) >= DRUMS_MIN * _rms(y):
            track_env = librosa.onset.onset_strength(y=d, sr=SR, hop_length=HOP)
            drums = True
    if notes and not drums:
        track_env = NOTE_BLEND * _norm(notes_envelope(notes, len(env))) + (1 - NOTE_BLEND) * _norm(env)
        env = _norm(notes_envelope(notes, len(env), bass_weight=BASS_WEIGHT)) + 0.25 * _norm(env)

    start, end = _music_span(y)
    tempo, frames = librosa.beat.beat_track(onset_envelope=track_env, sr=SR, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    beats = _fill_gaps(librosa.frames_to_time(frames, sr=SR, hop_length=HOP))
    if len(beats) >= 4:
        beats = _smooth(_fold_tempo(beats, track_env))
    if len(beats) < 4 or not MIN_BPM <= 60.0 / float(np.median(np.diff(beats))) <= MAX_BPM:
        beats = _steady(start, tempo)
    return _finish(beats, duration, env, start, end)


def melody_downbeat(melody, grid: BeatGrid, music_start: float, bass=None) -> BeatGrid:
    """Place the bar lines where the tune leans: its long notes start on beat 1.

    A melody lengthens its notes on strong beats (agogic accent), which marks
    the bar far better than loudness or harmony when every beat has a chord:
    on the test music this finds beat 1 in 13 of 19 pieces, against 4-6 for
    onset loudness. Each melody note scores velocity x length (capped at two
    beats) on its nearest beat; the phase with the highest mean wins. The first
    downbeat is then the first beat of that phase at or after the music start.
    Returns the grid unchanged when there's too little melody to judge.
    """
    b = np.asarray(grid.beats, dtype=float)
    bpb = grid.beats_per_bar
    if bpb < 2 or len(b) < 2 * bpb or len(melody) < 4 * bpb:
        return grid
    period = float(np.median(np.diff(b)))
    weight = np.zeros(len(b))
    for x in melody:
        k = int(np.argmin(np.abs(b - x.start)))
        if abs(b[k] - x.start) < 0.25 * period:
            weight[k] += (x.velocity / 127) * min(x.end - x.start, 2 * period) / period
    # a bass that plays about once a bar (oom-pah, a held root) marks beat 1;
    # a bass that moves every beat (a chorale, a walking line) says nothing
    hits = np.zeros(len(b))
    for x in bass or []:
        k = int(np.argmin(np.abs(b - x.start)))
        if abs(b[k] - x.start) < 0.25 * period:
            hits[k] = 1.0
    span = (b >= music_start - 0.25 * period)
    share = float(hits[span].mean()) if span.any() else 0.0
    if 0 < share < SPARSE_BASS:
        w = weight[span]
        if w.std() > 0:
            weight = weight / w.std()
        weight = weight + hits
    idx = np.arange(len(b))
    means = [float(weight[idx % bpb == p].mean()) for p in range(bpb)]
    best = int(np.argmax(means))
    runner_up = max(m for p, m in enumerate(means) if p != best)
    margin = NEURAL_DOWNBEAT_MARGIN if grid.tracker == "neural" else DOWNBEAT_MARGIN
    if means[best] <= 0 or means[best] < margin * runner_up:
        return grid                     # no clear evidence: keep the beat tracker's choice
    first = next((i for i in range(len(b)) if i % bpb == best and b[i] >= music_start - 0.25 * period),
                 best)
    return replace(grid, first_downbeat=first)


def _beat_splits(parts, grid: BeatGrid) -> list[tuple[int, int, int]]:
    """(beats split in three, beats split in two, beats holding an onset) for
    each of `parts` (lists of notes: the tune, the accompaniment), each judged
    on its own so a tune's 16th can't cancel the accompaniment's 8ths.

    A beat splits in three when a note starts about 1/3 or 2/3 of the way
    through it and none at 1/4 or 3/4 (16ths of a quarter beat); it splits in
    two when notes start at 1/4, 1/2 or 3/4 and none at a third. A dotted-8th
    + 16th + 8th beat of a siciliano (1/2 and 2/3) counts as three.

    A tracked grid often sits a little late or early against the notes (on
    piano recordings they come about 1/8 beat before the tracked beats, which
    would put every 8th near 1/3). So the onsets are first moved by the
    median offset of those within ALIGN_WINDOW of a beat (all parts together).
    """
    xss = [[grid.to_beats(n.start) for n in notes] for notes in parts]
    near = [x - round(x) for xs in xss for x in xs if abs(x - round(x)) <= ALIGN_WINDOW]
    lag = float(np.median(near)) if near else 0.0
    out = []
    for xs in xss:
        three = two = 0
        offs: dict[int, set[float]] = {}
        for x in xs:
            x -= lag
            k = math.floor(x + PHASE_TOL)          # a note just before a beat is on it
            f = x - k
            where = offs.setdefault(k, set())
            if abs(f) <= PHASE_TOL:
                continue
            p = min((*_THIRDS, *_QUARTERS, _HALF), key=lambda p: abs(f - p))
            if abs(f - p) <= PHASE_TOL:
                where.add(p)
        for where in offs.values():
            thirds = any(p in where for p in _THIRDS)
            quarters = any(p in where for p in _QUARTERS)
            if thirds and not quarters:
                three += 1
            elif (quarters or _HALF in where) and not thirds:
                two += 1
        out.append((three, two, len(offs)))
    return out


def _subdivision(counts: list[tuple[int, int, int]]) -> int:
    """3 or 2 from _beat_splits counts (the tune's first)."""
    three = sum(c[0] for c in counts)
    two = sum(c[1] for c in counts)
    if three < COMPOUND_MIN_BEATS or three < COMPOUND_SHARE * (three + two):
        return 2
    t3, t2, _ = counts[0]
    if t3 + t2 >= COMPOUND_MIN_BEATS and t3 < MELODY_MIN_SHARE * (t3 + t2):
        return 2                        # the tune itself goes in twos (over triplets)
    return 3


def _parts(melody, accompaniment) -> list:
    return [list(melody)] + ([list(accompaniment)] if accompaniment else [])


def beat_subdivision(melody, grid: BeatGrid, accompaniment=None) -> int:
    """3 if the music splits its beats in three (6/8, 9/8, 12/8: jigs,
    barcarolles, a 12/8 nocturne), else 2.

    Histogram of where the onsets (Notes, seconds) of the melody, and of the
    accompaniment when given, fall inside the grid's beats: compound music
    has its peaks at 1/3 and 2/3 of the beat, simple music at 1/2 (and 1/4,
    3/4). Counted per beat, so the odd triplet in a 4/4 tune doesn't make it
    compound: at least COMPOUND_MIN_BEATS beats, and COMPOUND_SHARE of the
    beats that split at all, must split in three. The accompaniment settles
    tunes that are ambiguous alone (Chopin's Op. 9/2 melody splits 39 beats in
    three against 29 in two; its left hand's 8ths are all in threes), but a
    tune that splits mostly in two (under MELODY_MIN_SHARE in three) stays in
    simple time over a triplet accompaniment, since the flute plays the tune.
    """
    return _subdivision(_beat_splits(_parts(melody, accompaniment), grid))


def beat_unit(melody, grid: BeatGrid, accompaniment=None) -> int:
    """Ticks per beat to write the grid's beats in: DOTTED_BEAT (18) when the
    music splits them in three (beat_subdivision); EIGHTH_BEAT (6) when they
    are fast (EIGHTH_BEAT_BPM or more), three to a bar and split in two (3/8,
    as in Für Elise, tracked at 150 eighths a minute); else QUARTER_BEAT (12)."""
    counts = _beat_splits(_parts(melody, accompaniment), grid)
    if _subdivision(counts) == 3:
        return DOTTED_BEAT
    two = sum(c[1] for c in counts)
    busy = sum(c[2] for c in counts)
    if (grid.beats_per_bar == 3 and grid.bpm() >= EIGHTH_BEAT_BPM
            and two >= max(1, EIGHTH_MIN_SHARE * busy)):
        return EIGHTH_BEAT
    return QUARTER_BEAT


def _midi_meter(numerator: int, denominator: int) -> tuple[int, int]:
    """(beats per bar, ticks per beat) for a MIDI time signature.

    pretty_midi's beats are dotted quarters in 6/8, 9/8 and 12/8, eighths in
    3/8 and quarters in 2/4, 3/4 and 4/4. Other signatures are written with 3
    beats a bar if they have 3, else 4, as quarters.
    """
    if denominator == 8 and numerator % 3 == 0 and numerator > 3:
        return numerator // 3, DOTTED_BEAT
    if denominator == 8 and numerator == 3:
        return 3, EIGHTH_BEAT
    if denominator == 4 and numerator in (2, 3, 4):
        return numerator, QUARTER_BEAT
    return (3 if numerator == 3 else 4), QUARTER_BEAT


def grid_from_midi(pm) -> BeatGrid:
    """Beat grid straight from a pretty_midi.PrettyMIDI, in its time signature
    (6/8 -> 2 dotted-quarter beats a bar; no signature -> 4/4)."""
    end = pm.get_end_time()
    beats = np.asarray(pm.get_beats(), dtype=float)
    if len(beats) < 2:
        _, tempi = pm.get_tempo_changes()
        beats = _steady(0.0, float(tempi[0]) if len(tempi) else DEFAULT_BPM)
    beats = _extend(beats, end)

    sigs = sorted(pm.time_signature_changes, key=lambda s: s.time)
    bpb, unit = _midi_meter(sigs[0].numerator, sigs[0].denominator) if sigs else (4, QUARTER_BEAT)

    downs = np.asarray(pm.get_downbeats(), dtype=float)
    if len(downs) == 0:
        downs = beats[::bpb]
    starts = [n.start for inst in pm.instruments if not inst.is_drum for n in inst.notes]
    if not starts:
        starts = [n.start for inst in pm.instruments for n in inst.notes]
    first = int(np.argmin(np.abs(beats - downs[0])))
    if starts:
        # first downbeat at or after the first note; earlier notes become a pickup
        tol = 0.25 * float(np.median(np.diff(beats)))
        later = downs[downs >= min(starts) - tol]
        if len(later):
            first = int(np.argmin(np.abs(beats - later[0])))
    return BeatGrid([float(b) for b in beats], bpb, first, unit, tracker="midi")


# --- helpers ---------------------------------------------------------------

def _rms(y: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0


def _music_span(y: np.ndarray) -> tuple[float, float]:
    """First and last moment the audio is louder than silence."""
    import librosa

    rms = librosa.feature.rms(y=y, frame_length=2 * HOP, hop_length=HOP)[0]
    loud = np.flatnonzero(rms > SILENCE * rms.max()) if rms.max() > 0 else []
    if len(loud) == 0:
        return 0.0, len(y) / SR
    t = librosa.frames_to_time(np.array([loud[0], loud[-1]]), sr=SR, hop_length=HOP)
    return float(t[0]), float(t[1])


def _fold_bpm(bpm: float) -> float:
    if not math.isfinite(bpm) or bpm <= 0:
        return DEFAULT_BPM
    while bpm > MAX_BPM:
        bpm /= 2
    while bpm < MIN_BPM:
        bpm *= 2
    return bpm


def _steady(start: float, bpm: float) -> np.ndarray:
    """Two beats at a steady tempo from start; _extend fills the rest."""
    period = 60.0 / _fold_bpm(bpm)
    return np.array([start, start + period])


def _fill_gaps(beats: np.ndarray) -> np.ndarray:
    """Drop beats closer than half a beat, fill holes longer than 1.5 beats."""
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 2:
        return beats
    med = float(np.median(np.diff(beats)))
    if med <= 0:
        return beats[:1]
    out = [beats[0]]
    for b in beats[1:]:
        prev = out[-1]
        gap = b - prev
        if gap < 0.5 * med:
            continue
        if gap > 1.5 * med:
            k = int(round(gap / med))
            out.extend(prev + gap * j / k for j in range(1, k))
        out.append(b)
    return np.array(out)


def _smooth(beats: np.ndarray) -> np.ndarray:
    """Centered 5-beat average: removes the one-frame jitter, keeps slow tempo drift."""
    if len(beats) < 5:
        return beats
    out = beats.copy()
    out[2:-2] = np.convolve(beats, np.ones(5) / 5, mode="valid")
    return out


def _strength_at(env: np.ndarray, beats: np.ndarray) -> np.ndarray:
    """Onset strength at each beat (peak within +-3 frames; 0 outside the file)."""
    frames = np.round(np.asarray(beats) * SR / HOP).astype(int)
    out = np.zeros(len(frames))
    for i, f in enumerate(frames):
        if 0 <= f < len(env):
            out[i] = env[max(0, f - 3):f + 4].max()
    return out


def _fold_tempo(beats: np.ndarray, env: np.ndarray) -> np.ndarray:
    """Halve or double the beat rate until the tempo is 60-180 BPM."""
    for _ in range(4):
        bpm = 60.0 / float(np.median(np.diff(beats)))
        if bpm > MAX_BPM and len(beats) >= 4:
            s = _strength_at(env, beats)
            beats = beats[0::2] if s[0::2].mean() >= s[1::2].mean() else beats[1::2]
        elif bpm < MIN_BPM:
            beats = np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) / 2]))
        else:
            break
    return beats


def _extend(beats: np.ndarray, duration: float) -> np.ndarray:
    """Add beats at the edge intervals until they cover 0 s .. duration."""
    beats = np.asarray(beats, dtype=float)
    d0 = beats[1] - beats[0]
    n_pre = max(0, math.floor(beats[0] / d0 + 1e-9))
    pre = np.maximum(beats[0] - d0 * np.arange(n_pre, 0, -1), 0.0)
    dn = beats[-1] - beats[-2]
    n_post = max(0, math.ceil((duration - beats[-1]) / dn - 1e-9))
    post = beats[-1] + dn * np.arange(1, n_post + 1)
    return np.concatenate([pre, beats, post])


def _meter(strength: np.ndarray, lo: int, hi: int) -> tuple[int, int]:
    """(beats_per_bar, phase): the beat phase whose onsets stand out most."""
    idx = np.arange(lo, hi + 1)
    s = strength[lo:hi + 1]
    best: dict[int, tuple[float, int]] = {}
    for m in (4, 3):
        for p in range(m):
            on, off = s[idx % m == p], s[idx % m != p]
            if len(on) < 2 or len(off) < 2:
                continue
            score = float(on.mean() / (off.mean() + 1e-9))
            if score > best.get(m, (0.0, 0))[0]:
                best[m] = (score, p)
    s4 = best.get(4, (0.0, 0))
    if 3 in best and best[3][0] > THREE_MARGIN * s4[0]:
        return 3, best[3][1]
    return 4, s4[1]


def _finish(beats: np.ndarray, duration: float, env, start: float, end: float) -> BeatGrid:
    """Cover the file, pick the meter and the first downbeat at/after the music start."""
    beats = _extend(beats, duration)
    period = float(np.median(np.diff(beats)))
    lo = int(np.searchsorted(beats, start - 0.5 * period))
    hi = max(lo, int(np.searchsorted(beats, end, side="right")) - 1)
    bpb, phase = (4, 0) if env is None else _meter(_strength_at(env, beats), lo, hi)
    first = next((i for i in range(len(beats))
                  if i % bpb == phase and beats[i] >= start - 0.25 * period), phase)
    return BeatGrid([float(b) for b in beats], int(bpb), int(first))
