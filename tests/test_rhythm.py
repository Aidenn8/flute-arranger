import numpy as np

from sheetswap.model import BeatGrid, Note, QNote
from sheetswap.rhythm import add_breath_marks, quantize_line, snap


def _steady(period=0.5, n=40, start=0.0, bpb=4, first=0):
    return BeatGrid([start + period * i for i in range(n)], bpb, first)


def _check_line(q, min_dur):
    starts = [n.start for n in q]
    assert starts == sorted(starts)
    assert all(n.dur >= min_dur for n in q)
    assert all(a.end <= b.start for a, b in zip(q, q[1:]))


def test_snap():
    assert snap(4.4, 3) == 3
    assert snap(4.6, 3) == 6
    assert snap(4.5, 3) == 6
    assert snap(-1.4, 3) == 0
    assert snap(-1.6, 3) == -3
    assert snap(7.9, 4) == 8


def test_quantize_straight_eighths():
    g = _steady(0.5)
    rng = np.random.default_rng(0)
    notes = []
    for i in range(16):
        t = 0.25 * i + rng.uniform(-0.02, 0.02)
        notes.append(Note(72 + i % 5, t, t + 0.23))
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [6 * i for i in range(16)]
    assert all(n.dur == 6 for n in q)
    assert [n.pitch for n in q] == [72 + i % 5 for i in range(16)]


def test_quantize_triplets():
    g = _steady(0.5)
    rng = np.random.default_rng(1)
    notes = []
    for i in range(12):
        t = 0.5 * i / 3 + rng.uniform(-0.005, 0.005)
        notes.append(Note(70, t, t + 0.16))
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [4 * i for i in range(12)]
    assert all(n.dur == 4 for n in q)


def test_quantize_mixed_beats_choose_own_grid():
    # beat 0: two 8ths, beat 1: a triplet, beat 2: four 16ths
    g = _steady(0.6)
    times = [0.0, 0.3, 0.6, 0.8, 1.0, 1.2, 1.35, 1.5, 1.65]
    notes = [Note(74, t, t + 0.14) for t in times]
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [0, 6, 12, 16, 20, 24, 27, 30, 33]
    _check_line(q, 3)


def test_quantize_follows_tempo_drift():
    gaps = np.linspace(0.5, 0.7, 24)
    beats = [0.0] + list(np.cumsum(gaps))
    g = BeatGrid([float(b) for b in beats], 4, 0)
    rng = np.random.default_rng(2)
    notes = []
    for i in range(len(beats) - 1):
        s = beats[i] + rng.uniform(-0.02, 0.02)
        e = beats[i + 1] - 0.03
        notes.append(Note(65 + i % 7, s, e))
    q = quantize_line(notes, g)
    assert len(q) == len(notes)
    assert all(n.start % 12 == 0 for n in q)
    assert [n.start for n in q] == [12 * i for i in range(len(notes))]
    assert all(n.dur == 12 for n in q)


def test_simple_mode_min_eighth():
    g = _steady(0.5)
    rng = np.random.default_rng(3)
    notes = []
    for i in range(32):  # a run of 16ths with uneven loudness
        t = 0.125 * i + rng.uniform(-0.01, 0.01)
        notes.append(Note(70 + i % 9, t, t + 0.1, int(rng.integers(40, 120))))
    q = quantize_line(notes, g, simple=True)
    _check_line(q, 6)
    assert all(n.start % 6 == 0 for n in q)
    assert all(n.dur % 6 == 0 for n in q)
    q_full = quantize_line(notes, g)
    _check_line(q_full, 3)
    assert all(n.start % 3 == 0 for n in q_full)
    assert len(q_full) == 32


def test_collision_keeps_louder():
    g = _steady(0.5)
    notes = [Note(70, 0.0, 0.2, 50), Note(75, 0.03, 0.4, 100), Note(72, 0.5, 1.0, 80)]
    q = quantize_line(notes, g)
    assert [(n.pitch, n.start) for n in q] == [(75, 0), (72, 12)]
    assert q[0].dur == 12  # ends 2.4 ticks before the next onset (< a 16th) -> extended
    assert q[0].velocity == 100


def test_short_gaps_held_long_rests_kept():
    g = _steady(0.5)
    notes = [
        Note(70, 0.0, 0.42),   # ends 1.9 ticks before the next onset -> held to it
        Note(72, 0.5, 0.75),   # ends 6 ticks (an 8th) before the next onset -> held (legato)
        Note(74, 1.0, 1.4),    # ends 14.4 ticks before the next onset -> a rest of a beat stays
        Note(76, 2.5, 3.0),
    ]
    q = quantize_line(notes, g)
    assert [(n.start, n.dur) for n in q] == [(0, 12), (12, 12), (24, 12), (60, 12)]


def test_rubato_eighths_written_as_eighths():
    g = _steady(0.5)
    # eighth notes played unevenly (up to 0.1 beat early/late) stay plain 8ths
    offs = [0.0, 0.08, -0.09, 0.1, 0.0, -0.06, 0.07, 0.0]
    notes = [Note(72 + i, (i * 0.5 + o) * 0.5, (i * 0.5 + o) * 0.5 + 0.2) for i, o in enumerate(offs)]
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [0, 6, 12, 18, 24, 30, 36, 42]
    assert all(n.dur == 6 for n in q[:-1])


def test_pickup_notes_get_negative_ticks():
    g = _steady(0.5, n=20, bpb=4, first=4)   # first downbeat at 2.0 s
    notes = [Note(67, 1.5, 1.95), Note(72, 2.0, 2.95)]
    q = quantize_line(notes, g)
    assert [(n.start, n.dur) for n in q] == [(-12, 12), (0, 24)]


def test_quantize_random_line_invariants():
    g = BeatGrid([0.55 * i + 0.01 * np.sin(i) for i in range(80)], 3, 1)
    rng = np.random.default_rng(4)
    for simple in (False, True):
        t, notes = 0.3, []
        while t < 40:
            d = rng.uniform(0.03, 0.9)
            notes.append(Note(int(rng.integers(60, 90)), t, t + d, int(rng.integers(1, 127))))
            t += d * rng.uniform(0.6, 1.6)
        q = quantize_line(notes, g, simple=simple)
        md = 6 if simple else 3
        _check_line(q, md)
        assert all(n.start % 3 == 0 or n.start % 4 == 0 for n in q)
        if simple:
            assert all(n.start % 6 == 0 for n in q)


def test_quantize_empty():
    assert quantize_line([], _steady()) == []


def _line(durs, start=0, gaps=None):
    out, t = [], start
    for i, d in enumerate(durs):
        out.append(QNote(70 + i % 5, t, d))
        t += d + (gaps[i] if gaps else 0)
    return out


def test_breath_marks_long_phrase():
    bar = 48
    line = _line([12] * 32)  # 8 bars of unbroken quarter notes in 4/4
    out = add_breath_marks(line, 4)
    ends = [n.end for n in out if n.breath_after]
    assert ends == [2 * bar, 4 * bar, 6 * bar]
    assert not any(n.breath_after for n in line)   # input left untouched
    assert [(n.pitch, n.start, n.dur) for n in out] == [(n.pitch, n.start, n.dur) for n in line]


def test_breath_marks_pick_longest_note():
    durs = [12, 12, 12, 24] + [12] * 28   # a half note ending at tick 60 inside the first window
    out = add_breath_marks(_line(durs), 4)
    marked = [n for n in out if n.breath_after]
    assert marked[0].end == 60 and marked[0].dur == 24


def test_breath_marks_three_four():
    out = add_breath_marks(_line([12] * 24), 3)   # 8 bars of 3/4
    assert [n.end for n in out if n.breath_after] == [72, 144, 216]


def test_no_breath_marks_in_short_or_rested_phrases():
    short = _line([12] * 12)  # 3 bars
    assert not any(n.breath_after for n in add_breath_marks(short, 4))
    # 8 bars, but an 8th rest after every 2 bars
    gaps = [6 if (i + 1) % 8 == 0 else 0 for i in range(32)]
    rested = _line([12] * 32, gaps=gaps)
    assert not any(n.breath_after for n in add_breath_marks(rested, 4))
    # a 16th rest is not a breath
    gaps16 = [3 if (i + 1) % 8 == 0 else 0 for i in range(32)]
    assert any(n.breath_after for n in add_breath_marks(_line([12] * 32, gaps=gaps16), 4))


def test_breath_marks_empty():
    assert add_breath_marks([], 4) == []


def test_long_note_leaned_into_lands_on_the_beat():
    g = _steady(0.5)
    # a held note sung a 16th early (0.125 beat) before beat 2, then one a 16th late on beat 4
    notes = [Note(72, 0.0, 0.45), Note(74, 0.4375, 1.4), Note(76, 1.53, 2.4)]
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [0, 12, 36]


def test_dotted_rhythm_with_two_notes_in_a_beat_is_kept():
    g = _steady(0.5)
    notes = [Note(72, 0.0, 0.37), Note(74, 0.375, 0.5), Note(76, 0.5, 1.0)]   # dotted 8th + 16th
    q = quantize_line(notes, g)
    assert [(n.start, n.dur) for n in q][:2] == [(0, 9), (9, 3)]
