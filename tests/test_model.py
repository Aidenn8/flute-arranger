import json

from sheetswap.model import (METERS, Analysis, ArrangeOptions, Arrangement, BeatGrid, Note,
                             normalize_meter, time_signature)


def test_beatgrid_to_beats():
    g = BeatGrid(beats=[1.0, 1.5, 2.0, 2.5], beats_per_bar=4, first_downbeat=1)
    assert g.to_beats(1.5) == 0
    assert g.to_beats(1.75) == 0.5
    assert g.to_beats(0.5) == -2
    assert g.to_beats(3.0) == 3
    assert g.to_ticks(1.75) == 6
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
    assert time_signature(4, 12) == (4, 4)
    assert time_signature(3, 12) == (3, 4)
    assert time_signature(2, 12) == (2, 4)
    assert time_signature(2, 18) == (6, 8)
    assert time_signature(3, 18) == (9, 8)
    assert time_signature(4, 18) == (12, 8)
    assert time_signature(1, 18) == (3, 8)            # one dotted beat a bar
    assert time_signature(3, 6) == (3, 8)             # three 8th beats a bar
    for name, (bpb, unit) in METERS.items():
        assert "%d/%d" % time_signature(bpb, unit) == name
    arr = Arrangement(flute=[], beats_per_bar=2, beat_ticks=18)
    assert arr.time_signature == (6, 8) and arr.bar_ticks == 36
    assert Arrangement(flute=[]).bar_ticks == 48


def test_beat_ticks_scale_the_grid():
    g = BeatGrid([0.0, 0.5, 1.0, 1.5], 2, 0, beat_ticks=18)
    assert g.to_ticks(0.5) == 18 and g.to_ticks(0.25) == 9 and g.bar_ticks == 36


def test_old_analysis_json_loads_with_quarter_beats(tmp_path):
    # analysis.json written before beat_ticks existed
    old = {"song_id": "abc", "kind": "piano", "duration": 3.0, "title": "Old",
           "grid": {"beats": [0.0, 0.5, 1.0], "beats_per_bar": 3, "first_downbeat": 0},
           "sources": {"piano": [[60, 0.1, 0.6, 90]]}}
    p = tmp_path / "analysis.json"
    p.write_text(json.dumps(old))
    a = Analysis.load(p)
    assert a.grid.beat_ticks == 12 and a.grid.to_ticks(0.5) == 12
    a.grid.beat_ticks = 18
    a.save(p)
    assert Analysis.load(p).grid.beat_ticks == 18
