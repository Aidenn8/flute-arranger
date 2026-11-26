"""The learned rhythm reader (rhythm.quantize_line_learned): features, decoding,
model loading and fallback, and the reader switch (SHEETSWAP_RHYTHM)."""
import json

import numpy as np
import pytest

from sheetswap import config, rhythm
from sheetswap.model import DOTTED_BEAT, EIGHTH_BEAT, QUARTER_BEAT, BeatGrid, Note


def _steady(period=0.5, n=48, bpb=4):
    return BeatGrid([period * i for i in range(n)], bpb, 0)


def _jittered_eighths(n=24, period=0.5, seed=0, jitter=0.03):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        t = period / 2 * i + rng.uniform(-jitter, jitter)
        out.append(Note(70 + (i % 4), t, t + period / 2 - 0.03, 70 + int(rng.integers(0, 20))))
    return out


class _NearestEighth:
    """Stands in for the network: each note's place is the 8th nearest to where it was
    heard (the fraction-of-beat input), no rest after it, never an extra note."""

    def __call__(self, x):
        import torch
        f = x[..., 8]
        n = x.shape[1]
        place = torch.full((1, n, rhythm.N_PLACES), -20.0)
        k_on, k_half = rhythm.TWELFTHS.index(0), rhythm.TWELFTHS.index(6)
        here, after = len(rhythm.TWELFTHS), 2 * len(rhythm.TWELFTHS)
        for i in range(n):
            v = float(f[0, i])
            c = here + k_on if v < 0.25 else here + k_half if v < 0.75 else after + k_on
            place[0, i, c] = 10.0
        rest = torch.full((1, n, rhythm.N_RESTS), -20.0)
        rest[..., 0] = 10.0
        return place, rest, torch.full((1, n), -10.0)


def test_place_class_round_trip():
    for unit in (QUARTER_BEAT, DOTTED_BEAT, EIGHTH_BEAT):
        mask = rhythm.place_mask(unit)
        for c in np.flatnonzero(mask):
            t = rhythm.place_ticks(int(c), 7, unit)
            assert rhythm.place_class(t, 7, unit) == c
            assert 6 * unit <= t < 9 * unit
    assert rhythm.place_class(7 * 48 + 6, 7, 48) is None        # a 32nd is no place
    assert rhythm.place_class(3 * 48, 7, 48) is None            # too far away
    assert rhythm.rest_class(0) == 0 and rhythm.rest_class(30) == rhythm.LONG_REST
    assert rhythm.rest_class(5) is None


def test_masks_follow_the_beat_kind():
    quarter = rhythm.place_mask(QUARTER_BEAT)
    tw = [rhythm.TWELFTHS[c % len(rhythm.TWELFTHS)] for c in np.flatnonzero(quarter)]
    assert set(tw) == {0, 3, 4, 6, 8, 9}                        # 16ths and 8th triplets
    simple = [rhythm.TWELFTHS[c % len(rhythm.TWELFTHS)] for c in np.flatnonzero(rhythm.place_mask(DOTTED_BEAT, True))]
    assert set(simple) == {0, 4, 8}                             # 8ths of a dotted beat
    assert rhythm.rest_mask(EIGHTH_BEAT)[rhythm.LONG_REST]


def test_features_shape_and_values():
    g = _steady()
    notes = _jittered_eighths(jitter=0.0)
    merged, grace = rhythm._merged(notes, g)
    f = rhythm.learned_features(merged, grace, g)
    assert f.shape == (len(merged), rhythm.N_FEATURES)
    assert f.dtype == np.float32 and np.isfinite(f).all()
    assert np.allclose(f[0::2, 8], 0.0, atol=1e-6)              # on the beat
    assert np.allclose(f[1::2, 8], 0.5, atol=1e-6)              # half way through it
    said = f[:, -(rhythm.N_PLACES + 1):]
    assert np.allclose(said.sum(axis=1), 1.0)                   # the rules' place, one-hot
    unit_cols = f[:, 24:27]
    assert np.allclose(unit_cols, [1, 0, 0])                    # a quarter beat
    assert rhythm.learned_features([], [], g).shape == (0, rhythm.N_FEATURES)


def test_decode_keeps_order_and_leaves_out_a_double():
    n = 4
    lp = np.full((n, rhythm.N_PLACES), -20.0)
    here = len(rhythm.TWELFTHS)
    for i, k in enumerate((0, 6, 6, 0)):                       # notes 1 and 2 both want the 8th
        lp[i, here + rhythm.TWELFTHS.index(k)] = 0.0
    beats = [0, 0, 0, 1]
    l_extra = np.log(np.full(n, 0.05))
    l_keep = np.log(np.full(n, 0.95))
    ticks = rhythm.decode_places(lp, l_extra, l_keep, beats, QUARTER_BEAT)
    kept = [t for t in ticks if t is not None]
    assert kept == sorted(set(kept))
    assert ticks[0] == 0 and ticks[3] == 48
    assert ticks.count(None) == 1 and 24 in kept


def test_jittered_eighths_come_out_as_eighths_with_a_stand_in_network():
    g = _steady()
    notes = _jittered_eighths()
    q = rhythm.quantize_line_learned(notes, g, model=(_NearestEighth(), {"extra_weight": 1.0}))
    assert [n.start for n in q] == [24 * i for i in range(len(notes))]
    assert all(n.dur == 24 for n in q[:-1])
    assert [n.pitch for n in q] == [n.pitch for n in notes]


def test_jittered_eighths_come_out_as_eighths_with_the_trained_model():
    model = rhythm.load_learned()
    if model is None:
        pytest.skip("no trained model in models/rhythm (bench/train_rhythm.py train)")
    g = _steady()
    for seed in range(3):
        notes = _jittered_eighths(seed=seed)
        q = rhythm.quantize_line_learned(notes, g, model=model)
        assert [n.start for n in q] == [24 * i for i in range(len(notes))]


def test_falls_back_to_the_rules_without_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rhythm, "_learned_cache", {})
    assert rhythm.load_learned() is None
    g = _steady()
    notes = _jittered_eighths(seed=3, jitter=0.06)
    assert rhythm.quantize_line_learned(notes, g) == rhythm.quantize_line(notes, g)


def test_a_damaged_model_is_not_loaded(tmp_path, monkeypatch):
    d = tmp_path / "rhythm"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"features": rhythm.N_FEATURES, "hidden": 8, "layers": 1}))
    (d / "weights.pt").write_bytes(b"not a model")
    monkeypatch.setattr(rhythm, "_learned_cache", {})
    assert rhythm.load_learned(d) is None


def test_a_saved_model_loads(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    d = tmp_path / "rhythm"
    d.mkdir()
    net = rhythm.build_net(rhythm.N_FEATURES, 8, 1)
    torch.save(net.state_dict(), d / "weights.pt")
    (d / "config.json").write_text(json.dumps({"features": rhythm.N_FEATURES, "hidden": 8, "layers": 1}))
    monkeypatch.setattr(rhythm, "_learned_cache", {})
    loaded = rhythm.load_learned(d)
    assert loaded is not None and loaded[1]["hidden"] == 8
    q = rhythm.quantize_line_learned(_jittered_eighths(), _steady(), model=loaded)
    starts = [n.start for n in q]
    assert starts == sorted(set(starts))                       # whatever it reads, in order
    assert all(a.end <= b.start for a, b in zip(q, q[1:]))


def test_reader_switch(monkeypatch):
    calls = []
    monkeypatch.setattr(rhythm, "quantize_line_learned", lambda n, g, s=False: calls.append("learned") or [])
    g, notes = _steady(), _jittered_eighths()
    monkeypatch.delenv("SHEETSWAP_RHYTHM", raising=False)
    monkeypatch.setattr(rhythm, "RHYTHM_DEFAULT", "rules")
    assert rhythm.rhythm_reader() == "rules"
    assert rhythm.quantize_tune(notes, g) == rhythm.quantize_line(notes, g)
    monkeypatch.setenv("SHEETSWAP_RHYTHM", "learned")
    assert rhythm.rhythm_reader() == "learned"
    rhythm.quantize_tune(notes, g)
    assert calls == ["learned"]
    rhythm.quantize_tune(notes, g, recording=False)             # a MIDI file keeps the rules
    rhythm.quantize_tune(notes, g, source="other")              # so does a violin's tune
    assert calls == ["learned"]
    monkeypatch.setenv("SHEETSWAP_RHYTHM", "nonsense")
    assert rhythm.rhythm_reader() == "rules"


def test_leaning_to_the_rules():
    lp = np.log(np.full((2, rhythm.N_PLACES), 1.0 / rhythm.N_PLACES))
    here = len(rhythm.TWELFTHS)
    out = rhythm.lean_to_rules(lp, [24, None], [0, 0], QUARTER_BEAT, 2.0)
    c = here + rhythm.TWELFTHS.index(6)
    assert out[0, c] == pytest.approx(lp[0, c] + 2.0)
    assert np.array_equal(out[1], lp[1]) and np.array_equal(np.delete(out[0], c), np.delete(lp[0], c))
    assert rhythm.lean_to_rules(lp, [24, None], [0, 0], QUARTER_BEAT, 0.0) is lp
