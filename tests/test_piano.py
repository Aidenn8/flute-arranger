import random
from collections import defaultdict

from sheetswap.model import BeatGrid, Note, QNote
from sheetswap.piano import _quantize_chords, accompany

# Quarter-note tune over C, G, Am, F (one chord per 4/4 bar at 120 bpm).
TUNE = [76, 74, 72, 74, 74, 71, 67, 69, 72, 71, 69, 71, 69, 67, 65, 64]
ROOTS = (0, 7, 9, 5)


def _grid(n_beats=64, beat=0.5, bpb=4):
    return BeatGrid([i * beat for i in range(n_beats)], bpb, 0)


def _triad(root, minor, start, end, vel=70):
    third = 3 if minor else 4
    return [Note(root, start, end, vel), Note(root + third, start, end, vel),
            Note(root + 7, start, end, vel)]


def _progression():
    notes = []
    for i, (root, minor) in enumerate([(48, False), (43, False), (45, True), (41, False)]):
        t0 = 2.0 * i
        notes += _triad(root + 12, minor, t0, t0 + 2.0)
        notes.append(Note(root, t0, t0 + 2.0, 80))
    notes += [Note(p, 0.5 * i, 0.5 * i + 0.45, 90) for i, p in enumerate(TUNE)]
    return notes


def _flute(octave=0, shift=0):
    return [QNote(p + octave + shift, 48 * i, 48) for i, p in enumerate(TUNE)]


def _by_onset(hand):
    groups = defaultdict(list)
    for n in hand:
        groups[n.start].append(n)
    return dict(sorted(groups.items()))


def _overlaps(a, b):
    return a.start < b.end and b.start < a.end


def test_lh_in_bass_register():
    g = _grid()
    for shift in (-5, -2, 0, 3, 6):
        rh, lh = accompany(_progression(), _flute(12, shift), g, "chords", shift=shift)
        assert lh
        assert all(36 <= n.pitch <= 47 for n in lh)
        assert all(n.start % 96 == 0 for n in lh)          # on beat 1 or beat 3
        # held with no gaps from the first downbeat to the end of the last chord
        ordered = sorted(lh, key=lambda n: n.start)
        assert ordered[0].start == 0 and ordered[-1].end == 768
        assert all(a.end == b.start for a, b in zip(ordered, ordered[1:]))
        # the bass note on each downbeat is the chord root, moved with the flute
        on_downbeat = [n.pitch % 12 for n in ordered if n.start % 192 == 0]
        assert on_downbeat == [(r + shift) % 12 for r in ROOTS]
    rh, lh = accompany(_progression(), _flute(), g, "cleaned")
    assert lh and all(n.pitch < 60 for n in lh)


def test_chords_style_voice_leading():
    rh, lh = accompany(_progression(), _flute(12), _grid(), "chords")
    chords = {s: sorted(n.pitch for n in ns) for s, ns in _by_onset(rh).items()}
    assert list(chords) == [0, 192, 384, 576]               # one chord per bar here
    voicings = list(chords.values())
    for v in voicings:
        assert len(v) == 3 and 52 <= v[0] and v[-1] <= 67
        assert v[-1] - v[0] <= 9                             # close position
    assert [{p % 12 for p in v} for v in voicings] == [
        {0, 4, 7}, {7, 11, 2}, {9, 0, 4}, {5, 9, 0}]
    for a, b in zip(voicings, voicings[1:]):
        assert sum(abs(x - y) for x, y in zip(a, b)) <= 5   # nearest voicing, no jumps


def test_chords_rh_ducks_under_low_flute():
    for low in (60, 62, 64):
        flute = [QNote(low, 0, 768)]
        rh, lh = accompany(_progression(), flute, _grid(), "chords")
        groups = _by_onset(rh)
        assert {0, 192, 384, 576} <= set(groups)             # still plays every bar
        assert all(n.pitch <= low - 3 for n in rh)
        assert all(len(ns) >= 2 for ns in groups.values())


def test_bass_decides_root():
    g = _grid()
    notes = [Note(60, 0.0, 2.0, 70), Note(64, 0.0, 2.0, 70)]   # C + E: C major or A minor?
    flute = [QNote(84, 0, 192)]
    rh, lh = accompany(notes, flute, g, "chords")
    assert {n.pitch for n in lh} == {36}
    rh, lh = accompany(notes, flute, g, "chords", bass=[Note(45, 0.0, 2.0, 80)])
    assert {n.pitch for n in lh} == {45}
    assert {n.pitch % 12 for n in rh} == {9, 0, 4}
    rh, lh = accompany(notes, [], g, "chords")              # no flute: still accompanies
    assert rh and lh


def test_silence_leaves_rests():
    notes = (_triad(60, False, 0.0, 2.0) + [Note(48, 0.0, 2.0)]
             + _triad(57, True, 4.0, 6.0) + [Note(45, 4.0, 6.0)])
    flute = [QNote(79, 0, 576)]
    for style in ("chords", "cleaned"):
        rh, lh = accompany(notes, flute, _grid(), style)
        assert rh and lh
        assert not any(n.start < 384 and n.end > 192 for n in rh + lh), style


def test_cleaned_drops_tune_and_splits_hands():
    g = _grid()
    tune = [(76, 0.0), (74, 0.5), (72, 1.0), (76, 1.5)]
    notes = [Note(p, t, t + 0.45, 90) for p, t in tune]
    notes += [Note(p, 0.0, 2.0, 70) for p in (36, 48, 60, 64, 67)]
    notes.append(Note(79, 1.5, 2.0, 60))                     # sits above the flute
    notes += [Note(p, 2.0, 3.0, 70) for p in (36, 40, 43, 48, 60, 62, 64, 65, 67)]
    for octave in (0, 12):                                   # flute may be an octave up
        flute = [QNote(p + octave, int(t * 96), 48) for p, t in tune]
        flute.append(QNote(84 + octave, 192, 96))
        rh, lh = accompany(notes, flute, g, "cleaned")
        rh_on = {s: sorted(n.pitch for n in ns) for s, ns in _by_onset(rh).items()}
        lh_on = {s: sorted(n.pitch for n in ns) for s, ns in _by_onset(lh).items()}
        above = 67 if octave == 0 else 79                    # dropped an octave only if needed
        assert rh_on == {0: [60, 64, 67], 144: [above], 192: [60, 62, 64]}
        assert lh_on == {0: [36, 48], 192: [36, 40]}         # <=2 per onset, lowest kept
        # notes at one onset share a length and stop before the next onset
        assert {n.dur for n in rh if n.start == 0} == {144}
        assert {n.dur for n in lh if n.start == 0} == {192}
    rh, lh = accompany(notes, [QNote(p + 2, int(t * 96), 48) for p, t in tune], g,
                       "cleaned", shift=2)
    assert sorted(n.pitch for n in lh if n.start == 0) == [38, 50]


def test_cleaned_keeps_the_third():
    # G2 bass, G3-B3-D4 chord: the left hand holds two notes, so B3 moves to
    # the right hand instead of vanishing (it is what makes the chord major).
    notes = [Note(p, 0.0, 2.0, 70) for p in (43, 55, 59, 62)]
    rh, lh = accompany(notes, [QNote(79, 0, 192)], _grid(), "cleaned")
    assert sorted(n.pitch for n in lh) == [43, 55]
    assert sorted(n.pitch for n in rh) == [59, 62]


def test_quantize_chords_snaps_in_beat_space():
    g = BeatGrid([0.0, 0.5, 1.0, 1.5, 2.0, 2.6, 3.2, 3.8], 4, 0)
    notes = [Note(60, 0.02, 0.49, 70), Note(60, 0.03, 0.9, 90),   # same pitch, same onset
             Note(64, 0.01, 0.04, 50),                             # tiny -> one step long
             Note(67, 2.62, 3.21, 80)]                             # slower beats here
    assert _quantize_chords(notes, g, step=24) == [
        QNote(60, 0, 96, 90), QNote(64, 0, 24, 50), QNote(67, 240, 48, 80)]


def test_quantize_chords_same_pitch_never_overlaps():
    g = _grid()
    notes = [Note(60, 0.0, 1.0), Note(60, 0.5, 1.5), Note(64, 0.0, 1.5)]
    q = _quantize_chords(notes, g, step=24)
    assert q == [QNote(60, 0, 48), QNote(64, 0, 144), QNote(60, 48, 96)]


def _random_case(seed):
    rng = random.Random(seed)
    beats, t = [], 0.0
    for _ in range(48):
        beats.append(t)
        t += rng.uniform(0.4, 0.7)
    bpb = rng.choice([3, 4])
    g = BeatGrid(beats, bpb, rng.randrange(bpb))
    end = beats[-1]
    notes = []
    for _ in range(160):
        s = rng.uniform(-0.5, end)
        notes.append(Note(rng.randint(28, 100), s, s + rng.uniform(0.03, 2.5),
                          rng.randint(20, 120)))
    bass = None
    if rng.random() < 0.5:
        bass = []
        for _ in range(40):
            s = rng.uniform(0.0, end)
            bass.append(Note(rng.randint(28, 52), s, s + rng.uniform(0.2, 1.5)))
    flute, tick = [], -rng.choice([0, 24, 48])
    while tick < g.to_ticks(end):
        dur = rng.choice([12, 24, 24, 48, 48, 72, 96, 144])
        if rng.random() > 0.15:                       # otherwise a rest
            pitch = rng.choice([rng.randint(60, 66), rng.randint(60, 96)])
            flute.append(QNote(pitch, tick, dur))
        tick += dur
    return notes, bass, flute, g, rng.randint(-5, 6)


def test_piano_never_above_flute():
    for seed in range(25):
        notes, bass, flute, g, shift = _random_case(seed)
        for style in ("chords", "cleaned"):
            rh, lh = accompany(notes, flute, g, style, shift=shift, bass=bass)
            assert rh and lh, (seed, style)
            rh_margin = 3 if style == "chords" else 2
            for hand, margin in ((rh, rh_margin), (lh, 1)):
                for p in hand:
                    assert p.dur > 0 and 21 <= p.pitch <= 108
                    for f in flute:
                        if _overlaps(p, f):
                            assert p.pitch <= f.pitch - margin, (seed, style, p, f)
                # each hand reads as plain chords: one length per onset, no overlaps
                groups = list(_by_onset(hand).items())
                for i, (start, ns) in enumerate(groups):
                    assert len({n.dur for n in ns}) == 1
                    assert len({n.pitch for n in ns}) == len(ns)
                    if i + 1 < len(groups):
                        assert start + ns[0].dur <= groups[i + 1][0]


def _under_flute(hand, flute, margin):
    return all(p.pitch <= f.pitch - margin for p in hand for f in flute if _overlaps(p, f))


def test_maj7_voicing_over_the_bass():
    # Gymnopedie-style 3/4: G2 / D2 in the left hand, B-D-F# / A-C#-F# in the right
    beat = 0.7
    g = BeatGrid([i * beat for i in range(28)], 3, 0)
    notes = []
    for b in range(8):
        t0 = 3 * b * beat
        bass, chord = ((43, (59, 62, 66)) if b % 2 == 0 else (38, (57, 61, 66)))
        notes.append(Note(bass, t0, t0 + 3 * beat, 60))
        notes += [Note(p, t0 + beat, t0 + 3 * beat, 55) for p in chord]
    flute = [QNote(p, 48 * i, 48) for i, p in
             enumerate([0, 90, 93, 91, 90, 85, 83, 85, 86, 81, 81, 81]) if p]
    rh, lh = accompany(notes, flute, g, "chords")
    assert [(n.start, n.pitch) for n in sorted(lh, key=lambda n: n.start)] == [
        (144 * b, 43 if b % 2 == 0 else 38) for b in range(8)]
    for s, ns in _by_onset(rh).items():
        want = {11, 2, 6} if (s // 144) % 2 == 0 else {9, 1, 6}    # no doubled root
        assert len(ns) == 3 and {n.pitch % 12 for n in ns} == want
    assert _under_flute(rh, flute, 3) and _under_flute(lh, flute, 1)


def test_lh_plays_the_real_bass_note():
    chord = [Note(p, 0.0, 2.0, 70) for p in (60, 64, 67)]
    flute = [QNote(84, 0, 192)]
    for low, lh_pitch in ((36, 36), (40, 40), (43, 43), (28, 40)):  # C, C/E, C/G, C/E
        rh, lh = accompany(chord + [Note(low, 0.0, 2.0, 80)], flute, _grid(), "chords")
        assert {n.pitch for n in lh} == {lh_pitch}
        assert {n.pitch % 12 for n in rh} == {0, 4, 7}
    rh, lh = accompany(chord, flute, _grid(), "chords", bass=[Note(52, 0.0, 2.0, 80)])
    assert {n.pitch for n in lh} == {40}                         # bass part E3 -> E2
    rh, lh = accompany(chord, flute, _grid(), "chords", shift=2, bass=[Note(40, 0.0, 2.0, 80)])
    assert {n.pitch for n in lh} == {42} and {n.pitch % 12 for n in rh} == {2, 6, 9}


def test_tune_decides_the_chord():
    acc = [Note(64, 0.0, 2.0, 70), Note(67, 0.0, 2.0, 70)]      # E + G: C or Em?
    for tune, root, third in ((84, 0, 4), (83, 4, 7)):            # C6 -> C, B5 -> Em
        rh, lh = accompany(acc, [QNote(tune, 0, 192)], _grid(), "chords")
        assert {n.pitch % 12 for n in lh} == {root}
        assert third in {n.pitch % 12 for n in rh}


def test_thin_accompaniment_follows_the_tune():
    # a tune with just a stray note or two under it still gets I IV V I
    tune = [76, 79, 84, 79, 77, 81, 84, 81, 74, 79, 83, 79, 84]
    for shift in (0, 3):
        flute = [QNote(p + shift, 48 * i, 48 if i < 12 else 192) for i, p in enumerate(tune)]
        for notes in ([Note(30, 0.1, 0.2, 40)], [Note(40, 0.0, 0.3, 50), Note(90, 2.0, 2.2, 30)]):
            rh, lh = accompany(notes, flute, _grid(), "chords", shift=shift)
            assert [(n.start, n.pitch % 12) for n in sorted(lh, key=lambda n: n.start)] == [
                (s, (r + shift) % 12) for s, r in ((0, 0), (192, 5), (384, 7), (576, 0))]
            assert all(36 <= n.pitch <= 47 for n in lh)
            # every downbeat's tune note is in the right-hand chord
            for s, ns in _by_onset(rh).items():
                f = next(q for q in flute if q.start == s)
                assert f.pitch % 12 in {n.pitch % 12 for n in ns}
            assert _under_flute(rh, flute, 3) and _under_flute(lh, flute, 1)
