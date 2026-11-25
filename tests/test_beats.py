import json
from dataclasses import asdict

import numpy as np
import pretty_midi
import soundfile as sf

from sheetswap.beats import grid_from_midi, track_beats

SR = 22050


def _click(accent: bool) -> np.ndarray:
    """Short noise burst; accented clicks are louder and carry a low thump."""
    n = int(0.03 * SR)
    t = np.arange(n) / SR
    noise = np.random.default_rng(1).uniform(-1, 1, n) * np.exp(-t * 150)
    if not accent:
        return 0.2 * noise
    m = int(0.15 * SR)
    t2 = np.arange(m) / SR
    low = 0.8 * np.sin(2 * np.pi * 90 * t2) * np.exp(-t2 * 20)
    return np.pad(noise, (0, m - n)) + low


def _write(path, y):
    peak = np.abs(y).max()
    if peak > 0:
        y = 0.9 * y / peak
    sf.write(str(path), y, SR)
    return path


def _clicks(path, duration, times, accents=None):
    y = np.zeros(int(duration * SR))
    accents = accents if accents is not None else [False] * len(times)
    for t, a in zip(times, accents):
        c = _click(a)
        i = int(round(t * SR))
        seg = y[i:i + len(c)]
        seg += c[:len(seg)]
    return _write(path, y)


def _tones(path, duration, onsets, seed=0):
    rng = np.random.default_rng(seed)
    y = np.zeros(int(duration * SR))
    for t in onsets:
        f = rng.uniform(200, 800)
        n = int(0.4 * SR)
        tt = np.arange(n) / SR
        tone = 0.3 * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 6)
        i = int(t * SR)
        seg = y[i:i + n]
        seg += tone[:len(seg)]
    return _write(path, y)


def _assert_covers(g, duration):
    gap0 = g.beats[1] - g.beats[0]
    assert 0.0 <= g.beats[0] < gap0 + 1e-6
    assert g.beats[-1] >= duration - 1e-6
    assert all(b2 > b1 for b1, b2 in zip(g.beats, g.beats[1:]))


def _assert_downbeats(g, times, period):
    for t in times:
        b = g.to_beats(t)
        assert abs(b - round(b)) < 0.15, (t, b)
        assert round(b) % period == 0, (t, b)


def test_meter_three_four(tmp_path):
    times = [1.0 + 0.5 * i for i in range(48)]
    accents = [i % 3 == 0 for i in range(48)]
    g = track_beats(_clicks(tmp_path / "waltz.wav", 26.0, times, accents))
    assert g.beats_per_bar == 3
    assert 115 <= g.bpm() <= 125
    _assert_downbeats(g, [t for t, a in zip(times, accents) if a], 3)


def test_notes_give_meter_and_downbeat_when_audio_is_even(tmp_path):
    # even clicks (no accents): the audio alone can't tell 3/4; the notes can:
    # a long bass note starts every 3rd beat, short melody notes on the others
    from sheetswap.model import Note
    times = [1.0 + 0.5 * i for i in range(48)]
    wav = _clicks(tmp_path / "even.wav", 26.0, times)
    notes = []
    for i, t in enumerate(times):
        if i % 3 == 1:
            notes.append(Note(43, t, t + 1.45, 80))
        notes.append(Note(72 + i % 5, t, t + 0.3, 70))
    g = track_beats(wav, None, notes)
    assert g.beats_per_bar == 3
    _assert_downbeats(g, [t for i, t in enumerate(times) if i % 3 == 1], 3)


def test_meter_four_four(tmp_path):
    times = [1.25 + 0.5 * i for i in range(48)]
    accents = [i % 4 == 0 for i in range(48)]
    g = track_beats(_clicks(tmp_path / "march.wav", 26.0, times, accents))
    assert g.beats_per_bar == 4
    _assert_downbeats(g, [t for t, a in zip(times, accents) if a], 4)


def test_beats_cover_file_after_silent_intro(tmp_path):
    times = [6.0 + 0.6 * i for i in range(24)]
    accents = [i % 4 == 0 for i in range(24)]
    g = track_beats(_clicks(tmp_path / "intro.wav", 22.0, times, accents))
    _assert_covers(g, 22.0)
    assert 95 <= g.bpm() <= 105
    # the music starts on a downbeat, or at most a pickup of under one bar before it
    b = g.to_beats(6.0)
    assert -g.beats_per_bar < b < 0.25
    assert abs(b - round(b)) < 0.15


def test_silence_falls_back_to_steady_grid(tmp_path):
    p = _write(tmp_path / "silent.wav", np.zeros(4 * SR))
    g = track_beats(p)
    assert 60 <= g.bpm() <= 180
    assert g.beats_per_bar == 4
    _assert_covers(g, 4.0)


def test_few_sounds_give_regular_grid(tmp_path):
    g = track_beats(_tones(tmp_path / "sparse.wav", 12.0, [2.0, 5.3, 9.1]))
    assert 60 <= g.bpm() <= 180
    _assert_covers(g, 12.0)
    b = g.to_beats(2.0)
    assert abs(b - round(b)) < 0.15
    gaps = np.diff(g.beats)
    assert gaps.max() - gaps.min() < 0.05


def test_tracker_without_beats_falls_back_to_steady_grid(tmp_path, monkeypatch):
    import librosa

    p = _tones(tmp_path / "t.wav", 10.0, [1.5, 4.0, 7.0])
    for fake_tempo, want_bpm in ((0.0, 120.0), (200.0, 100.0), (45.0, 90.0)):
        monkeypatch.setattr(librosa.beat, "beat_track",
                            lambda *a, _t=fake_tempo, **k: (np.array([_t]), np.array([], dtype=int)))
        g = track_beats(p)
        assert abs(g.bpm() - want_bpm) < 0.5
        _assert_covers(g, 10.0)
        b = g.to_beats(1.5)      # steady grid starts where the music starts
        assert abs(b - round(b)) < 0.15
        assert -g.beats_per_bar < b < 0.25


def test_tiny_file_does_not_fail(tmp_path):
    p = _write(tmp_path / "tiny.wav", 0.1 * np.ones(SR // 20))
    g = track_beats(p)
    assert 60 <= g.bpm() <= 180
    assert len(g.beats) >= 2


def test_rubato_without_clear_beat(tmp_path):
    rng = np.random.default_rng(7)
    onsets, t = [], 0.3
    while t < 18:
        onsets.append(t)
        t += rng.uniform(0.15, 1.4)
    g = track_beats(_tones(tmp_path / "rubato.wav", 20.0, onsets))
    assert 60 <= g.bpm() <= 180
    assert g.beats_per_bar in (3, 4)
    _assert_covers(g, 20.0)


def test_tempo_folded_into_range(tmp_path):
    slow = _clicks(tmp_path / "slow.wav", 32.0, [1.0 + 1.5 * i for i in range(20)])
    fast = _clicks(tmp_path / "fast.wav", 16.0, [0.5 + 0.25 * i for i in range(60)])
    for p in (slow, fast):
        g = track_beats(p)
        assert 60 <= g.bpm() <= 180, (p.name, g.bpm())


def test_drums_stem_drives_tempo(tmp_path):
    times = [0.5 + 0.6 * i for i in range(30)]
    drums = _clicks(tmp_path / "drums.wav", 19.0, times, [i % 4 == 0 for i in range(30)])
    rng = np.random.default_rng(3)
    mix = _tones(tmp_path / "mix.wav", 19.0, sorted(rng.uniform(0, 18, 25)))
    g = track_beats(mix, drums)
    assert 95 <= g.bpm() <= 105
    _assert_covers(g, 19.0)


def test_silent_drums_stem_uses_mix(tmp_path):
    times = [0.5 + 0.5 * i for i in range(30)]
    mix = _clicks(tmp_path / "mix.wav", 16.0, times, [i % 4 == 0 for i in range(30)])
    drums = _write(tmp_path / "drums.wav", np.zeros(16 * SR))
    g = track_beats(mix, drums)
    assert 115 <= g.bpm() <= 125


def test_grid_is_json_safe(tmp_path):
    times = [1.0 + 0.5 * i for i in range(20)]
    g = track_beats(_clicks(tmp_path / "c.wav", 12.0, times))
    json.dumps(asdict(g))
    assert type(g.first_downbeat) is int and type(g.beats_per_bar) is int
    assert all(type(b) is float for b in g.beats)


def _midi(num=None, den=4, tempo=100.0, first=0.0, end=10.0):
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    if num:
        pm.time_signature_changes.append(pretty_midi.TimeSignature(num, den, 0))
    inst = pretty_midi.Instrument(0)
    step = 60.0 / tempo
    t = first
    while t < end - 1e-9:
        inst.notes.append(pretty_midi.Note(80, 72, t, t + step))
        t += step
    pm.instruments.append(inst)
    return pm


def test_grid_from_midi_three_four():
    g = grid_from_midi(_midi(3, 4, tempo=100))
    assert g.beats_per_bar == 3
    assert abs(g.bpm() - 100) < 0.5
    assert g.first_downbeat == 0
    _assert_covers(g, 10.0)


def test_grid_from_midi_time_signatures():
    def shape(num, den):
        g = grid_from_midi(_midi(num, den))
        return g.beats_per_bar, g.beat_ticks
    assert shape(None, 4) == (4, 48)                 # no signature: 4/4
    assert shape(2, 4) == (2, 48)
    assert shape(4, 4) == (4, 48)
    assert shape(6, 8) == (2, 72)                    # dotted-quarter beats
    assert shape(9, 8) == (3, 72)
    assert shape(12, 8) == (4, 72)
    assert shape(3, 8) == (3, 24)                    # eighth beats
    assert shape(5, 4) == (4, 48)                    # anything else: as before
    # pretty_midi's 6/8 beats are dotted quarters: 100 quarters a minute = 66.7 beats
    g = grid_from_midi(_midi(6, 8, tempo=100))
    assert abs(g.bpm() - 100 / 1.5) < 0.5
    assert abs(g.to_ticks(0.6) - 48) < 1e-6          # a quarter note (0.6 s) is 48 ticks


def test_grid_from_midi_pickup():
    # 4/4 at 100 bpm; the tune starts on beat 4 of the first bar -> one-beat pickup
    g = grid_from_midi(_midi(4, 4, tempo=100, first=1.8))
    assert abs(g.to_beats(1.8) + 1) < 1e-9
    assert abs(g.to_beats(2.4)) < 1e-9


def test_grid_from_empty_midi():
    g = grid_from_midi(pretty_midi.PrettyMIDI(initial_tempo=90))
    assert len(g.beats) >= 2
    assert 60 <= g.bpm() <= 180


def test_melody_long_notes_place_beat_one():
    from sheetswap.beats import melody_downbeat
    from sheetswap.model import BeatGrid, Note
    grid = BeatGrid([i * 0.5 for i in range(40)], 4, 0)
    # tune: a long note on beats 2, 6, 10, ... (a half note), short notes between
    mel = []
    for bar in range(9):
        t = (bar * 4 + 2) * 0.5
        mel.append(Note(76, t, t + 1.0, 90))
        mel.append(Note(74, t + 1.0, t + 1.5, 70))
        mel.append(Note(72, t + 1.5, t + 2.0, 70))
    g = melody_downbeat(mel, grid, music_start=1.0)
    assert g.first_downbeat == 2
    even = [Note(72, i * 0.5, i * 0.5 + 0.45, 80) for i in range(36)]
    assert melody_downbeat(even, grid, 0.0) is grid      # no evidence: unchanged
    one = BeatGrid(grid.beats, 1, 0, 72)                 # 3/8 a dotted beat a bar: no choice
    assert melody_downbeat(mel, one, 1.0) is one


# ---------------------------------------------------------------- beat subdivision

def _tune(patterns, period, bpb=4, jitter=0.03, seed=0, n_beats=32):
    """A melody on a steady grid: beat i plays the onsets (fractions of a beat)
    in patterns[i % len(patterns)], each late or early by up to `jitter` beats."""
    from sheetswap.model import BeatGrid, Note
    rng = np.random.default_rng(seed)
    grid = BeatGrid([i * period for i in range(n_beats + 4)], bpb, 0)
    notes = []
    for i in range(n_beats):
        offs = patterns[i % len(patterns)]
        ends = list(offs[1:]) + [1.0]
        for f, g in zip(offs, ends):
            t = (i + f + rng.uniform(-jitter, jitter)) * period
            notes.append(Note(72 + i % 5, t, (i + g) * period - 0.02, 80))
    return notes, grid


JIG = [(0, 1 / 3, 2 / 3), (0, 2 / 3), (0, 1 / 3, 2 / 3), (0,)]          # 6/8: 8ths, quarter-8th
MARCH = [(0, 1 / 2), (0,), (0, 1 / 4, 1 / 2, 3 / 4), (0, 3 / 4)]          # 4/4: 8ths, 16ths, dotted
SICILIANO = [(0, 1 / 2, 2 / 3), (0,)]                                    # dotted 8th, 16th, 8th


def test_subdivision_jig_vs_march():
    from sheetswap.beats import beat_subdivision, beat_unit
    jig, g = _tune(JIG, period=0.6)
    assert beat_subdivision(jig, g) == 3 and beat_unit(jig, g) == 72
    march, g = _tune(MARCH, period=0.5)
    assert beat_subdivision(march, g) == 2 and beat_unit(march, g) == 48
    sic, g = _tune(SICILIANO, period=0.9)
    assert beat_subdivision(sic, g) == 3
    # the grid's downbeat doesn't change where notes fall inside a beat
    g.first_downbeat = 3
    assert beat_subdivision(sic, g) == 3


def test_subdivision_ignores_a_lagging_grid():
    # tracked beats often come ~1/8 beat after the tune's notes: plain 8ths
    # would then sit near 1/3 of a beat, 8ths of a jig near 1/4
    from sheetswap.beats import beat_subdivision
    for pattern, period, want in ((MARCH, 0.5, 2), (JIG, 0.6, 3)):
        for lag in (-0.12, 0.12):
            notes, g = _tune(pattern, period)
            g.beats = [b + lag * period for b in g.beats]
            assert beat_subdivision(notes, g) == want, (want, lag)


def test_subdivision_needs_enough_beats_in_three():
    from sheetswap.beats import COMPOUND_MIN_BEATS, COMPOUND_SHARE, beat_subdivision
    # a 4/4 tune of 8ths with a triplet every 4th beat: triplets stay a minority
    some, g = _tune([(0, 1 / 2), (0, 1 / 2), (0, 1 / 3, 2 / 3), (0,)], period=0.5)
    assert 1 / 3 < COMPOUND_SHARE and beat_subdivision(some, g) == 2
    # all triplets, but too few of them to judge
    few, g = _tune([(0, 1 / 3, 2 / 3)], period=0.5, n_beats=COMPOUND_MIN_BEATS - 1)
    assert beat_subdivision(few, g) == 2
    enough, g = _tune([(0, 1 / 3, 2 / 3)], period=0.5, n_beats=COMPOUND_MIN_BEATS)
    assert beat_subdivision(enough, g) == 3
    # no notes between the beats: nothing says three
    plain, g = _tune([(0,)], period=0.5)
    assert beat_subdivision(plain, g) == 2
    assert beat_subdivision([], g) == 2


def test_fast_three_beat_bars_in_two_become_eighth_beats():
    from sheetswap.beats import EIGHTH_BEAT_BPM, beat_unit
    elise = [(0, 1 / 2), (0, 1 / 2), (0, 1 / 2)]            # running 16ths of 3/8
    fast = 60 / (EIGHTH_BEAT_BPM + 10)
    slow = 60 / (EIGHTH_BEAT_BPM - 10)
    notes, g = _tune(elise, period=fast, bpb=3)
    assert beat_unit(notes, g) == 24                         # 3/8 in eighth beats
    notes, g = _tune(elise, period=slow, bpb=3)
    assert beat_unit(notes, g) == 48                         # a minuet in 3/4
    notes, g = _tune(elise, period=fast, bpb=4)
    assert beat_unit(notes, g) == 48                         # bars of four: stays 4/4
    notes, g = _tune([(0,), (0,), (0,)], period=fast, bpb=3)
    assert beat_unit(notes, g) == 48                         # a fast waltz on the beats
    # a neural tracker puts Für Elise at exactly 150 eighths a minute
    assert EIGHTH_BEAT_BPM <= 130
    notes, g = _tune(elise, period=60 / 150, bpb=3)
    assert abs(g.bpm() - 150) < 1e-6 and beat_unit(notes, g) == 24


def test_accompaniment_settles_an_ambiguous_tune():
    # a compound tune that's ambiguous alone: 33 beats in three, 35 in two
    # (share 0.49, under COMPOUND_SHARE); the left hand's 8ths are in threes
    from sheetswap.beats import COMPOUND_SHARE, _beat_splits, beat_subdivision, beat_unit
    tune_pat = [(0, 1 / 3, 2 / 3)] * 33 + [(0, 1 / 2)] * 35 + [(0,)] * 12
    tune, g = _tune(tune_pat, period=60 / 39.5, n_beats=len(tune_pat))
    [(three, two, _)] = _beat_splits([tune], g)
    assert (three, two) == (33, 35) and three < COMPOUND_SHARE * (three + two)
    assert beat_subdivision(tune, g) == 2
    # the real Op. 9/2 tune (39 in three, 29 in two, share 0.57) is compound by itself
    chopin, gc = _tune([(0, 1 / 3, 2 / 3)] * 39 + [(0, 1 / 2)] * 29 + [(0,)] * 12,
                       period=60 / 39.5, n_beats=80)
    assert beat_subdivision(chopin, gc) == 3
    left, _ = _tune([(0, 1 / 3, 2 / 3)], period=60 / 39.5, n_beats=len(tune_pat), seed=1)
    assert beat_subdivision(tune, g, accompaniment=left) == 3
    assert beat_unit(tune, g, left) == 72
    # a simple-time piece (Träumerei-like) keeps its 8ths with any accompaniment
    march, g = _tune(MARCH, period=0.8)
    eighths, _ = _tune([(0, 1 / 2)], period=0.8, seed=2)
    assert beat_subdivision(march, g, accompaniment=eighths) == 2
    assert beat_subdivision(march, g, accompaniment=[]) == 2
    # Ave Maria: a tune in 8ths and dotted rhythms over triplets stays in simple time
    ave = [(0, 1 / 2), (0, 3 / 4), (0,), (0, 1 / 3, 2 / 3)]
    tune, g = _tune(ave, period=1.0, n_beats=48)
    triplets, _ = _tune([(0, 1 / 3, 2 / 3)], period=1.0, n_beats=48, seed=3)
    assert beat_subdivision(tune, g, accompaniment=triplets) == 2
    # ...while a tune with no notes between the beats lets the accompaniment decide
    held, g = _tune([(0,)], period=1.0, n_beats=48)
    assert beat_subdivision(held, g, accompaniment=triplets) == 3
