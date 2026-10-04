import pytest

from siavos_scribe.intervals import format_interval, parse_interval


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("90m", 5400),
        ("12h", 43200),
        ("3d", 259200),
        ("1d12h", 129600),
        (" 2 Д ", 172800),
        ("12ч", 43200),
        ("30м", 1800),
    ],
)
def test_parse_valid(text, seconds):
    assert parse_interval(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "12", "h", "0m", "3x", "1d garbage", "-5h"])
def test_parse_invalid(text):
    assert parse_interval(text) is None


def test_format():
    assert format_interval(90000) == "1 день 1 час"
    assert format_interval(1800) == "30 минут"
    assert format_interval(0) == "0 минут"
    assert format_interval(60) == "1 минуту"  # accusative: "раз в 1 минуту"
    assert format_interval(3 * 86400 + 12 * 3600) == "3 дня 12 часов"
    assert format_interval(21 * 86400 + 22 * 60) == "21 день 22 минуты"
    assert format_interval(11 * 86400 + 14 * 3600) == "11 дней 14 часов"
