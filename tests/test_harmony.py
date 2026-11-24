from sheetswap.harmony import KK_MAJOR, KK_MINOR, detect_chords, detect_key
from sheetswap.model import BeatGrid, Note

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
