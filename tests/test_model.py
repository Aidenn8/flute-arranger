from sheetswap.model import Analysis, ArrangeOptions, BeatGrid, Note


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
            ArrangeOptions(melody_source="vocals").key(), ArrangeOptions(octave=-1).key()}
    assert len(keys) == 6
