import pytest

from app.utils import format_time, parse_time_to_ms, validate_video_url

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42", 42_000),
        ("42.5", 42_500),
        ("1:02.345", 62_345),
        ("1:02:03.004", 3_723_004),
        ("0:59.999", 59_999),
    ],
)

def test_parse_time(raw, expected):
    assert parse_time_to_ms(raw) == expected

@pytest.mark.parametrize("raw", ["", "0", "1:60", "1:2:60", "1:60:00", "1:02.1234", "wat"])
def test_parse_time_rejects_invalid(raw):
    with pytest.raises(ValueError):
        parse_time_to_ms(raw)

def test_format_time():
    assert format_time(62_345) == "1:02.345"
    assert format_time(3_723_004) == "1:02:03.004"

def test_video_url_validation():
    assert validate_video_url("https://youtu.be/example") == "https://youtu.be/example"
    with pytest.raises(ValueError):
        validate_video_url("javascript:alert(1)")
