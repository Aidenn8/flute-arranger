from sheetswap.checks import check_arrangement
from sheetswap.model import Arrangement, QNote


def scale(start=0):
    return [QNote(72 + i, start + 48 * i, 48) for i in range(4)]


def test_clean_arrangement_has_no_problems():
    arr = Arrangement(flute=scale(), rh=[QNote(60, 0, 192), QNote(64, 0, 192)],
                      lh=[QNote(48, 0, 192)])
    assert check_arrangement(arr, simple=False) == []


def test_checks_flag_out_of_range_and_short_notes():
    arr = Arrangement(flute=[QNote(50, 0, 48), QNote(100, 48, 48),
                             QNote(72, 96, 8), QNote(74, 104, 88)])
    problems = check_arrangement(arr, simple=False)
    text = " ".join(problems).lower()
    assert "range" in text and "2 flute notes" in text
    assert "short" in text
    assert all(isinstance(p, str) and p.endswith(".") for p in problems)


def test_simple_mode_needs_eighths():
    arr = Arrangement(flute=[QNote(72, 0, 12), QNote(74, 12, 36), QNote(76, 48, 144)])
    assert check_arrangement(arr, simple=False) == []
    problems = check_arrangement(arr, simple=True)
    assert len(problems) == 1 and "8th" in problems[0]


def test_checks_flag_flute_overlap():
    arr = Arrangement(flute=[QNote(72, 0, 72), QNote(74, 48, 48)])
    problems = check_arrangement(arr, simple=False)
    assert any("overlap" in p for p in problems)


def test_checks_flag_piano_above_flute():
    flute = [QNote(72, 0, 96), QNote(67, 96, 96)]
    # 69 is under the first flute note but sounds over the second one (67)
    arr = Arrangement(flute=flute, rh=[QNote(69, 0, 192)], lh=[QNote(48, 0, 192)])
    problems = check_arrangement(arr, simple=False)
    assert any("piano" in p.lower() and "above" in p for p in problems)
    # same pitch counts too
    arr2 = Arrangement(flute=[QNote(72, 0, 96)], rh=[QNote(72, 48, 48)])
    assert any("piano" in p.lower() for p in check_arrangement(arr2, simple=False))
    # a piano note under a flute rest is fine
    arr3 = Arrangement(flute=[QNote(72, 0, 48)], rh=[QNote(79, 96, 48)])
    assert check_arrangement(arr3, simple=False) == []


def test_checks_flag_empty_flute():
    problems = check_arrangement(Arrangement(flute=[]), simple=False)
    assert len(problems) == 1 and "empty" in problems[0]


def test_problems_name_bars():
    # bar 3 in 4/4 starts at tick 384; a pickup bar is bar 0 (R-PAGE-5), so the
    # first full bar is still bar 1; a lead-in of whole bars is counted
    arr = Arrangement(flute=[QNote(72, 0, 48), QNote(40, 400, 48)])
    assert "bar 3" in check_arrangement(arr, simple=False)[0]
    arr_pickup = Arrangement(flute=[QNote(72, -24, 24), QNote(40, 400, 48)])
    assert "bar 3" in check_arrangement(arr_pickup, simple=False)[0]
    arr_lead_bar = Arrangement(flute=[QNote(72, -192, 48), QNote(40, 400, 48)])
    assert "bar 4" in check_arrangement(arr_lead_bar, simple=False)[0]
