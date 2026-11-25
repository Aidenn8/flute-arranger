"""The decoding around the Beat This! network: meter choice, unsure beats, bar repetition.

All on synthetic activation curves and notes (the network itself isn't run)."""
import numpy as np

from sheetswap import neural_beats as nb
from sheetswap.beats import bar_repetition
from sheetswap.model import Note

FPS = nb.FPS


def _curves(period=0.75, bpb=4, seconds=60.0, beat_peak=0.9, down_peak=0.85, start=1.0):
    """Beat and downbeat probabilities with a peak on every beat (every bpb-th a downbeat)."""
    n = int(seconds * FPS)
    beat = np.full(n, 0.02)
    down = np.full(n, 0.01)
    times = np.arange(start, seconds - 1.0, period)
    for i, t in enumerate(times):
        f = int(round(t * FPS))
        beat[f] = beat_peak
        beat[f - 1] = beat[f + 1] = beat_peak / 2
        if i % bpb == 0:
            down[f] = down_peak
            down[f - 1] = down[f + 1] = down_peak / 2
    return beat, down, times


def _pattern_notes(beats, pattern):
    """Notes on `beats` (seconds): beat i gets onsets at the fractions pattern[i % len(pattern)]."""
    out = []
    for i, (a, b) in enumerate(zip(beats, beats[1:])):
        for f in pattern[i % len(pattern)]:
            t = a + f * (b - a)
            out.append(Note(72, t, t + 0.1, 90))
    return out


FOUR = [(0.0, 0.5), (0.0,), (0.0, 0.25, 0.5, 0.75), (0.0, 1 / 3, 2 / 3)]
THREE = [(0.0, 0.25, 0.5), (0.0,), (0.0, 1 / 3, 2 / 3)]


def test_bar_repetition_tells_four_from_three():
    beats = np.arange(0.0, 40.0, 0.6)
    assert bar_repetition(beats, _pattern_notes(beats, FOUR)) > 0.2
    assert bar_repetition(beats, _pattern_notes(beats, THREE)) < -0.2


def test_bar_repetition_needs_enough_beats():
    beats = np.arange(0.0, 5.0, 0.6)
    assert bar_repetition(beats, _pattern_notes(beats, FOUR)) == 0.0


def test_bar_repetition_ignores_beats_before_the_start():
    beats = np.arange(0.0, 60.0, 0.6)
    notes = _pattern_notes(beats[:40], THREE) + _pattern_notes(beats[40:], FOUR)
    assert bar_repetition(beats, notes, start=beats[41]) > 0.2


def test_clear_curves_give_their_meter():
    beat, down, times = _curves(period=0.75, bpb=3)
    g = nb.grid_from_activations(beat, down, 60.0, 1.0)
    assert g.beats_per_bar == 3
    assert abs(g.bpm() - 80) < 2
    assert g.tracker == "neural"


def test_notes_decide_a_close_meter_call():
    """Equal downbeat evidence for 3 and 4 (a downbeat on every 12th beat): the
    rhythm that repeats every 4 beats makes it 4, every 3 beats makes it 3."""
    beat, down, times = _curves(period=0.75, bpb=12)
    four = nb.grid_from_activations(beat, down, 60.0, 1.0, _pattern_notes(times, FOUR))
    three = nb.grid_from_activations(beat, down, 60.0, 1.0, _pattern_notes(times, THREE))
    assert four.beats_per_bar == 4
    assert three.beats_per_bar == 3


def test_notes_dont_overrule_a_clear_network():
    beat, down, times = _curves(period=0.75, bpb=3)
    g = nb.grid_from_activations(beat, down, 60.0, 1.0, _pattern_notes(times, FOUR))
    assert g.beats_per_bar == 3


def test_choose_meter_weighs_likelihood_against_repetition():
    beats = np.arange(0.0, 40.0, 0.6)
    notes = _pattern_notes(beats, FOUR)
    close = {3: nb.MeterPath(3, -1.000, beats, beats[::3]), 4: nb.MeterPath(4, -1.003, beats, beats[::4])}
    clear = {3: nb.MeterPath(3, -1.0, beats, beats[::3]), 4: nb.MeterPath(4, -1.3, beats, beats[::4])}
    assert nb.choose_meter(close).bpb == 3                 # no notes: the likelier path
    assert nb.choose_meter(close, notes).bpb == 4
    assert nb.choose_meter(clear, notes).bpb == 3


def test_beat_confidence():
    beat, _, times = _curves(beat_peak=0.9)
    assert nb.beat_confidence(beat, times) > 0.85
    assert nb.beat_confidence(beat, times + 0.37) < 0.1
    assert nb.beat_confidence(beat, []) == 0.0


def test_pulse_decode_follows_the_beat_curve():
    beat, _, times = _curves(period=0.6, beat_peak=0.5)
    pulse = nb.pulse_decode(beat, 60, 140)
    assert abs(np.median(np.diff(pulse)) - 0.6) < 0.03
    # every beat found, within a frame
    near = np.min(np.abs(pulse[:, None] - times[None, :]), axis=0)
    assert np.mean(near <= 1.0 / FPS + 1e-9) > 0.95


def test_pulse_decode_keeps_to_its_tempo_range():
    beat, _, _ = _curves(period=0.6, beat_peak=0.5)
    slow = nb.pulse_decode(beat, 40, 60)                   # 100 BPM curve, 40-60 allowed: every other beat
    assert abs(np.median(np.diff(slow)) - 1.2) < 0.05


def test_unsure_network_beats_tracked_from_the_beat_curve(monkeypatch):
    """Weak beat peaks (the network unsure) and a downbeat curve that would
    drag the bar-pointer path: the beats come from pulse_decode near the
    path's tempo, the bar from the path."""
    beat, down, times = _curves(period=0.75, bpb=4, beat_peak=0.4, down_peak=0.35)
    calls = []
    real = nb.pulse_decode

    def spy(p, lo, hi):
        calls.append((lo, hi))
        return real(p, lo, hi)

    monkeypatch.setattr(nb, "pulse_decode", spy)
    g = nb.grid_from_activations(beat, down, 60.0, 1.0)
    assert len(calls) == 1
    lo, hi = calls[0]
    assert lo < 80 < hi and hi / lo < 1.6
    assert abs(g.bpm() - 80) < 2
    assert g.beats_per_bar == 4


def test_sure_network_keeps_its_path(monkeypatch):
    beat, down, _ = _curves(period=0.75, bpb=4)
    monkeypatch.setattr(nb, "pulse_decode", lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    assert nb.grid_from_activations(beat, down, 60.0, 1.0).beats_per_bar == 4


def test_bar_from_downbeats_with_a_given_meter():
    beats = np.arange(0.0, 30.0, 0.5)
    downs = beats[1::4]                                    # every 4th beat from beat 1
    assert nb.bar_from_downbeats(beats, downs) == (4, 1)
    assert nb.bar_from_downbeats(beats, downs, 4) == (4, 1)
    assert nb.bar_from_downbeats(beats, np.array([]), 3) == (3, 0)


def test_bar_pointer_decode_still_picks_the_likeliest_meter():
    beat, down, _ = _curves(period=0.75, bpb=3)
    beats, downs, bpb = nb.bar_pointer_decode(beat, down)
    paths = nb.bar_pointer_paths(beat, down)
    assert bpb == 3 == max(paths.values(), key=lambda p: p.score).bpb
    assert np.allclose(beats, paths[3].beats)
    assert len(downs) and abs(np.median(np.diff(downs)) - 2.25) < 0.05
