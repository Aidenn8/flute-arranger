from sheetswap.checks import check_arrangement
from sheetswap.model import Arrangement, QNote


def scale(start=0):
    return [QNote(72 + i, start + 12 * i, 12) for i in range(4)]


def test_clean_arrangement_has_no_problems():
    arr = Arrangement(flute=scale(), rh=[QNote(60, 0, 48), QNote(64, 0, 48)],
                      lh=[QNote(48, 0, 48)])
    assert check_arrangement(arr, simple=False) == []


def test_checks_flag_out_of_range_and_short_notes():
    arr = Arrangement(flute=[QNote(50, 0, 12), QNote(100, 12, 12),
                             QNote(72, 24, 2), QNote(74, 26, 22)])
    problems = check_arrangement(arr, simple=False)
    text = " ".join(problems).lower()
    assert "range" in text and "2 flute notes" in text
    assert "short" in text
    assert all(isinstance(p, str) and p.endswith(".") for p in problems)


def test_simple_mode_needs_eighths():
    arr = Arrangement(flute=[QNote(72, 0, 3), QNote(74, 3, 9), QNote(76, 12, 36)])
    assert check_arrangement(arr, simple=False) == []
    problems = check_arrangement(arr, simple=True)
    assert len(problems) == 1 and "8th" in problems[0]


def test_checks_flag_flute_overlap():
    arr = Arrangement(flute=[QNote(72, 0, 18), QNote(74, 12, 12)])
    problems = check_arrangement(arr, simple=False)
    assert any("overlap" in p for p in problems)


def test_checks_flag_piano_above_flute():
    flute = [QNote(72, 0, 24), QNote(67, 24, 24)]
    # 69 is under the first flute note but sounds over the second one (67)
    arr = Arrangement(flute=flute, rh=[QNote(69, 0, 48)], lh=[QNote(48, 0, 48)])
    problems = check_arrangement(arr, simple=False)
    assert any("piano" in p.lower() and "above" in p for p in problems)
    # same pitch counts too
    arr2 = Arrangement(flute=[QNote(72, 0, 24)], rh=[QNote(72, 12, 12)])
    assert any("piano" in p.lower() for p in check_arrangement(arr2, simple=False))
    # a piano note under a flute rest is fine
    arr3 = Arrangement(flute=[QNote(72, 0, 12)], rh=[QNote(79, 24, 12)])
    assert check_arrangement(arr3, simple=False) == []


def test_checks_flag_empty_flute():
    problems = check_arrangement(Arrangement(flute=[]), simple=False)
    assert len(problems) == 1 and "empty" in problems[0]


def test_problems_name_bars():
    # bar 3 in 4/4 starts at tick 96; a pickup bar is bar 0 (R-PAGE-5), so the
    # first full bar is still bar 1; a lead-in of whole bars is counted
    arr = Arrangement(flute=[QNote(72, 0, 12), QNote(40, 100, 12)])
    assert "bar 3" in check_arrangement(arr, simple=False)[0]
    arr_pickup = Arrangement(flute=[QNote(72, -6, 6), QNote(40, 100, 12)])
    assert "bar 3" in check_arrangement(arr_pickup, simple=False)[0]
    arr_lead_bar = Arrangement(flute=[QNote(72, -48, 12), QNote(40, 100, 12)])
    assert "bar 4" in check_arrangement(arr_lead_bar, simple=False)[0]
