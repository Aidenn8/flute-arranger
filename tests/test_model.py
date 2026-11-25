import json

from sheetswap.model import (METERS, Analysis, ArrangeOptions, Arrangement, BeatGrid, Note,
                             normalize_meter, time_signature)


def test_beatgrid_to_beats():
    g = BeatGrid(beats=[1.0, 1.5, 2.0, 2.5], beats_per_bar=4, first_downbeat=1)
    assert g.to_beats(1.5) == 0
    assert g.to_beats(1.75) == 0.5
    assert g.to_beats(0.5) == -2
    assert g.to_beats(3.0) == 3
    assert g.to_ticks(1.75) == 24
    assert abs(g.bpm() - 120) < 1e-9


def test_beatgrid_to_seconds_inverts():
    g = BeatGrid(beats=[0.0, 0.5, 1.1, 1.8], first_downbeat=0)
    for t in (-0.3, 0.2, 0.9, 1.5, 2.5):
        assert abs(g.to_seconds(g.to_beats(t)) - t) < 1e-9


def test_analysis_roundtrip(tmp_path):
    a = Analysis(
        song_id="abc", kind="piano", duration=12.5,
        grid=BeatGrid([0.0, 0.5, 1.0], 3, 1),
        sources={"piano": [Note(60, 0.1, 0.6, 90), Note(64, 0.5, 1.0, 70)]},
        title="Test",
    )
    p = tmp_path / "a.json"
    a.save(p)
    assert Analysis.load(p) == a


def test_options_from_form_defaults():
    o = ArrangeOptions.from_form({"octave": "x", "mode": "nope"})
    assert o == ArrangeOptions()
    o2 = ArrangeOptions.from_form({"mode": "duet", "octave": "1", "simple": "on"})
    assert (o2.mode, o2.octave, o2.simple) == ("duet", 1, True)
    keys = {ArrangeOptions().key(), o2.key(),
            ArrangeOptions(easy_key=True).key(), ArrangeOptions(piano_style="cleaned").key(),
            ArrangeOptions(melody_source="vocals").key(), ArrangeOptions(octave=-1).key(),
            ArrangeOptions(meter="3/4").key()}
    assert len(keys) == 7
    assert ArrangeOptions.from_form({"meter": "3/4"}).meter == "3/4"
    assert ArrangeOptions.from_form({"meter": "7"}).meter == "auto"


def test_meter_choices():
    # every choice gets its own cache folder name (no "/" in it)
    keys = {ArrangeOptions(meter=m).key() for m in ("auto", *METERS)}
    assert len(keys) == 1 + len(METERS) == 8
    assert all("/" not in k for k in keys)
    for m in METERS:
        assert ArrangeOptions.from_form({"meter": m}).meter == m
    # the old numeric choices (links and code from before 6/8 existed)
    assert ArrangeOptions.from_form({"meter": "0"}).meter == "auto"
    assert ArrangeOptions.from_form({"meter": "3"}).meter == "3/4"
    assert ArrangeOptions.from_form({"meter": 4}).meter == "4/4"
    assert ArrangeOptions(meter=3) == ArrangeOptions(meter="3/4")
    assert ArrangeOptions(meter=0).meter == "auto"
    for junk in ("5/4", "6/4", "x", None, "", True, 2.5):
        assert normalize_meter(junk) == "auto", junk
    assert ArrangeOptions.from_form({}).meter == "auto"


def test_time_signatures():
    assert time_signature(4, 48) == (4, 4)
    assert time_signature(3, 48) == (3, 4)
    assert time_signature(2, 48) == (2, 4)
    assert time_signature(2, 72) == (6, 8)
    assert time_signature(3, 72) == (9, 8)
    assert time_signature(4, 72) == (12, 8)
    assert time_signature(1, 72) == (3, 8)            # one dotted beat a bar
    assert time_signature(3, 24) == (3, 8)             # three 8th beats a bar
    for name, (bpb, unit) in METERS.items():
        assert "%d/%d" % time_signature(bpb, unit) == name
    arr = Arrangement(flute=[], beats_per_bar=2, beat_ticks=72)
    assert arr.time_signature == (6, 8) and arr.bar_ticks == 144
    assert Arrangement(flute=[]).bar_ticks == 192


def test_beat_ticks_scale_the_grid():
    g = BeatGrid([0.0, 0.5, 1.0, 1.5], 2, 0, beat_ticks=72)
    assert g.to_ticks(0.5) == 72 and g.to_ticks(0.25) == 36 and g.bar_ticks == 144


def test_old_analysis_json_loads_with_quarter_beats(tmp_path):
    # analysis.json written before beat_ticks existed
    old = {"song_id": "abc", "kind": "piano", "duration": 3.0, "title": "Old",
           "grid": {"beats": [0.0, 0.5, 1.0], "beats_per_bar": 3, "first_downbeat": 0},
           "sources": {"piano": [[60, 0.1, 0.6, 90]]}}
    p = tmp_path / "analysis.json"
    p.write_text(json.dumps(old))
    a = Analysis.load(p)
    assert a.grid.beat_ticks == 48 and a.grid.to_ticks(0.5) == 48
    a.grid.beat_ticks = 72
    a.save(p)
    assert Analysis.load(p).grid.beat_ticks == 72
    assert json.loads(p.read_text())["ticks_per_beat"] == 48


def test_old_twelve_tick_caches_load_on_the_finer_grid(tmp_path):
    # analysis.json and score.json from before 48 ticks a quarter (12 a quarter then)
    old = {"song_id": "abc", "kind": "piano", "duration": 3.0, "title": "Old",
           "grid": {"beats": [0.0, 0.5, 1.0], "beats_per_bar": 2, "first_downbeat": 0,
                    "beat_ticks": 18},
           "sources": {"piano": [[60, 0.1, 0.6, 90]]}}
    p = tmp_path / "analysis.json"
    p.write_text(json.dumps(old))
    g = Analysis.load(p).grid
    assert g.beat_ticks == 72 and g.bar_ticks == 144 and g.to_ticks(0.5) == 72
    old["grid"]["beat_ticks"] = 12
    p.write_text(json.dumps(old))
    assert Analysis.load(p).grid.beat_ticks == 48

    from sheetswap.score_input import ScoreSource
    part = {"name": "Flute", "instrument": "Flute", "staff": 0, "keyboard": False,
            "melody": [[72, -3, 3, 80], [74, 0, 9, 80]], "notes": [[72, -3, 3], [74, 0, 9]],
            "marks": [["slur", -3, 0, ""]], "warnings": [], "tune": False}
    src = ScoreSource.from_dict({"format": 1, "parts": [part], "beats_per_bar": 2,
                                 "beat_ticks": 18, "meter": "6/8", "pickup": 3})
    q = src.parts[0].melody
    assert [(n.start, n.dur) for n in q] == [(-12, 12), (0, 36)]
    assert [(n.start, n.dur) for n in src.parts[0].notes] == [(-12, 12), (0, 36)]
    assert (src.parts[0].marks[0].start, src.parts[0].marks[0].end) == (-12, 0)
    assert (src.beat_ticks, src.pickup) == (72, 12)
    again = ScoreSource.from_dict(src.to_dict())          # today's format is read as it is
    assert again.to_dict() == src.to_dict()
