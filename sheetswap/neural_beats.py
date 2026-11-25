"""Beat and bar tracking with the "Beat This!" tracker (used by pipeline.analyze).

Beat This! (Foscarin, Schlüter & Widmer, ISMIR 2024) is a neural network that
hears beats and downbeats straight from the audio, 50 frames a second. On real
piano recordings it keeps the beat through rubato and puts bar lines where the
music has them, which the older librosa tracker (sheetswap.beats) did not.

    from sheetswap.neural_beats import track_beats_neural, neural_beats
    grid = track_beats_neural(Path("out/433f3ceb/audio.wav"))         # -> BeatGrid
    beats, downbeats = neural_beats(Path("out/433f3ceb/audio.wav"))    # seconds

Drop-in: `track_beats_neural(mono, drums, beat_notes)` takes the same arguments
as sheetswap.beats.track_beats; the drums stem is ignored (the network hears
the beat in the mix itself). The notes, when given, help choose 3 or 4 beats a
bar (choose_meter).

Two ways to turn the network's beat/downbeat curves into times:

- dbn=False: Beat This!'s own "minimal" step, every peak above 50%. Beats are
  good, but downbeats come out irregular: on chorales and pop beats it marks
  beat 3 as often as beat 1, so the bar reads as 2 beats.
- dbn=True (default): a bar-pointer hidden Markov model decoded with Viterbi,
  the model of madmom's DBNDownBeatTrackingProcessor that Beat This! uses for
  its own dbn=True. madmom isn't installed, so this file carries a numpy port
  (same transition/observation settings). It keeps one steady beat through
  held notes and rubato, and puts a bar line exactly every 3 or 4 beats.
  One change: the slowest tempo is 30 BPM, not 55, so a slow compound beat
  (Chopin's Nocturne Op. 9/2: dotted quarter = 39) is tracked as itself;
  with 55 it becomes 2 beats per dotted quarter, a level no note value has.
  Two additions (grid_from_activations): the meter is the HMM's 3 or 4 plus
  how the notes' rhythm repeats every 3 or 4 beats (Träumerei is 4/4, the
  network alone says 3/4), and where the network is unsure of the beat
  (slow rubato piano, solo violin) the beats come from the beat curve alone
  (pulse_decode), because weak downbeat guesses forced into bars drag the
  beat onto off-beats (Bach's Allemanda: 5% of onsets exact -> 73%).

The network runs on the CPU (Apple's MPS backend crashes on macOS 14); its
weights (77 MB) download once into ~/.cache/torch/hub/checkpoints/.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import numpy as np

from .model import BeatGrid

FPS = 50                   # Beat This! frame rate
MIN_BPM, MAX_BPM = 30.0, 215.0
TRANSITION_LAMBDA = 100.0  # how strongly the tempo resists change from beat to beat
OBSERVATION_LAMBDA = 16    # a beat "region" is the first 1/16 of each beat interval
THRESHOLD = 0.05           # frames before/after the first/last activation above this are skipped
METERS = (3, 4)            # beats per bar the HMM may choose from
SILENCE = 0.01             # RMS below 1% of the peak (-40 dB) counts as silence (as sheetswap.beats)
CONFIDENT = 0.65           # mean beat probability at the beats under this: the network is unsure
                           # (Träumerei 0.43, the Allemanda 0.61; everything else measured 0.67-0.99)
PULSE_SPREAD = 1.25        # ...and the beat is tracked again within this factor of its tempo
MIN_PULSE_BPM = 20.0
REPEAT_WEIGHT = 0.1        # log probability per frame one unit of beats.bar_repetition is worth

_model = None


def _frames_model():
    global _model
    if _model is None:
        from beat_this.inference import Audio2Frames
        _model = Audio2Frames(checkpoint_path="final0", device="cpu", float16=False)
    return _model


def _load(wav: Path) -> tuple[np.ndarray, int]:
    from beat_this.preprocessing import load_audio
    signal, sr = load_audio(str(wav))
    if signal.ndim == 2:
        signal = signal.mean(axis=1)
    return np.asarray(signal, dtype=np.float64), int(sr)


def neural_activations(wav: Path, signal=None, sr=None) -> tuple[np.ndarray, np.ndarray]:
    """Beat and downbeat probabilities, one value per 1/50 s frame."""
    import torch
    if signal is None:
        signal, sr = _load(wav)
    beat_logits, down_logits = _frames_model()(signal, sr)
    return (torch.sigmoid(beat_logits).double().numpy(),
            torch.sigmoid(down_logits).double().numpy())


def neural_beats(wav: Path, dbn: bool = True, meters=METERS, min_bpm: float = MIN_BPM,
                 signal=None, sr=None) -> tuple[np.ndarray, np.ndarray]:
    """(beats, downbeats) in seconds, straight from the tracker (no extension, no meter fixing)."""
    if signal is None:
        signal, sr = _load(wav)
    if not dbn:
        from beat_this.model.postprocessor import Postprocessor
        beat_logits, down_logits = _frames_model()(signal, sr)
        b, d = Postprocessor(type="minimal", fps=FPS)(beat_logits, down_logits)
        return np.asarray(b, dtype=float), np.asarray(d, dtype=float)
    beat_p, down_p = neural_activations(wav, signal, sr)
    beats, downs, _ = bar_pointer_decode(beat_p, down_p, meters, min_bpm=min_bpm)
    return beats, downs


def track_beats_neural(wav: Path, drums_wav: Path | None = None, notes=None, *,
                       dbn: bool = True, meters=METERS, min_bpm: float = MIN_BPM) -> BeatGrid:
    """Beat grid of an audio file (drop-in for sheetswap.beats.track_beats).

    beats cover 0 s .. the end of the file (edge intervals repeated, as the
    current tracker does); beats_per_bar is the most common number of beats
    between neighbouring downbeats; first_downbeat is the first downbeat at
    or after the moment the music starts (earlier beats are a pickup).
    drums_wav and notes are accepted for compatibility and ignored.
    """
    signal, sr = _load(wav)
    duration = len(signal) / sr if sr else 0.0
    start, _end = _music_span(signal, sr)
    if not dbn:
        beats, downs = neural_beats(wav, dbn=False, signal=signal, sr=sr)
        return grid_from_beats(beats, downs, duration, start)
    beat_p, down_p = neural_activations(wav, signal, sr)
    return grid_from_activations(beat_p, down_p, duration, start, notes, meters=meters, min_bpm=min_bpm)


def grid_from_activations(beat_p: np.ndarray, down_p: np.ndarray, duration: float, music_start: float,
                          notes=None, *, meters=METERS, min_bpm: float = MIN_BPM) -> BeatGrid:
    """BeatGrid from the network's beat/downbeat probabilities (and the transcribed notes).

    1. The bar-pointer HMM, once per meter (bar_pointer_paths).
    2. The meter: the HMM's likelihoods, plus how the notes' rhythm repeats
       every 3 or 4 beats (choose_meter) when notes are given.
    3. Where the network is unsure of the beat (mean beat probability at the
       beats under CONFIDENT), the beats are tracked again from the beat
       curve alone at about the same tempo (pulse_decode); the bar lines stay
       where the meter's path put them.
    """
    paths = bar_pointer_paths(beat_p, down_p, meters, min_bpm=min_bpm)
    if not paths:
        return grid_from_beats([], [], duration, music_start)
    path = choose_meter(paths, notes, music_start)
    beats = path.beats
    if len(beats) >= 4 and beat_confidence(beat_p, beats) < CONFIDENT:
        bpm = 60.0 / float(np.median(np.diff(beats)))
        pulse = pulse_decode(beat_p, max(MIN_PULSE_BPM, bpm / PULSE_SPREAD), min(MAX_BPM, bpm * PULSE_SPREAD))
        if len(pulse) >= 4:
            beats = pulse
    return grid_from_beats(beats, path.downs, duration, music_start, bpb=path.bpb)


def choose_meter(paths: dict, notes=None, music_start: float = 0.0) -> "MeterPath":
    """The meter's path: the HMM's likeliest, unless the notes' rhythm says otherwise.

    The network's downbeat curve often can't tell 3 from 4 beats a bar on
    slow rubato piano (Träumerei: 3/4 by 2 thousandths of a log unit per
    frame). Bars repeat their rhythm, so beats.bar_repetition (how much more
    alike beats 4 and 8 apart are than beats 3 and 6 apart) moves the choice:
    REPEAT_WEIGHT log units per frame per unit of repetition. Clear network
    choices (most songs: 0.02-0.3 per frame) are left alone.
    """
    best = max(paths.values(), key=lambda p: p.score)
    if not notes or 3 not in paths or 4 not in paths:
        return best
    from .beats import bar_repetition
    rep = bar_repetition(best.beats, notes, music_start)
    z = paths[4].score - paths[3].score + REPEAT_WEIGHT * rep
    pick = paths[4] if z > 0 else paths[3]
    other = max((p for p in paths.values() if p.bpb not in (3, 4)), key=lambda p: p.score, default=None)
    return other if other is not None and other.score > max(paths[3].score, paths[4].score) else pick


def beat_confidence(beat_p: np.ndarray, beats) -> float:
    """Mean beat probability at the beats (peak within +-2 frames)."""
    if len(beats) == 0:
        return 0.0
    fr = np.clip(np.round(np.asarray(beats) * FPS).astype(int), 0, len(beat_p) - 1)
    return float(np.mean([beat_p[max(0, f - 2):f + 3].max() for f in fr]))


def grid_from_beats(beats, downs, duration: float, music_start: float, bpb: int | None = None) -> BeatGrid:
    """BeatGrid from tracker output: gaps filled, extended over the file, bar from the downbeats
    (with bpb given, only where beat 1 falls comes from them)."""
    beats = _fill_gaps(np.asarray(beats, dtype=float))
    if len(beats) < 4:
        return BeatGrid([float(b) for b in _extend(np.array([music_start, music_start + 0.5]),
                                                   duration)], 4, 0)
    beats = _extend(beats, duration)
    bpb, phase = bar_from_downbeats(beats, downs, bpb)
    period = float(np.median(np.diff(beats)))
    first = next((i for i in range(len(beats))
                  if i % bpb == phase and beats[i] >= music_start - 0.25 * period), phase)
    return BeatGrid([float(b) for b in beats], int(bpb), int(first), tracker="neural")


def bar_from_downbeats(beats: np.ndarray, downs: np.ndarray, bpb: int | None = None) -> tuple[int, int]:
    """(beats per bar, phase): the commonest beat count between downbeats (or
    bpb when given), and the beat index (mod that count) most downbeats fall on."""
    beats = np.asarray(beats, dtype=float)
    if len(downs) == 0 or len(beats) < 2:
        return bpb or 4, 0
    period = float(np.median(np.diff(beats)))
    idx = []
    for d in downs:
        k = int(np.argmin(np.abs(beats - d)))
        if abs(beats[k] - d) < 0.35 * period:
            idx.append(k)
    idx = sorted(set(idx))
    if bpb is None:
        gaps = [b - a for a, b in zip(idx, idx[1:]) if 2 <= b - a <= 12]
        bpb = Counter(gaps).most_common(1)[0][0] if gaps else 4
    phase = Counter(k % bpb for k in idx).most_common(1)[0][0] if idx else 0
    return bpb, phase


# --- bar-pointer HMM (numpy port of madmom's DBNDownBeatTrackingProcessor) ---

def _tempo_intervals(min_bpm: float = MIN_BPM, max_bpm: float = MAX_BPM) -> np.ndarray:
    lo = 60.0 * FPS / max_bpm
    hi = 60.0 * FPS / min_bpm
    return np.arange(int(round(lo)), int(round(hi)) + 1)


def _tempo_transitions(taus: np.ndarray) -> np.ndarray:
    """log P(next beat interval | this one): exp(-lambda |ratio - 1|), rows normalised."""
    ratio = taus[None, :] / taus[:, None]
    prob = np.exp(-TRANSITION_LAMBDA * np.abs(ratio - 1.0))
    prob[prob <= np.spacing(1)] = 0.0
    prob /= prob.sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore"):
        return np.log(prob)


def bar_pointer_decode(beat_p: np.ndarray, down_p: np.ndarray, meters=METERS,
                       min_bpm: float = MIN_BPM, max_bpm: float = MAX_BPM):
    """(beats, downbeats, beats_per_bar) from frame-wise probabilities.

    Same model as madmom's DBNDownBeatTrackingProcessor with Beat This!'s
    settings (transition_lambda=100, observation_lambda=16, 3 or 4 beats per
    bar), except the tempo range: min_bpm..max_bpm (Beat This! uses 55-215):
    one HMM per meter, the most likely one wins. Beats are snapped to the
    strongest frame of each beat's start region, as madmom's correct=True.
    """
    paths = bar_pointer_paths(beat_p, down_p, meters, min_bpm, max_bpm)
    if not paths:
        return np.array([]), np.array([]), 4
    best = max(paths.values(), key=lambda p: p.score)
    return best.beats, best.downs, best.bpb


class MeterPath:
    """One meter's best path: beats and downbeats (seconds), its log probability per frame."""

    def __init__(self, bpb: int, score: float, beats: np.ndarray, downs: np.ndarray):
        self.bpb, self.score, self.beats, self.downs = bpb, score, beats, downs


def bar_pointer_paths(beat_p: np.ndarray, down_p: np.ndarray, meters=METERS,
                      min_bpm: float = MIN_BPM, max_bpm: float = MAX_BPM) -> dict[int, MeterPath]:
    """The bar-pointer HMM decoded once per meter: {beats per bar: MeterPath}.

    Scores are per frame, so they compare across meters (the same frames)."""
    eps = 1e-5
    beat_p = beat_p * (1 - eps) + eps / 2
    down_p = down_p * (1 - eps) + eps / 2
    act = np.stack([np.maximum(beat_p - down_p, eps / 2), down_p], axis=1)
    above = np.flatnonzero(act.max(axis=1) >= THRESHOLD)
    if len(above) < 2:
        return {}
    first, last = int(above[0]), int(above[-1]) + 1
    act = act[first:last]
    no_beat = np.clip(1.0 - act.sum(axis=1), eps / 2, None) / (OBSERVATION_LAMBDA - 1)
    log_obs = np.log(np.stack([no_beat, act[:, 0], act[:, 1]], axis=1))
    taus = _tempo_intervals(min_bpm, max_bpm)
    strength = act.sum(axis=1)
    out = {}
    for bpb in meters:
        score, tj, tk, tp = _viterbi_scored(log_obs, bpb, taus)
        beats, numbers = _path_beats(tj, tk, tp, taus, strength)
        beats = (beats + first) / FPS
        downs = beats[numbers == 0] if len(beats) else beats
        out[bpb] = MeterPath(bpb, score / len(log_obs), beats, downs)
    return out


def pulse_decode(beat_p: np.ndarray, min_bpm: float = MIN_BPM, max_bpm: float = MAX_BPM) -> np.ndarray:
    """Beats (seconds) from the beat probabilities alone: the same HMM with one
    beat per "bar", so the downbeat curve can't pull the beats around.

    Used where the network is unsure of the beat (slow rubato piano, solo
    violin): its downbeat guesses there are weak and, forced into bars of 3 or
    4, they drag the beat onto off-beats (Träumerei, Bach's Allemanda)."""
    eps = 1e-5
    bp = beat_p * (1 - eps) + eps / 2
    above = np.flatnonzero(bp >= THRESHOLD)
    if len(above) < 2:
        return np.array([])
    first, last = int(above[0]), int(above[-1]) + 1
    bp = bp[first:last]
    no_beat = np.clip(1.0 - bp, eps / 2, None) / (OBSERVATION_LAMBDA - 1)
    log_obs = np.log(np.stack([no_beat, np.full_like(bp, eps / 2), bp], axis=1))
    taus = _tempo_intervals(min_bpm, max_bpm)
    _, tj, tk, tp = _viterbi_scored(log_obs, 1, taus)
    beats, _ = _path_beats(tj, tk, tp, taus, bp)
    return (beats + first) / FPS


def _path_beats(tj, tk, tp, taus, strength) -> tuple[np.ndarray, np.ndarray]:
    """(beat frames, beat number in the bar) along a decoded path: each beat at
    the strongest frame of its start region (madmom's correct=True)."""
    zone = tp < taus[tj] / OBSERVATION_LAMBDA
    beats, numbers = [], []
    t = 0
    T = len(zone)
    while t < T:
        if zone[t]:
            u = t
            while u + 1 < T and zone[u + 1] and tk[u + 1] == tk[t]:
                u += 1
            f = t + int(np.argmax(strength[t:u + 1]))
            beats.append(f)
            numbers.append(int(tk[t]))
            t = u + 1
        else:
            t += 1
    return np.asarray(beats, dtype=float), np.asarray(numbers, dtype=int)


def _viterbi_scored(log_obs: np.ndarray, bpb: int, taus: np.ndarray):
    """Best path through (tempo, beat in bar, frame in beat) for one meter.

    log_obs: (T, 3) log densities for [no beat, beat, downbeat]. Tempo may
    change only where a beat ends. Returns (path log probability, and per
    frame: tempo index, beat in the bar, frame within the beat).
    """
    n, maxtau = len(taus), int(taus.max())
    valid = np.arange(maxtau)[None, :] < taus[:, None]
    beat_zone = np.arange(maxtau)[None, :] < taus[:, None] / OBSERVATION_LAMBDA
    cls = np.zeros((n, bpb, maxtau), dtype=np.int64)
    cls[:, 1:, :] = np.where(beat_zone, 1, 0)[:, None, :]
    cls[:, 0, :] = np.where(beat_zone, 2, 0)
    invalid = ~np.broadcast_to(valid[:, None, :], (n, bpb, maxtau))
    log_t = _tempo_transitions(taus)
    last = taus - 1
    n_states = int(taus.sum()) * bpb

    delta = np.full((n, bpb, maxtau), -np.log(n_states))
    delta[invalid] = -np.inf
    delta += log_obs[0][cls]
    offset = 0.0
    T = len(log_obs)
    back = np.zeros((T, bpb, n), dtype=np.int16)
    rows = np.arange(n)
    for t in range(1, T):
        ends = delta[rows, :, last]
        prev = np.roll(ends, 1, axis=1)
        cand = prev[:, :, None] + log_t[:, None, :]
        arg = np.argmax(cand, axis=0)
        best = np.take_along_axis(cand, arg[None], axis=0)[0]
        new = np.empty_like(delta)
        new[:, :, 1:] = delta[:, :, :-1]
        new[:, :, 0] = best.T
        new[invalid] = -np.inf
        new += log_obs[t][cls]
        back[t] = arg
        m = new.max()
        offset += m
        delta = new - m
    j, k, p = np.unravel_index(int(np.argmax(delta)), delta.shape)
    score = offset + float(delta.max())
    tj = np.empty(T, dtype=np.int64)
    tk = np.empty(T, dtype=np.int64)
    tp = np.empty(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        tj[t], tk[t], tp[t] = j, k, p
        if p > 0:
            p -= 1
        else:
            j, k = int(back[t, k, j]), (k - 1) % bpb
            p = int(taus[j]) - 1
    return score, tj, tk, tp


# --- helpers (copies of sheetswap.beats', so this file doesn't depend on its private names) ---

def _music_span(y: np.ndarray, sr: int) -> tuple[float, float]:
    """First and last moment the audio is louder than -40 dB of its peak (2048/1024-sample RMS)."""
    hop = 1024
    n = len(y) // hop
    if n < 2:
        return 0.0, len(y) / max(sr, 1)
    frames = y[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    if rms.max() <= 0:
        return 0.0, len(y) / sr
    loud = np.flatnonzero(rms > SILENCE * rms.max())
    return float(loud[0] * hop / sr), float((loud[-1] + 1) * hop / sr)


def _fill_gaps(beats: np.ndarray) -> np.ndarray:
    """Drop beats closer than half a beat, fill holes longer than 1.5 beats."""
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 3:
        return beats
    med = float(np.median(np.diff(beats)))
    if med <= 0:
        return beats[:1]
    out = [beats[0]]
    for b in beats[1:]:
        gap = b - out[-1]
        if gap < 0.5 * med:
            continue
        if gap > 1.5 * med:
            k = int(round(gap / med))
            out.extend(out[-1] + gap * j / k for j in range(1, k))
        out.append(b)
    return np.array(out)


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
