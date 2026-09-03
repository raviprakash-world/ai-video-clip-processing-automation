from app.errors import TimestampOutOfRangeError
from app.json_validation.validator import validate_analysis_json
from app.json_validation.video_bounds import check_all_clips, check_clip_within_video
from app.video_metadata.probe import VideoMetadata
from tests.fixtures import valid_analysis


def _video(duration_seconds: float) -> VideoMetadata:
    return VideoMetadata(
        duration_seconds=duration_seconds,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        audio_present=True,
        size_bytes=123456,
    )


def test_clip_within_bounds_ok():
    validated = validate_analysis_json(valid_analysis())
    video = _video(20 * 60)  # 20 minutes, both fixture clips fit
    for clip in validated.clips:
        check_clip_within_video(clip, video)  # should not raise


def test_clip_end_exceeds_duration():
    data = valid_analysis()
    data["clips"][1]["start_time"] = "00:19:50"
    data["clips"][1]["end_time"] = "00:20:30"
    data["clips"][1]["duration_seconds"] = 40
    validated = validate_analysis_json(data)
    video = _video(20 * 60)  # 00:20:00
    clip_002 = next(c for c in validated.clips if c.clip_id == "clip_002")
    try:
        check_clip_within_video(clip_002, video)
        assert False, "expected TimestampOutOfRangeError"
    except TimestampOutOfRangeError as exc:
        assert exc.details["video_duration"] == "00:20:00"
        assert exc.details["clip_id"] == "clip_002"


def test_one_bad_clip_does_not_block_the_others():
    data = valid_analysis()
    data["clips"][1]["start_time"] = "00:19:50"
    data["clips"][1]["end_time"] = "00:20:30"
    data["clips"][1]["duration_seconds"] = 40
    validated = validate_analysis_json(data)
    video = _video(20 * 60)
    errors = check_all_clips(validated.clips, video)
    assert set(errors.keys()) == {"clip_002"}
    assert "clip_001" not in errors
