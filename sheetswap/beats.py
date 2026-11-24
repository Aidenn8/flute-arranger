"""Find the beats and the meter (3 or 4 beats per bar) of a recording or MIDI file.

The grid always covers the whole file: the first beat sits within one beat of
0 s and the last one at or after the end. Music without a clear beat (rubato
piano, long silence) gets a steady grid at the estimated tempo instead.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .model import BeatGrid

SR = 22050
HOP = 512                 # 23 ms frames; beat times are smoothed afterwards
MIN_BPM, MAX_BPM = 60.0, 180.0
DEFAULT_BPM = 120.0
THREE_MARGIN = 1.1        # 3/4 must beat 4/4 by 10% (ties go to 4/4)
SILENCE = 0.01            # RMS below 1% of the peak (-40 dB) counts as silence
DRUMS_MIN = 0.1           # drums stem quieter than this share of the mix RMS is ignored


def track_beats(wav: Path, drums_wav: Path | None = None) -> BeatGrid:
    """Beat grid of an audio file; tracks on the drums stem when it has real drums."""
    import librosa

    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    duration = len(y) / SR
    if len(y) < 4 * HOP or not np.any(y):
        return _finish(_steady(0.0, DEFAULT_BPM), duration, None, 0.0, duration)

    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    track_env = env
    if drums_wav is not None:
        d, _ = librosa.load(str(drums_wav), sr=SR, mono=True)
        if len(d) >= 4 * HOP and _rms(d) >= DRUMS_MIN * _rms(y):
            track_env = librosa.onset.onset_strength(y=d, sr=SR, hop_length=HOP)

    start, end = _music_span(y)
    tempo, frames = librosa.beat.beat_track(onset_envelope=track_env, sr=SR, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    beats = _fill_gaps(librosa.frames_to_time(frames, sr=SR, hop_length=HOP))
    if len(beats) >= 4:
        beats = _smooth(_fold_tempo(beats, track_env))
    if len(beats) < 4 or not MIN_BPM <= 60.0 / float(np.median(np.diff(beats))) <= MAX_BPM:
        beats = _steady(start, tempo)
    return _finish(beats, duration, env, start, end)


def grid_from_midi(pm) -> BeatGrid:
    """Beat grid straight from a pretty_midi.PrettyMIDI (3/4 -> 3 beats per bar, else 4)."""
    end = pm.get_end_time()
    beats = np.asarray(pm.get_beats(), dtype=float)
    if len(beats) < 2:
        _, tempi = pm.get_tempo_changes()
        beats = _steady(0.0, float(tempi[0]) if len(tempi) else DEFAULT_BPM)
    beats = _extend(beats, end)

    sigs = sorted(pm.time_signature_changes, key=lambda s: s.time)
    bpb = 3 if sigs and sigs[0].numerator == 3 else 4

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
    return BeatGrid([float(b) for b in beats], bpb, first)


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
