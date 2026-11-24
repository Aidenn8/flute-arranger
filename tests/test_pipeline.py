import json

import pretty_midi
import pytest

from sheetswap import pipeline
from sheetswap.config import FLUTE_HARD
from sheetswap.model import Analysis, ArrangeOptions, BeatGrid, Note

MELODY = [72, 74, 76, 77, 79, 77, 76, 74, 72, 76, 79, 84, 79, 76, 74, 72]


def _song(tmp_path, bpm=100):
    """Melody track (quarter notes) over a block-chord piano track, 4 bars."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    beat = 60 / bpm
    mel = pretty_midi.Instrument(program=73, name="Melody")
    for i, p in enumerate(MELODY):
        mel.notes.append(pretty_midi.Note(90, p, i * beat, (i + 0.95) * beat))
    acc = pretty_midi.Instrument(program=0, name="Piano")
    for bar, chord in enumerate([(48, 52, 55), (53, 57, 60), (55, 59, 62), (48, 52, 55)]):
        for p in chord:
            acc.notes.append(pretty_midi.Note(70, p, bar * 4 * beat, (bar + 1) * 4 * beat - 0.05))
    pm.instruments += [mel, acc]
    d = tmp_path / "song1"
    d.mkdir()
    pm.write(str(d / "input.mid"))
    (d / "meta.json").write_text(json.dumps({"title": "Test Song"}))
    return d


def test_midi_end_to_end_solo_and_duet(tmp_path):
    d = _song(tmp_path)
    an = pipeline.analyze(d)
    assert an.kind == "midi" and an.title == "Test Song"
    for mode in ("solo", "duet"):
        res = pipeline.arrange_and_render(d, ArrangeOptions(mode=mode))
        arr = res["arr"]
        assert res["svgs"] and "<svg" in res["svgs"][0]
        assert res["musicxml"].exists() and res["midi"].exists()
        assert res["warnings"] == []
        assert [q.pitch % 12 for q in arr.flute] == [p % 12 for p in MELODY]
        assert all(FLUTE_HARD[0] <= q.pitch <= FLUTE_HARD[1] for q in arr.flute)
        if mode == "duet":
            assert arr.rh and arr.lh
        else:
            assert not arr.rh and not arr.lh


def test_corrupt_caches_are_rebuilt(tmp_path):
    d = _song(tmp_path)
    pipeline.analyze(d)
    opts = ArrangeOptions()
    pipeline.arrange_and_render(d, opts)
    (d / "arr" / opts.key() / "info.json").write_text('{"pages": 1, "musi')   # killed mid-write
    (d / "analysis.json").write_text('{"song_id"')
    assert pipeline.analyze(d).kind == "midi"
    assert pipeline.arrange_and_render(d, opts)["svgs"]


def test_duet_without_chords_says_so():
    an = _analysis({"midi_melody": [Note(72, 0.0, 0.5)], "midi_all": [Note(72, 0.0, 0.5)]})
    arr = pipeline.arrange(an, ArrangeOptions(mode="duet"))
    assert not arr.rh and not arr.lh
    assert any("flute alone" in w for w in arr.warnings)


def test_render_is_cached(tmp_path):
    d = _song(tmp_path)
    pipeline.analyze(d)
    opts = ArrangeOptions()
    pipeline.arrange_and_render(d, opts)
    info = d / "arr" / opts.key() / "info.json"
    before = info.stat().st_mtime_ns
    pipeline.arrange_and_render(d, opts)
    assert info.stat().st_mtime_ns == before


def test_easy_key_and_octave_options(tmp_path):
    d = _song(tmp_path)
    an = pipeline.analyze(d)
    base = pipeline.arrange(an, ArrangeOptions())
    assert base.key_fifths == 0 and base.key_mode == "major"
    up = pipeline.arrange(an, ArrangeOptions(octave=1))
    lo = pipeline.arrange(an, ArrangeOptions(octave=-1))
    assert {q.pitch for q in up.flute} != {q.pitch for q in lo.flute}
    for arr in (up, lo):
        assert all(FLUTE_HARD[0] <= q.pitch <= FLUTE_HARD[1] for q in arr.flute)
    three = pipeline.arrange(an, ArrangeOptions(meter=3))
    assert three.beats_per_bar == 3 and base.beats_per_bar == 4
    shifted = pipeline.arrange(an, ArrangeOptions(barshift=1))
    assert [q.start for q in shifted.flute] == [q.start - 12 for q in base.flute]


def _analysis(sources, duration=8.0):
    return Analysis("x", "song", duration, BeatGrid([i * 0.5 for i in range(20)], 4, 0), sources)


def test_bar_shift_keeps_pickup_under_a_bar():
    # music starts 3 beats before the first downbeat (a 3-beat pickup in 4/4)
    grid = BeatGrid([i * 0.5 for i in range(40)], 4, 3)
    piano = [Note(72 + (i % 4), i * 0.5, i * 0.5 + 0.45, 90) for i in range(30)]
    an = Analysis("x", "piano", 20.0, grid, {"piano": piano})
    for shift in range(4):
        arr = pipeline.arrange(an, ArrangeOptions(barshift=shift))
        first = min(q.start for q in arr.flute)
        assert -48 < first <= 0, (shift, first)          # never a whole empty bar
    starts = {s: min(q.start for q in pipeline.arrange(an, ArrangeOptions(barshift=s)).flute)
              for s in range(4)}
    assert starts == {0: -36, 1: 0, 2: -12, 3: -24}


def test_pick_melody_prefers_singing():
    vocals = [Note(67 + (i % 3), i * 0.5, i * 0.5 + 0.45) for i in range(14)]
    other = [Note(84, i * 0.5, i * 0.5 + 0.45) for i in range(14)]
    notes, used, warn = pipeline.pick_melody(_analysis({"vocals": vocals, "other": other}), "auto")
    assert used == "vocals" and warn == []
    assert [n.pitch for n in notes] == [n.pitch for n in vocals]


def test_pick_melody_missing_source_warns():
    other = [Note(72, i * 0.5, i * 0.5 + 0.45) for i in range(8)]
    _, used, warn = pipeline.pick_melody(_analysis({"other": other}), "vocals")
    assert used == "other" and warn


def test_unheard_notes_and_cover():
    piano = [Note(60 + (i % 2) * 4, i * 0.5, i * 0.5 + 0.45) for i in range(20)]
    # the general model hears the piano (slightly off) plus a held violin line
    general = [Note(n.pitch + (1 if i % 5 == 0 else 0), n.start + 0.05, n.end) for i, n in enumerate(piano)]
    violin = [Note(76 + (i % 3), i * 1.0, i * 1.0 + 0.95) for i in range(10)]
    assert pipeline.unheard_notes(general, piano) == []
    assert pipeline.unheard_notes(general + violin, piano) == violin
    assert pipeline.other_instrument_cover(general, piano, 10.0) == 0.0
    assert pipeline.other_instrument_cover(general + violin, piano, 10.0) > pipeline.ENSEMBLE_COVER


def test_ensemble_takes_tune_from_other_instrument():
    piano = [Note(48 + (i % 3) * 4, i * 0.5, i * 0.5 + 0.45) for i in range(14)]
    other = [Note(79, i * 0.5, i * 0.5 + 0.45) for i in range(14)]
    an = _analysis({"piano": piano, "other": other})
    an.kind = "ensemble"
    _, used, _ = pipeline.pick_melody(an, "auto")
    assert used == "other"
    an.kind = "piano"
    _, used, _ = pipeline.pick_melody(an, "auto")
    assert used == "piano"


def test_empty_midi_raises(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    pretty_midi.PrettyMIDI().write(str(d / "input.mid"))
    with pytest.raises(ValueError):
        pipeline.analyze(d)
