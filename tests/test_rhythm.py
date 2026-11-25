import numpy as np

from sheetswap.model import BeatGrid, Note, QNote
from sheetswap.rhythm import add_breath_marks, quantize_line, snap, uses_triplets


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


# ---------------------------------------------------------------- compound and 8th beats

def _six_eight(period=0.6, n=40, bpb=2, first=0):
    """Dotted-quarter beats (6/8 with bpb=2)."""
    return BeatGrid([period * i for i in range(n)], bpb, first, beat_ticks=18)


def test_compound_three_even_notes_are_plain_eighths():
    # three notes a beat (what 4/4 would write as 8th triplets) in 6/8: plain 8ths
    g = _six_eight(0.6)
    rng = np.random.default_rng(5)
    notes = []
    for i in range(12):
        t = 0.2 * i + rng.uniform(-0.015, 0.015)
        notes.append(Note(72 + i % 4, t, t + 0.18))
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [6 * i for i in range(12)]
    assert all(n.dur == 6 for n in q)


def test_compound_quarter_eighth_and_sixteenths():
    g = _six_eight(0.6)
    # beat 0: quarter + 8th; beat 1: six 16ths; beat 2: dotted 8th, 16th, 8th (siciliano)
    times = [0.0, 0.4, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 1.6]
    notes = [Note(74, t, t + 0.09) for t in times]
    q = quantize_line(notes, g)
    assert [n.start for n in q] == [0, 12, 18, 21, 24, 27, 30, 33, 36, 45, 48]
    _check_line(q, 3)
    assert all(n.start % 3 == 0 for n in q)


def test_compound_long_note_snaps_to_the_dotted_beat():
    g = _six_eight(0.6)
    # held notes started a little early / late (0.1 beat) land on the beat
    notes = [Note(72, 0.0, 0.55), Note(74, 0.54, 1.15), Note(76, 1.27, 1.75)]
    q = quantize_line(notes, g)
    assert [(n.start, n.dur) for n in q] == [(0, 18), (18, 18), (36, 18)]
    # one that really starts on the 2nd 8th stays there
    q = quantize_line([Note(72, 0.2, 0.6), Note(74, 0.6, 1.1)], g)
    assert q[0].start == 6


def test_compound_never_uses_triplets():
    g = BeatGrid([0.55 * i + 0.01 * np.sin(i) for i in range(80)], 4, 1, beat_ticks=18)
    rng = np.random.default_rng(6)
    t, notes = 0.3, []
    while t < 40:
        d = rng.uniform(0.03, 0.9)
        notes.append(Note(int(rng.integers(60, 90)), t, t + d, int(rng.integers(1, 127))))
        t += d * rng.uniform(0.6, 1.6)
    for simple in (False, True):
        q = quantize_line(notes, g, simple=simple)
        _check_line(q, 6 if simple else 3)
        assert all(n.start % (6 if simple else 3) == 0 for n in q)
        assert all(n.end % 3 == 0 for n in q)


def test_eighth_beats_split_in_sixteenths():
    # 3/8 counted in three 8th beats (0.4 s each): 16ths fall on half beats
    g = BeatGrid([0.4 * i for i in range(40)], 3, 0, beat_ticks=6)
    times = [0.0, 0.2, 0.4, 0.6, 0.8, 1.2, 1.4, 1.6]
    q = quantize_line([Note(76, t, t + 0.18) for t in times], g)
    assert [n.start for n in q] == [0, 3, 6, 9, 12, 18, 21, 24]
    assert all(n.start % 3 == 0 for n in q)
    # a long note a little late lands on its beat
    q = quantize_line([Note(76, 0.03, 0.9), Note(77, 1.2, 1.6)], g)
    assert q[0].start == 0 and q[1].start == 18


def test_breath_marks_compound_and_short_bars():
    # 6/8 has two beats a bar, like 2/4: windows of 4 bars (8 dotted quarters)
    out = add_breath_marks(_line([18] * 24), 2, 18)     # 12 bars of dotted quarters
    assert [n.end for n in out if n.breath_after] == [144, 288]
    # 9/8 and 12/8 count like 3/4 and 4/4: windows of 2 bars
    out = add_breath_marks(_line([18] * 24), 3, 18)     # 8 bars of 9/8
    assert [n.end for n in out if n.breath_after] == [108, 216, 324]
    # 3/8 (one felt beat a bar) and 2/4: windows of 4 bars
    out = add_breath_marks(_line([6] * 48), 3, 6)       # 16 bars of 8ths
    assert [n.end for n in out if n.breath_after] == [72, 144, 216]
    out = add_breath_marks(_line([12] * 32), 2)         # 16 bars of 2/4
    assert [n.end for n in out if n.breath_after] == [96, 192, 288]


# ---------------------------------------------------------------- reading the piece's rhythm

TICK = 0.5 / 12       # seconds per tick at 0.5 s a quarter beat (_steady)


def _at(ticks, pitch=72, length=5):
    """Notes starting at the given tick positions (floats), `length` ticks long."""
    return [Note(pitch + i % 5, t * TICK, (t + length) * TICK, 80) for i, t in enumerate(ticks)]


def test_wavering_eighths_stay_eighths_in_a_piece_of_eighths():
    # a minuet's plain 8ths, played up to 2 ticks (1/6 beat) early or late: still 8ths,
    # not 8th + 16th figures
    wobble = [0, 2, -1.5, 1.8, -2, 1, 0, -1.8, 1.5, 2, -1, 0]
    ticks = [6 * i + wobble[i % len(wobble)] for i in range(48)]
    q = quantize_line(_at(ticks), _steady(0.5, n=40))
    assert [n.start for n in q] == [6 * i for i in range(48)]


def test_sixteenth_runs_read_as_sixteenths():
    ticks = [3 * i + float(np.sin(0.7 * i)) for i in range(64)]      # up to a tick early or late
    q = quantize_line(_at(ticks, length=2.5), _steady(0.5, n=40))
    assert [n.start for n in q] == [3 * i for i in range(64)]


def test_uneven_sixteenths_are_not_read_as_triplets():
    # 16ths and 8ths; two beats hold notes that happen to sit near a triplet's places
    ticks = []
    for beat in range(30):
        b = 12 * beat
        if beat in (5, 22):
            ticks += [b, b + 4.2, b + 7.8]          # meant as 16th, 16th, 8th (0, 3, 6)
        elif beat % 2:
            ticks += [b, b + 3, b + 6, b + 9]
        else:
            ticks += [b, b + 6]
    q = quantize_line(_at(ticks, length=2.5), _steady(0.5, n=40))
    assert len(q) == len(ticks)
    assert all(n.start % 3 == 0 for n in q)


def test_uses_triplets():
    trip = [12 * b + p for b in range(16) for p in (0, 4, 8)]
    six = [12 * b + p for b in range(16) for p in (0, 3, 6, 9)]
    assert uses_triplets(trip, 12)
    assert not uses_triplets(six, 12)
    assert not uses_triplets([x + 0.9 * (i % 2) for i, x in enumerate(six)], 12)
    assert not uses_triplets(trip, 18)        # dotted beats split in three are plain 8ths


def test_short_notes_played_just_before_the_beat_land_on_it():
    # pairs of 8ths whose downbeat note comes 2 ticks early every time
    ticks = [t for b in range(1, 17) for t in (12 * b - 2, 12 * b + 6)]
    q = quantize_line(_at(ticks, length=4), _steady(0.5, n=40))
    assert [n.start for n in q] == [t for b in range(1, 17) for t in (12 * b, 12 * b + 6)]


def test_a_sung_scoop_into_a_note_is_left_out():
    # quarter notes; the one on beat 2 is sung with a short scoop up from below
    notes = [Note(70 + b % 3, 12 * b * TICK, (12 * b + 11) * TICK) for b in range(40) if b != 2]
    notes += [Note(67, 22.9 * TICK, 24.6 * TICK), Note(72, 24.6 * TICK, 35 * TICK)]
    q = quantize_line(notes, _steady(0.5, n=50))
    assert [(n.pitch, n.start) for n in q][1:4] == [(71, 12), (72, 24), (70, 36)]
    assert len(q) == 40
