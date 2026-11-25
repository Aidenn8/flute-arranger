from sheetswap.harmony import KK_MAJOR, KK_MINOR, detect_chords, detect_key, harmonize
from sheetswap.model import BeatGrid, Note, QNote

# A tune in C major: every scale note, with the tonic triad held longer.
C_MAJOR_TUNE = {60: 4, 62: 1, 64: 3, 65: 1, 67: 3, 69: 1, 71: 1, 72: 2}
# A tune in A minor (harmonic minor, G# leading note), tonic triad held longer.
A_MINOR_TUNE = {57: 4, 59: 1, 60: 3, 62: 1, 64: 3, 65: 1, 68: 1, 69: 2}


def _moved(tune, semitones):
    return [(p + semitones, w) for p, w in tune.items()]


def _grid(n_beats=64, beat=0.5, bpb=4):
    return BeatGrid([i * beat for i in range(n_beats)], bpb, 0)


def _triad(root, minor, start, end, vel=70):
    third = 3 if minor else 4
    return [Note(root, start, end, vel), Note(root + third, start, end, vel),
            Note(root + 7, start, end, vel)]


def test_detect_key_c_major():
    assert detect_key(list(C_MAJOR_TUNE.items())) == (0, "major")
    assert detect_key(_moved(C_MAJOR_TUNE, 7)) == (1, "major")     # G major
    assert detect_key(_moved(C_MAJOR_TUNE, 5)) == (-1, "major")    # F major
    assert detect_key(_moved(C_MAJOR_TUNE, 3)) == (-3, "major")    # E-flat major


def test_detect_key_a_minor():
    # minor keys report the fifths of their relative major
    assert detect_key(list(A_MINOR_TUNE.items())) == (0, "minor")
    assert detect_key(_moved(A_MINOR_TUNE, 7)) == (1, "minor")     # E minor
    assert detect_key(_moved(A_MINOR_TUNE, 5)) == (-1, "minor")    # D minor


def test_detect_key_every_key_in_range():
    for tonic in range(12):
        for profile, mode in ((KK_MAJOR, "major"), (KK_MINOR, "minor")):
            weights = [(60 + (tonic + i) % 12, w) for i, w in enumerate(profile)]
            fifths, got_mode = detect_key(weights)
            assert got_mode == mode
            assert -6 <= fifths <= 6
            major_tonic = tonic if mode == "major" else (tonic + 3) % 12
            assert (fifths * 7) % 12 == major_tonic


def test_detect_key_empty_defaults_to_c_major():
    assert detect_key([]) == (0, "major")
    assert detect_key([(60, 0.0)]) == (0, "major")


def _progression():
    """C, G, Am, F, one chord per 4/4 bar at 120 bpm: piano triad, bass root,
    and a quarter-note tune with passing tones."""
    notes = []
    for i, (root, minor) in enumerate([(48, False), (43, False), (45, True), (41, False)]):
        t0 = 2.0 * i
        notes += _triad(root + 12, minor, t0, t0 + 2.0)
        notes.append(Note(root, t0, t0 + 2.0, 80))
    tune = [76, 74, 72, 74, 74, 71, 67, 69, 72, 71, 69, 71, 69, 67, 65, 64]
    notes += [Note(p, 0.5 * i, 0.5 * i + 0.45, 90) for i, p in enumerate(tune)]
    return notes


def test_chords_c_g_progression():
    rows = detect_chords(_progression(), _grid(), 24)
    # same chord in both halves of a bar -> one row per bar
    assert rows == [(0, 48, 0, "maj"), (48, 48, 7, "maj"),
                    (96, 48, 9, "min"), (144, 48, 5, "maj")]


def test_chords_change_mid_bar():
    notes = _triad(60, False, 0.0, 1.0) + _triad(55, False, 1.0, 2.0)
    rows = detect_chords(notes, _grid(), 24)
    assert rows == [(0, 24, 0, "maj"), (24, 24, 7, "maj")]


def test_chords_silence_has_no_chord():
    notes = _triad(60, False, 0.0, 2.0) + _triad(57, True, 4.0, 6.0)
    rows = detect_chords(notes, _grid(), 24)
    assert rows == [(0, 48, 0, "maj"), (96, 48, 9, "min")]


def test_chords_follow_beats_not_seconds():
    # tempo slows from 0.5 s to 0.8 s per beat; chords change on bar 2's downbeat
    beats, t = [], 0.0
    for i in range(16):
        beats.append(t)
        t += 0.5 + 0.3 * i / 15
    g = BeatGrid(beats, 4, 0)
    notes = _triad(60, False, beats[0], beats[4]) + _triad(55, False, beats[4], beats[8])
    rows = detect_chords(notes, g, 24)
    assert rows == [(0, 48, 0, "maj"), (48, 48, 7, "maj")]


# ---------------------------------------------------------------- chord choice

def _waltz_maj7(bars=8, beat=0.7):
    """Gymnopedie-style 3/4: bass on beat 1, chord on beats 2-3, alternating
    Gmaj7 (G2 + B3 D4 F#4) and Dmaj7 (D2 + A3 C#4 F#4)."""
    g = BeatGrid([i * beat for i in range(3 * bars + 4)], 3, 0)
    notes = []
    for b in range(bars):
        t0 = 3 * b * beat
        bass, chord = ((43, (59, 62, 66)) if b % 2 == 0 else (38, (57, 61, 66)))
        notes.append(Note(bass, t0, t0 + 3 * beat, 60))
        notes += [Note(p, t0 + beat, t0 + 3 * beat, 55) for p in chord]
    return notes, g


def test_tune_picks_between_chords_that_fit_alike():
    # E4 + G4 belong to C major and to E minor; the tune's note decides
    acc = [Note(64, 0.0, 2.0, 70), Note(67, 0.0, 2.0, 70)]
    for tune, chord in ((72, (0, "maj")), (71, (4, "min"))):     # C5 -> C, B4 -> Em
        for key in ((0, "major"), None):
            rows = harmonize(acc, _grid(), melody=[QNote(tune, 0, 48)], key=key)
            assert [(c.start, c.dur, c.root, c.quality) for c in rows] == [(0, 48, *chord)]


def test_maj7_alternation_over_g_and_d_bass():
    # Satie: G and D in the bass under B-D-F# and A-C#-F#: Gmaj7 / Dmaj7, never B minor
    notes, g = _waltz_maj7()
    tune = [QNote(p, 12 * i, 12) for i, p in
            enumerate([0, 78, 81, 79, 78, 73, 71, 73, 74, 69, 69, 69]) if p]
    for melody in (None, tune):
        rows = detect_chords(notes, g, melody=melody)
        assert rows == [(36 * b, 36, 7 if b % 2 == 0 else 2, "maj7") for b in range(8)]
    chords = harmonize(notes, g)
    assert [c.bass for c in chords] == [7, 2] * 4                  # root position


def test_missing_third_follows_the_key():
    # A + E with no third: A minor in A minor, A major in A major
    fifth = [Note(45, 0.0, 2.0, 80), Note(52, 0.0, 2.0, 70)]
    assert detect_chords(fifth, _grid(), key=(0, "minor")) == [(0, 48, 9, "min")]
    assert detect_chords(fifth, _grid(), key=(3, "major")) == [(0, 48, 9, "maj")]
    # in context: Am, Dm, E, then the open fifth, with the key found from the notes
    notes = []
    for i, chord in enumerate([(57, 60, 64), (50, 53, 57), (52, 56, 59)]):
        notes += [Note(p, 2.0 * i, 2.0 * i + 2.0, 70) for p in chord]
        notes.append(Note(chord[0] - 12, 2.0 * i, 2.0 * i + 2.0, 80))
    notes += [Note(45, 6.0, 8.0, 80), Note(57, 6.0, 8.0, 70), Note(64, 6.0, 8.0, 70)]
    assert detect_chords(notes, _grid()) == [(0, 48, 9, "min"), (48, 48, 2, "min"),
                                             (96, 48, 4, "maj"), (144, 48, 9, "min")]


def test_seventh_only_when_it_sounds():
    # G B D F: G7; G B D alone stays a triad even with an F in the tune
    g7 = [Note(43, 0.0, 2.0, 80)] + [Note(p, 0.0, 2.0, 70) for p in (59, 62, 65)]
    assert detect_chords(g7, _grid(), key=(0, "major")) == [(0, 48, 7, "7")]
    g = [Note(43, 0.0, 2.0, 80)] + [Note(p, 0.0, 2.0, 70) for p in (59, 62, 67)]
    rows = detect_chords(g, _grid(), melody=[QNote(77, 36, 12)], key=(0, "major"))
    assert rows == [(0, 48, 7, "maj")]


def test_inversion_from_the_bass():
    chord = [Note(p, 0.0, 2.0, 70) for p in (60, 64, 67)]
    for low, bass in ((36, 0), (40, 4), (43, 7)):                  # C, C/E, C/G
        rows = harmonize(chord + [Note(low, 0.0, 2.0, 80)], _grid(), key=(0, "major"))
        assert [(c.root, c.quality, c.bass) for c in rows] == [(0, "maj", bass)]
    rows = harmonize(chord, _grid(), bass=[Note(40, 0.0, 2.0, 80)], key=(0, "major"))
    assert [(c.root, c.quality, c.bass) for c in rows] == [(0, "maj", 4)]


def test_thin_accompaniment_harmonizes_the_tune():
    # a few stray notes are no accompaniment to follow: the tune gets diatonic
    # chords that hold its notes, V-I at the end
    tune = [76, 79, 84, 79, 77, 81, 84, 81, 74, 79, 83, 79, 72]
    melody = [QNote(p, 12 * i, 12 if i < 12 else 48) for i, p in enumerate(tune)]
    for stray in ([Note(30, 0.1, 0.2, 40)], [Note(30, 0.1, 0.2, 40), Note(95, 3.1, 3.3, 40)]):
        rows = harmonize(stray, _grid(), melody=melody)
        assert [(c.start, c.root, c.quality) for c in rows] == [
            (0, 0, "maj"), (48, 5, "maj"), (96, 7, "maj"), (144, 0, "maj")]
    # noise (a cluster of every pitch class) is no accompaniment either
    noise = [Note(48 + i, 0.0, 8.0, 60) for i in range(12)]
    assert [(c.root, c.quality) for c in harmonize(noise, _grid(), melody=melody)] == [
        (0, "maj"), (5, "maj"), (7, "maj"), (0, "maj")]
    assert harmonize([], _grid(), melody=melody)                # a tune alone: harmonized
    assert harmonize([], _grid()) == []                         # nothing at all: no piano
