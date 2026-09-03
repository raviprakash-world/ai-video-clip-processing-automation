import pytest

from app.clip_processing.timecode import format_timecode, parse_timecode, validate_clip_range
from app.errors import InvalidTimestampError


def test_parse_valid_timecode():
    assert parse_timecode("00:05:20") == 320
    assert parse_timecode("01:00:00") == 3600


def test_parse_rejects_malformed():
    for bad in ["5:20", "00:60:00", "00:00:60", "abc", "", "00:05", "00:05:20:00"]:
        with pytest.raises(InvalidTimestampError):
            parse_timecode(bad)


def test_parse_rejects_non_string():
    with pytest.raises(InvalidTimestampError):
        parse_timecode(320)  # type: ignore[arg-type]


def test_format_timecode_roundtrip():
    assert format_timecode(320) == "00:05:20"
    assert format_timecode(3661) == "01:01:01"


def test_validate_clip_range_ok():
    start, end, duration = validate_clip_range("00:05:20", "00:06:15", clip_id="clip_001")
    assert (start, end, duration) == (320, 375, 55)


def test_validate_clip_range_start_after_end():
    with pytest.raises(InvalidTimestampError):
        validate_clip_range("00:10:00", "00:05:00", clip_id="clip_x")


def test_validate_clip_range_start_equals_end():
    with pytest.raises(InvalidTimestampError):
        validate_clip_range("00:10:00", "00:10:00", clip_id="clip_x")


def test_validate_clip_range_negative_like_value_rejected_by_format():
    with pytest.raises(InvalidTimestampError):
        validate_clip_range("-1:00:00", "00:05:00", clip_id="clip_x")
