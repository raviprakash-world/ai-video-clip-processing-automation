"""Cross-check validated clip timestamps against the actual source video duration."""
from __future__ import annotations

from app.clip_processing.timecode import format_timecode
from app.errors import TimestampOutOfRangeError
from app.schemas.validated import ValidatedClip
from app.video_metadata.probe import VideoMetadata


def check_clip_within_video(clip: ValidatedClip, video: VideoMetadata) -> None:
    if clip.start_seconds < 0 or clip.end_seconds > video.duration_seconds:
        raise TimestampOutOfRangeError(
            f"Clip {clip.clip_id}: requested range {clip.start_time} -> {clip.end_time} "
            f"exceeds the source video's duration of {format_timecode(video.duration_seconds)}.",
            details={
                "clip_id": clip.clip_id,
                "start_time": clip.start_time,
                "end_time": clip.end_time,
                "video_duration": format_timecode(video.duration_seconds),
            },
        )


def check_all_clips(clips: list[ValidatedClip], video: VideoMetadata) -> dict[str, TimestampOutOfRangeError]:
    """Non-raising batch check: returns {clip_id: error} for out-of-range clips.

    One bad clip must not block validation of the others (section 17).
    """
    errors: dict[str, TimestampOutOfRangeError] = {}
    for clip in clips:
        try:
            check_clip_within_video(clip, video)
        except TimestampOutOfRangeError as exc:
            errors[clip.clip_id] = exc
    return errors
