import json
from pathlib import Path

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
    (d / "arr" / f"v{pipeline.RENDER_VERSION}-{opts.key()}" / "info.json").write_text('{"pages": 1, "musi')   # killed mid-write
    (d / "analysis.json").write_text('{"song_id"')
    assert pipeline.analyze(d).kind == "midi"
    assert pipeline.arrange_and_render(d, opts)["svgs"]


def test_tune_without_accompaniment_gets_harmonized():
    # a melody-only file: the piano part is made from the tune itself (C major)
    tune = [Note(p, i * 0.5, i * 0.5 + 0.45) for i, p in enumerate(
        [72, 74, 76, 77, 79, 77, 76, 74, 72, 76, 79, 84, 79, 76, 74, 72])]
    an = _analysis({"midi_melody": tune, "midi_all": list(tune)})
    arr = pipeline.arrange(an, ArrangeOptions(mode="duet"))
    assert arr.rh and arr.lh
    assert not any("flute alone" in w for w in arr.warnings)
    assert {q.pitch % 12 for q in arr.lh} <= {0, 2, 4, 5, 7, 9, 11}        # diatonic bass
    flute_top = {q.start: q.pitch for q in arr.flute}
    assert all(q.pitch < min(flute_top.values()) + 12 for q in arr.rh)


def test_duet_with_almost_nothing_says_so():
    an = _analysis({"midi_melody": [Note(72, 0.0, 0.5)], "midi_all": [Note(72, 0.0, 0.5)]})
    arr = pipeline.arrange(an, ArrangeOptions(mode="duet"))
    if not arr.rh and not arr.lh:
        assert any("flute alone" in w for w in arr.warnings)


def test_render_is_cached(tmp_path):
    d = _song(tmp_path)
    pipeline.analyze(d)
    opts = ArrangeOptions()
    pipeline.arrange_and_render(d, opts)
    info = d / "arr" / f"v{pipeline.RENDER_VERSION}-{opts.key()}" / "info.json"
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
    three = pipeline.arrange(an, ArrangeOptions(meter="3/4"))
    assert three.beats_per_bar == 3 and base.beats_per_bar == 4
    shifted = pipeline.arrange(an, ArrangeOptions(barshift=1))
    assert [q.start for q in shifted.flute] == [q.start - 12 for q in base.flute]


def _analysis(sources, duration=8.0):
    return Analysis("x", "song", duration, BeatGrid([i * 0.5 for i in range(20)], 4, 0), sources)


def test_rescale_tempo_half_and_double():
    g = BeatGrid([i * 0.25 for i in range(16)], 4, 2)        # 240 BPM, downbeat at 0.5 s
    half = pipeline.rescale_tempo(g, -1)
    assert abs(half.bpm() - 120) < 1e-6 and half.to_seconds(0) == 0.5
    double = pipeline.rescale_tempo(BeatGrid([i * 0.5 for i in range(8)], 4, 1), 1)
    assert abs(double.bpm() - 240) < 1e-6 and double.to_seconds(0) == 0.5
    assert pipeline.rescale_tempo(g, 0) is g


def test_half_tempo_doubles_note_values(tmp_path):
    an = pipeline.analyze(_song(tmp_path))
    base = pipeline.arrange(an, ArrangeOptions())
    half = pipeline.arrange(an, ArrangeOptions(tempo=-1))
    assert [q.dur for q in half.flute][:4] == [q.dur // 2 for q in base.flute][:4]
    assert half.bpm == base.bpm // 2


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


def test_split_held_notes_rejoined_for_other_instruments_only():
    # a violin's held quarter notes heard as two pieces each (0.5 s beats); a piano's
    # quick repeated notes are real key strikes
    split = [n for i in range(8) for n in (Note(76 + i % 3, i * 0.5, i * 0.5 + 0.1, 80),
                                           Note(76 + i % 3, i * 0.5 + 0.1, i * 0.5 + 0.45, 70))]
    notes, used, _ = pipeline.pick_melody(_analysis({"other": split}), "other")
    assert used == "other"
    assert [(n.pitch, n.start, n.end) for n in notes] == [
        (76 + i % 3, i * 0.5, i * 0.5 + 0.45) for i in range(8)]
    notes, used, _ = pipeline.pick_melody(_analysis({"piano": split}), "piano")
    assert used == "piano" and len(notes) == 16


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


# ---------------------------------------------------------------- compound meters

# A jig in 6/8, in 8ths (3 = a dotted quarter): an 8th pickup, then 8 bars.
JIG = [(74, 1),
       (79, 1), (78, 1), (79, 1), (81, 2), (79, 1),      # bar 1: 8th 8th 8th | quarter 8th
       (78, 3), (76, 1), (74, 1), (76, 1),               # bar 2: dotted quarter | 8th 8th 8th
       (79, 1), (81, 1), (83, 1), (84, 3),
       (83, 2), (81, 1), (79, 3),
       (79, 1), (78, 1), (79, 1), (81, 2), (79, 1),
       (78, 3), (76, 1), (74, 1), (76, 1),
       (79, 1), (81, 1), (83, 1), (81, 1), (79, 1), (78, 1),
       (79, 6)]


def _midi_song(tmp_path, tune, num, den, quarter_bpm, name="jig"):
    """A melody (pitch, length in 8ths) whose first note is a one-8th pickup
    to bar 2, in the given time signature, over a held bass note a bar."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=quarter_bpm)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(num, den, 0.0))
    eighth = 30 / quarter_bpm
    bar = num * eighth * (8 // den)
    mel = pretty_midi.Instrument(program=73, name="Flute")
    t = bar - eighth
    for p, n in tune:
        mel.notes.append(pretty_midi.Note(90, p, t, t + n * eighth - 0.02))
        t += n * eighth
    acc = pretty_midi.Instrument(program=0, name="Piano")
    b = bar
    while b < t - 1e-6:
        acc.notes.append(pretty_midi.Note(60, 43, b, min(b + bar, t) - 0.02))
        b += bar
    pm.instruments += [mel, acc]
    d = tmp_path / name
    d.mkdir()
    pm.write(str(d / "input.mid"))
    (d / "meta.json").write_text(json.dumps({"title": name}))
    return d


def test_six_eight_midi_end_to_end(tmp_path):
    from music21 import converter, stream
    d = _midi_song(tmp_path, JIG, 6, 8, quarter_bpm=150)      # dotted quarter = 100
    an = pipeline.analyze(d)
    assert (an.grid.beats_per_bar, an.grid.beat_ticks) == (2, 18)
    for mode in ("solo", "duet"):
        res = pipeline.arrange_and_render(d, ArrangeOptions(mode=mode))
        arr = res["arr"]
        assert arr.time_signature == (6, 8) and arr.bpm == 100
        assert res["warnings"] == []
        assert res["svgs"] and "<svg" in res["svgs"][0]
        # one 8th = 6 ticks, written exactly as the file has it
        starts, t = [], -6
        for _, n in JIG:
            starts.append(t)
            t += 6 * n
        assert [q.start for q in arr.flute] == starts
        xml = res["musicxml"].read_text()
        assert "<beats>6</beats>" in xml and "<beat-type>8</beat-type>" in xml
        assert "<beat-unit>quarter</beat-unit>" in xml and "<beat-unit-dot" in xml
        back = converter.parse(res["musicxml"])
        for part in back.parts:
            for m in part.getElementsByClass(stream.Measure):
                # the pickup is bar 0 and holds just its 8th (R-PAGE-5); the rest are full
                full = 0.5 if m.number == 0 else 3.0
                assert m.duration.quarterLength == full, (mode, part, m.number)
        played = pretty_midi.PrettyMIDI(str(res["midi"]))
        sig = played.time_signature_changes[0]
        assert (sig.numerator, sig.denominator) == (6, 8)
        flute = sorted((n for i in played.instruments if i.program == 73 for n in i.notes),
                       key=lambda n: n.start)
        # same speed as the file: the pickup sits an 8th (0.2 s) before bar 2
        assert abs(flute[1].start - flute[0].start - 0.2) < 1e-3
        if mode == "duet":
            assert arr.rh or arr.lh


def test_three_eight_midi_uses_eighth_beats(tmp_path):
    tune = [(76, 1)] + [(75, 1), (76, 1), (75, 1), (76, 1), (71, 1), (74, 1), (72, 1), (69, 3),
                        (76, 1)] * 3
    d = _midi_song(tmp_path, tune, 3, 8, quarter_bpm=72, name="elise")   # 8th = 144
    an = pipeline.analyze(d)
    assert (an.grid.beats_per_bar, an.grid.beat_ticks) == (3, 6)
    res = pipeline.arrange_and_render(d, ArrangeOptions())
    arr = res["arr"]
    assert arr.time_signature == (3, 8) and arr.bpm == 144
    xml = res["musicxml"].read_text()
    assert "<beats>3</beats>" in xml and "<beat-type>8</beat-type>" in xml
    assert "<beat-unit>eighth</beat-unit>" in xml
    assert all(q.start % 6 == 0 for q in arr.flute)
    # asked for 6/8 instead: the same music in bars twice as long
    six = pipeline.arrange(an, ArrangeOptions(meter="6/8"))
    assert six.time_signature == (6, 8) and six.bpm == 48
    assert [q.start for q in six.flute] == [q.start for q in arr.flute]


def test_meter_choice_reads_the_same_beats():
    g = BeatGrid([i * 0.5 for i in range(24)], 4, 0)                 # quarter beats
    six = pipeline.apply_meter(g, "6/8", [], detect=False)
    assert (six.beats_per_bar, six.beat_ticks, six.beats) == (2, 18, g.beats)
    assert (pipeline.apply_meter(g, "3/8", [], False).beat_ticks,
            pipeline.apply_meter(g, "3/8", [], False).beats_per_bar) == (6, 3)
    assert pipeline.apply_meter(g, "2/4", [], False).beats_per_bar == 2
    assert pipeline.apply_meter(g, "auto", [], False) is g          # a MIDI file's own meter
    dotted = BeatGrid(g.beats, 4, 0, 18)
    assert pipeline.apply_meter(dotted, "3/4", [], False).beat_ticks == 12
    one = pipeline.apply_meter(dotted, "3/8", [], False)             # a dotted beat a bar
    assert (one.beats_per_bar, one.beat_ticks) == (1, 18)
    eighths = BeatGrid(g.beats, 3, 4, 6)                             # 3/8, bar 1 at beat 4
    joined = pipeline.apply_meter(eighths, "6/8", [], False)
    assert (joined.beats_per_bar, joined.beat_ticks) == (2, 18)
    assert joined.beats == g.beats[1::3] and joined.to_seconds(0) == eighths.to_seconds(0)
    assert abs(joined.bpm() - eighths.bpm() / 3) < 1e-6


def test_auto_meter_hears_compound_audio():
    # an audio analysis (quarter-beat grid from the tracker) of a tune in threes
    beat = 0.6
    tune = []
    for i in range(32):
        offs = ((0, 1 / 3, 2 / 3), (0, 2 / 3))[i % 2]
        for f in offs:
            tune.append(Note(72 + (i + int(3 * f)) % 7, (i + f) * beat, (i + f) * beat + 0.18, 90))
    an = Analysis("x", "piano", 20.0, BeatGrid([i * beat for i in range(36)], 4, 0), {"piano": tune})
    arr = pipeline.arrange(an, ArrangeOptions())
    assert arr.beat_ticks == 18 and arr.time_signature == (12, 8)
    assert all(q.start % 6 == 0 for q in arr.flute)                  # plain 8ths, no triplets
    four = pipeline.arrange(an, ArrangeOptions(meter="4/4"))         # the user knows better
    assert four.beat_ticks == 12 and any(q.start % 4 == 0 and q.start % 3 for q in four.flute)


def test_meter_six_eight_on_a_four_four_file(tmp_path):
    an = pipeline.analyze(_song(tmp_path))
    six = pipeline.arrange(an, ArrangeOptions(meter="6/8"))
    assert six.time_signature == (6, 8)
    assert [q.dur for q in six.flute][:4] == [18] * 4                # each beat a dotted quarter


def test_find_beats_falls_back_when_the_network_fails(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from sheetswap import neural_beats
    sr = 22050
    y = np.zeros(sr * 6)
    for i in range(12):
        y[int(i * 0.5 * sr):int(i * 0.5 * sr) + 200] = 0.8
    sf.write(tmp_path / "a.wav", y, sr)
    def broken(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr(neural_beats, "track_beats_neural", broken)
    g = pipeline.find_beats(tmp_path / "a.wav", {}, "piano", {"piano": []})
    assert g.tracker == "librosa" and 100 < g.bpm() < 140


def test_grid_tracker_survives_save_and_load(tmp_path):
    an = _analysis({"piano": [Note(60, 0.0, 0.5)]})
    an.grid = BeatGrid([0.0, 0.5, 1.0, 1.5], 4, 0, tracker="neural")
    an.save(tmp_path / "a.json")
    assert Analysis.load(tmp_path / "a.json").grid.tracker == "neural"


def test_pictures_are_read_into_a_score(tmp_path, monkeypatch):
    from music21 import corpus
    from sheetswap import omr
    d = tmp_path / "pics"
    d.mkdir()
    (d / "input-01.png").write_bytes(b"png")
    seen = []
    def fake(images, out_dir, name="omr.musicxml", title=None):
        seen.extend(p.name for p in images)
        path = Path(out_dir) / name
        corpus.parse("bach/bwv66.6").write("musicxml", fp=str(path))
        return path
    monkeypatch.setattr(omr, "images_to_musicxml", fake)
    an = pipeline.analyze(d)
    assert seen == ["input-01.png"] and an.kind == "score"
    assert pipeline.arrange(an, ArrangeOptions()).flute
