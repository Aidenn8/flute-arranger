"""Tap the beat: grid_from_taps against a tracker's grid, and the pipeline hook."""
import json

import numpy as np
import pytest

from sheetswap import pipeline
from sheetswap.model import QUARTER_BEAT, Analysis, BeatGrid, Note
from sheetswap.taps import TAPS_FILE, first_bar_at, grid_from_taps

DURATION = 60.0


def neural(period=0.5, bpb=4, first=2, duration=DURATION):
    """A tracker's grid: steady beats over the whole file, beat 1 at index `first`."""
    n = int(duration / period) + 2
    return BeatGrid([round(i * period, 6) for i in range(n)], bpb, first, QUARTER_BEAT, tracker="neural")


def taps_at(times, jitter=0.0, lag=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return [float(t + lag + rng.uniform(-jitter, jitter)) for t in times]


def downbeat_times(g: BeatGrid, n=5):
    return np.array(g.beats)[g.first_downbeat::g.beats_per_bar][:n]


def covers(g: BeatGrid, duration=DURATION):
    b = np.array(g.beats)
    return b[0] >= 0 and b[0] <= np.median(np.diff(b)) and b[-1] >= duration and np.all(np.diff(b) > 0)


def test_steady_taps_keep_the_trackers_beats():
    cur = neural()
    g = grid_from_taps(taps_at([5 + 0.5 * i for i in range(16)], jitter=0.02), DURATION, None, cur)
    assert g.tracker == "taps"
    assert np.allclose(g.beats, cur.beats[:len(g.beats)])          # the tracker's timing, all song
    assert g.beats_per_bar == 4
    assert np.allclose(downbeat_times(g), [1.0, 3.0, 5.0, 7.0, 9.0])  # its bar lines too
    assert covers(g)


def test_sloppy_taps_with_outliers():
    cur = neural()
    beats = [10 + 0.5 * i for i in range(24)]
    t = taps_at(beats, jitter=0.04, lag=0.12, seed=3)               # +-40 ms, late through the speakers
    del t[9]                                                       # a missed beat
    t.append(t[4] + 0.03)                                          # a double tap
    t.append(t[12] + 0.24)                                         # a stray tap between beats
    t.insert(0, 3.31)                                              # a stray tap long before
    g = grid_from_taps(t, DURATION, None, cur)
    assert np.allclose(g.beats, cur.beats[:len(g.beats)])
    assert g.beats_per_bar == 4 and covers(g)


def test_half_tempo_takes_every_second_beat():
    cur = neural(first=2)
    # the tracker heard 8ths: the beat is every other one of its beats (the even ones)
    g = grid_from_taps(taps_at([6 + 1.0 * i for i in range(12)], jitter=0.04), DURATION, None, cur)
    b = np.array(g.beats)
    assert np.allclose(np.diff(b[2:-1]), 1.0)
    assert np.allclose(b[1:6], [1.0, 2.0, 3.0, 4.0, 5.0])            # on the tracker's even beats
    assert abs(60 / np.median(np.diff(b)) - 60) < 0.5
    assert g.beats_per_bar == 2                                     # 2 taps a tracker bar of 4
    assert covers(g)


def test_double_tempo_splits_the_trackers_beats():
    cur = neural(period=1.0, first=1)
    g = grid_from_taps(taps_at([4 + 0.5 * i for i in range(20)], jitter=0.03), DURATION, None, cur)
    b = np.array(g.beats)
    assert np.allclose(b[:5], [0.0, 0.5, 1.0, 1.5, 2.0])
    assert abs(np.median(np.diff(b)) - 0.5) < 1e-6
    assert g.beats_per_bar == 4 and downbeat_times(g, 2)[0] == pytest.approx(1.0)


def test_three_taps_a_tracker_beat_is_a_bar_of_three():
    # a waltz tracked one beat a bar (Brahms Op. 39/15): 1.5 s "beats", 4 to its "bar"
    cur = neural(period=1.5, bpb=4, first=1)
    g = grid_from_taps(taps_at([6 + 0.5 * i for i in range(24)], jitter=0.04), DURATION, None, cur)
    assert g.beats_per_bar == 3
    assert abs(np.median(np.diff(g.beats)) - 0.5) < 1e-6
    assert np.allclose(downbeat_times(g), [1.5, 3.0, 4.5, 6.0, 7.5])  # bar lines on its beats


def test_three_taps_a_tracker_bar_is_three_four():
    cur = neural(period=0.4, bpb=4, first=0)
    # one tap every 4/3 of the tracker's beats: 3 taps fill one of its bars
    g = grid_from_taps(taps_at([3.2 + 1.6 / 3 * i for i in range(18)], jitter=0.02), DURATION, None, cur)
    assert g.beats_per_bar == 3
    assert abs(np.median(np.diff(g.beats)) - 1.6 / 3) < 0.01
    assert downbeat_times(g, 3) == pytest.approx([0.0, 1.6, 3.2], abs=0.02)


def test_bar_start_taps_give_three_four():
    cur = neural(bpb=4, first=2)
    t = taps_at([5 + 0.5 * i for i in range(18)], jitter=0.03, seed=1)
    bars = [x for i, x in enumerate(t) if i % 3 == 1]                # beats 5.5, 7.0, 8.5, ...
    g = grid_from_taps(t, DURATION, bars, cur)
    assert g.beats_per_bar == 3
    assert np.allclose(g.beats, cur.beats[:len(g.beats)])
    d = downbeat_times(g, 8)
    assert {5.5, 7.0, 8.5} <= set(np.round(d, 6))
    # one missed bar mark still reads as 3/4
    g2 = grid_from_taps(t, DURATION, bars[:2] + bars[3:], cur)
    assert g2.beats_per_bar == 3 and g2.first_downbeat == g.first_downbeat


def test_taps_where_the_tracker_slipped():
    # the tracker loses half a beat at 20 s; the user taps the whole song
    beats = [i * 0.5 for i in range(40)] + [19.75 + i * 0.5 for i in range(1, 84)]
    cur = BeatGrid(beats, 4, 0, tracker="neural")
    true = [i * 0.5 for i in range(2, 118)]
    g = grid_from_taps(taps_at(true, jitter=0.02), DURATION, None, cur)
    b = np.array(g.beats)
    near = np.array([np.min(np.abs(b - t)) for t in true])
    assert near.max() < 0.05                                       # a beat at every tapped beat
    assert np.allclose(np.diff(b[2:40]), 0.5, atol=0.05)


def test_agreement_with_the_tracker():
    from sheetswap.taps import tracker_agreement
    steady = taps_at([5 + 1.5 * i for i in range(16)], jitter=0.04)
    assert tracker_agreement(steady, DURATION, neural()) == 1.0     # every 3rd of its beats
    # a tracker of 8ths that gains an 8th every 2.5 s: every 3rd of its beats keeps changing
    beats, t = [], 0.0
    while t < DURATION + 1:
        beats.append(t)
        t += 0.5 if len(beats) % 5 else 0.25
    slipping = BeatGrid(beats, 3, 0, tracker="neural")
    assert tracker_agreement(steady, DURATION, slipping) < 0.5


def test_no_tracker_grid_follows_the_taps():
    t = taps_at([2 + 0.8 * i for i in range(12)], jitter=0.02)
    g = grid_from_taps(t, 30.0, None, None)
    b = np.array(g.beats)
    assert abs(np.median(np.diff(b)) - 0.8) < 0.03
    assert covers(g, 30.0)


def test_too_few_or_unsteady_taps():
    with pytest.raises(ValueError):
        grid_from_taps([1.0, 1.5, 2.0], DURATION, None, neural())
    with pytest.raises(ValueError):                                 # no two gaps alike
        grid_from_taps([0.1 * 2 ** i for i in range(9)], DURATION, None, neural())


def test_first_bar_at_keeps_the_pickup_short():
    g = BeatGrid([i * 0.5 for i in range(40)], 3, 7, tracker="taps")
    assert first_bar_at(g, 0.9).first_downbeat == 4                 # 2.0 s: first bar start after 0.9 s
    assert first_bar_at(g, 0.6).first_downbeat == 1                 # 0.5 s is close enough to 0.6 s
    assert first_bar_at(g, 0.0).first_downbeat == 1


# ---------------------------------------------------------------- the song folder

def _song(tmp_path, kind="piano"):
    grid = neural(first=2)
    notes = [Note(72 + (i % 5), 1.0 + 0.5 * i, 1.4 + 0.5 * i, 80) for i in range(100)]
    an = Analysis("abc", kind, DURATION, grid, {"piano": notes})
    an.save(tmp_path / "analysis.json")
    (tmp_path / "arr" / "v2-x").mkdir(parents=True)
    (tmp_path / "arr" / "v2-x" / "info.json").write_text("{}")
    return grid


def test_apply_and_undo_taps(tmp_path):
    auto = _song(tmp_path)
    t = taps_at([5 + 1.0 * i for i in range(12)], jitter=0.02)
    an = pipeline.apply_taps(tmp_path, t, None)
    assert an.grid.tracker == "taps"
    saved = Analysis.load(tmp_path / "analysis.json")
    assert saved.grid.tracker == "taps" and abs(saved.grid.bpm() - 60) < 1
    assert not (tmp_path / "arr").exists()                           # pages are made again
    kept = json.loads((tmp_path / TAPS_FILE).read_text())
    assert len(kept["taps"]) == 12 and kept["auto_grid"]["tracker"] == "neural"
    assert kept["agree"] == 1.0 and kept["needs_more"] is False

    # tapping again starts from the automatic grid, not the tapped one
    bars = t[::3]
    an = pipeline.apply_taps(tmp_path, t, bars)
    assert an.grid.beats_per_bar == 3
    assert json.loads((tmp_path / TAPS_FILE).read_text())["auto_grid"]["beats"] == auto.beats

    an = pipeline.undo_taps(tmp_path)
    assert an.grid == auto
    assert Analysis.load(tmp_path / "analysis.json").grid == auto
    assert not (tmp_path / TAPS_FILE).exists()


def test_taps_leave_the_bars_alone_when_arranging(tmp_path):
    """Bar lines from tapped bar starts are not moved by the tune's long notes."""
    from sheetswap.beats import melody_downbeat
    g = BeatGrid([i * 0.5 for i in range(40)], 4, 1, tracker="taps")
    mel = [Note(76, (b * 4 + 2) * 0.5, (b * 4 + 2) * 0.5 + 1.0, 90) for b in range(9)]
    assert melody_downbeat(mel, g, 0.0) is g


def test_taps_refused_for_sheet_music(tmp_path):
    _song(tmp_path, kind="midi")
    with pytest.raises(ValueError):
        pipeline.apply_taps(tmp_path, [1.0 + 0.5 * i for i in range(10)], None)
