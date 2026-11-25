import pytest

from sheetswap.cli import build_parser


def test_meter_option():
    ap = build_parser()
    assert ap.parse_args(["x.mp3"]).meter == "auto"
    for m in ("auto", "2/4", "3/4", "4/4", "3/8", "6/8", "9/8", "12/8"):
        assert ap.parse_args(["x.mp3", "--meter", m]).meter == m
    # the old numbers still work
    assert ap.parse_args(["x.mp3", "--meter", "3"]).meter == "3/4"
    assert ap.parse_args(["x.mp3", "--meter", "0"]).meter == "auto"
    for bad in ("5/4", "7", "six-eight"):
        with pytest.raises(SystemExit):
            ap.parse_args(["x.mp3", "--meter", bad])
