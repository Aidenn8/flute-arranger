"""The learned tune picker (melody.learned_tune): features, model loading and
fallback, the line step, and finding the tune in small synthetic pieces."""
import json
import pickle
import random

import numpy as np
import pytest

from sheetswap import melody, pipeline
from sheetswap.melody import (FEATURES, LearnedLine, fill_tune, learned_tune, piano_melody,
                              tune_features, tune_model)
from sheetswap.model import Analysis, BeatGrid, Note


def _piece(seed, tune_low=False, voiced=True, ghosts=True):
    """(notes, tune): a stepwise tune in quarter notes (0.5 s) over a bass and
    chord tones; tune_low puts the tune in the middle with soft after-beat
    chords above it (Schumann's Happy Farmer). A soft octave ghost hangs over
    some tune notes, as the piano model hears them."""
    rng = random.Random(seed)
    p = 62 if tune_low else 74
    tune, notes = [], []
    for i in range(40):
        p = min(max(p + rng.choice([-2, -1, 1, 2, 0, 3, -3]), 60 if tune_low else 70), 67 if tune_low else 82)
        s = i * 0.5 + rng.uniform(-0.01, 0.01)
        v = rng.randint(80, 92) if voiced else rng.randint(58, 66)
        tune.append(Note(p, s, s + 0.48, v))
        if rng.random() < 0.25 and ghosts:
            notes.append(Note(p + 12, s + 0.01, s + 0.3, 30))
        notes.append(Note(p - rng.randint(15, 20), s, s + 0.45, rng.randint(50, 62)))
        notes.append(Note(p - rng.randint(5, 9), s + 0.01, s + 0.45, rng.randint(48, 60)))
        if tune_low:
            for q in (72, 76):
                notes.append(Note(q + rng.choice([0, 1, 2]), s + 0.25, s + 0.45, rng.randint(40, 50)))
    return notes + tune, tune


def _found(line, tune):
    got = {(n.pitch, round(n.start, 3)) for n in line}
    return sum((n.pitch, round(n.start, 3)) in got for n in tune)


def _train_tiny(tmp_path):
    """A small model trained on synthetic pieces, saved like the real one."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    X, y = [], []
    for seed in range(8):
        notes, tune = _piece(seed, tune_low=seed % 2 == 1, voiced=seed % 4 != 3)
        ns, F = tune_features(notes)
        keys = {(n.pitch, round(n.start, 3)) for n in tune}
        X.append(F)
        y += [(n.pitch, round(n.start, 3)) in keys for n in ns]
    clf = HistGradientBoostingClassifier(max_iter=40, random_state=0).fit(np.concatenate(X), np.array(y))
    (tmp_path / "model.pkl").write_bytes(pickle.dumps(clf))
    (tmp_path / "meta.json").write_text(json.dumps(dict(features=FEATURES, line=dict(bias=0.0, step=0.03,
                                                                                         leap=0.5))))
    return tmp_path


@pytest.fixture
def no_model_cache(monkeypatch):
    monkeypatch.setattr(melody, "_MODEL_CACHE", {})


def test_features_one_row_per_note_in_onset_order():
    notes, _ = _piece(1)
    notes.append(Note(notes[0].pitch, notes[0].start + 0.02, notes[0].end, 40))   # same key, same moment
    ns, X = tune_features(notes)
    assert X.shape == (len(ns), len(FEATURES)) and np.isfinite(X).all()
    assert len(ns) == len(notes) - 1                          # the unison became one note
    assert [(n.start, -n.pitch) for n in ns] == sorted((n.start, -n.pitch) for n in ns)
    assert tune_features([])[1].shape == (0, len(FEATURES))


def test_features_say_what_they_mean():
    chord = [Note(60, 0.0, 1.0, 60), Note(64, 0.01, 1.0, 62), Note(72, 0.0, 1.0, 90),
             Note(84, 0.02, 0.5, 30)]                         # 84: an octave over the louder 72
    ns, X = tune_features(chord + [Note(74, 1.0, 2.0, 90)])
    f = {(n.pitch, n.start): dict(zip(FEATURES, row)) for n, row in zip(ns, X)}
    assert f[(84, 0.02)]["is_top"] == 1 and f[(84, 0.02)]["octave_over_louder"] == 1
    assert f[(72, 0.0)]["above_in_cluster"] == 1 and f[(72, 0.0)]["octave_over_louder"] == 0
    assert f[(60, 0.0)]["below_in_cluster"] == 0 and f[(60, 0.0)]["cluster_size"] == 4
    assert f[(72, 0.0)]["vel_under_cluster_max"] == 0
    assert f[(74, 1.0)]["to_prev_top"] == 74 - 84 and f[(74, 1.0)]["near_prev"] == 72 - 74


def test_likely_line_one_note_per_moment_and_smooth():
    p = np.array([72, 60, 74, 79, 76], float)
    s = np.array([0.0, 0.0, 0.5, 0.5, 1.0])
    z = np.array([2.0, 1.0, 1.5, 1.6, 2.0])
    path = melody._likely_line(p, s, z, step=0.1, leap=0.5)
    assert path == [0, 2, 4]                                  # 79 is likelier alone, but 74 moves by step
    assert melody._likely_line(p, s, -np.ones(5), 0.1, 0.5) == []


def test_missing_model_falls_back_to_the_rules(tmp_path, monkeypatch, no_model_cache):
    monkeypatch.setattr(melody, "TUNE_MODEL_DIR", tmp_path / "none")
    monkeypatch.setenv(melody.PICKER_ENV, "learned")
    notes, _ = _piece(3)
    assert tune_model() is None and not melody.use_learned_picker()
    assert [(n.pitch, n.start) for n in learned_tune(notes)] == [(n.pitch, n.start) for n in piano_melody(notes)]
    an = Analysis("x", "piano", 20.0, BeatGrid([i * 0.5 for i in range(44)], 4, 0), {"piano": notes})
    line, used, _ = pipeline.pick_melody(an, "auto")
    assert used == "piano" and not isinstance(line, LearnedLine)


def test_model_for_other_features_or_broken_is_not_used(tmp_path, no_model_cache):
    (tmp_path / "meta.json").write_text(json.dumps(dict(features=["pitch"])))
    (tmp_path / "model.pkl").write_bytes(b"not a model")
    assert tune_model(tmp_path) is None
    (tmp_path / "meta.json").write_text(json.dumps(dict(features=FEATURES)))
    melody._MODEL_CACHE.clear()
    assert tune_model(tmp_path) is None


def test_picker_choice(monkeypatch):
    monkeypatch.setenv(melody.PICKER_ENV, "Learned ")
    assert melody.tune_picker() == "learned"
    monkeypatch.setenv(melody.PICKER_ENV, "rules")
    assert melody.tune_picker() == "rules"
    monkeypatch.setenv(melody.PICKER_ENV, "something")
    assert melody.tune_picker() == melody.PICKER_DEFAULT


def test_learned_picker_finds_the_tune(tmp_path, no_model_cache):
    model = tune_model(_train_tiny(tmp_path))
    assert model is not None
    for seed, low in ((100, False), (101, True)):            # pieces it was not trained on
        notes, tune = _piece(seed, tune_low=low)
        line = learned_tune(notes, model)
        assert isinstance(line, LearnedLine)
        assert _found(line, tune) >= 0.9 * len(tune), (low, _found(line, tune))
        assert len(line) <= 1.1 * len(tune)
        assert all(a.end <= b.start + 1e-9 and a.start < b.start for a, b in zip(line, line[1:]))
        assert fill_tune(line, notes) == line                 # the rules don't add to it


def test_pipeline_uses_the_learned_picker_for_piano_recordings(tmp_path, monkeypatch, no_model_cache):
    monkeypatch.setattr(melody, "TUNE_MODEL_DIR", _train_tiny(tmp_path))
    monkeypatch.setenv(melody.PICKER_ENV, "learned")
    notes, tune = _piece(102, ghosts=False)
    an = Analysis("x", "piano", 20.0, BeatGrid([i * 0.5 for i in range(44)], 4, 0), {"piano": notes})
    voiced, used, _ = pipeline.pick_melody(an, "auto")       # bar lines are found on the voiced notes
    assert used == "piano" and isinstance(voiced, melody.VoicedLine)
    assert [(n.pitch, n.start) for n in voiced] == [(n.pitch, n.start) for n in piano_melody(notes)]
    line = fill_tune(voiced, notes)                          # ... and the learned tune is written
    assert isinstance(line, LearnedLine) and _found(line, tune) >= 0.9 * len(tune)
    arr = pipeline.arrange(an, pipeline.ArrangeOptions())
    assert len(arr.flute) >= 0.9 * len(tune)
    monkeypatch.setenv(melody.PICKER_ENV, "rules")
    plain = pipeline.pick_melody(an, "auto")[0]
    assert not isinstance(plain, (melody.VoicedLine, LearnedLine))
    assert not isinstance(fill_tune(plain, notes), LearnedLine)


def test_evenly_played_music_keeps_the_rules(tmp_path, monkeypatch, no_model_cache):
    # no voiced tune (a sequencer's chorale): the learned picker is not used
    monkeypatch.setattr(melody, "TUNE_MODEL_DIR", _train_tiny(tmp_path))
    monkeypatch.setenv(melody.PICKER_ENV, "learned")
    notes, _ = _piece(103, voiced=False)
    notes = [Note(n.pitch, n.start, n.end, 64) for n in notes]
    assert melody.projected_line(notes) is None
    an = Analysis("x", "piano", 20.0, BeatGrid([i * 0.5 for i in range(44)], 4, 0), {"piano": notes})
    voiced, _, _ = pipeline.pick_melody(an, "auto")
    line = fill_tune(voiced, notes)
    assert not isinstance(line, LearnedLine)
    assert [(n.pitch, n.start) for n in line] == [(n.pitch, n.start) for n in melody.top_voice(notes)]


@pytest.mark.skipif(not (melody.TUNE_MODEL_DIR / "model.pkl").exists(),
                    reason="no trained model (bench/train_tune_picker.py)")
def test_trained_model_finds_a_voiced_tune(no_model_cache):
    notes, tune = _piece(7)
    line = learned_tune(notes)
    assert isinstance(line, LearnedLine)
    assert _found(line, tune) >= 0.85 * len(tune) and len(line) <= 1.2 * len(tune)
