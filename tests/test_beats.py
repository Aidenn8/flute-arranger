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


def test_grid_from_midi_defaults_to_four():
    assert grid_from_midi(_midi(None)).beats_per_bar == 4
    assert grid_from_midi(_midi(6, 8)).beats_per_bar == 4


def test_grid_from_midi_pickup():
    # 4/4 at 100 bpm; the tune starts on beat 4 of the first bar -> one-beat pickup
    g = grid_from_midi(_midi(4, 4, tempo=100, first=1.8))
    assert abs(g.to_beats(1.8) + 1) < 1e-9
    assert abs(g.to_beats(2.4)) < 1e-9


def test_grid_from_empty_midi():
    g = grid_from_midi(pretty_midi.PrettyMIDI(initial_tempo=90))
    assert len(g.beats) >= 2
    assert 60 <= g.bpm() <= 180
