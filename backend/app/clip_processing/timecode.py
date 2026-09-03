"""HH:MM:SS <-> seconds conversion and validation. Pure, no I/O, no ffmpeg."""
from __future__ import annotations

import re

from app.errors import InvalidTimestampError

_TIMECODE_RE = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)$")


def parse_timecode(value: str, *, field_name: str = "timestamp") -> int:
    """Parse a strict HH:MM:SS string into whole seconds. Raises InvalidTimestampError."""
    if not isinstance(value, str):
        raise InvalidTimestampError(
            f"{field_name} must be a string in HH:MM:SS format, got {type(value).__name__}.",
            details={"field": field_name, "value": value},
        )
    match = _TIMECODE_RE.match(value.strip())
    if not match:
        raise InvalidTimestampError(
            f"{field_name} '{value}' is not a valid HH:MM:SS timestamp.",
            details={"field": field_name, "value": value},
        )
    hours, minutes, seconds = (int(g) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def format_timecode(total_seconds: float) -> str:
    total_seconds = max(0, int(round(total_seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def validate_clip_range(start_time: str, end_time: str, *, clip_id: str) -> tuple[int, int, int]:
    """Returns (start_seconds, end_seconds, duration_seconds). Raises InvalidTimestampError."""
    start_seconds = parse_timecode(start_time, field_name="start_time")
    end_seconds = parse_timecode(end_time, field_name="end_time")
    if end_seconds <= start_seconds:
        raise InvalidTimestampError(
            f"Clip {clip_id}: end_time ({end_time}) must be after start_time ({start_time}).",
            details={"clip_id": clip_id, "start_time": start_time, "end_time": end_time},
        )
    return start_seconds, end_seconds, end_seconds - start_seconds
