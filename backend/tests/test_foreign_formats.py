"""
A real third-party tool produces JSON shaped like a "processing_steps"
pipeline rather than our native schema_version/analysis/clips document.
These tests cover the translation adapter that lets the app accept it
without ever treating its action/execution_instruction/watermark-region
fields as anything other than display text.
"""
import pytest

from app.errors import MissingRequiredFieldError, UnsupportedSchemaVersionError
from app.json_validation.validator import validate_analysis_json

REAL_WORLD_PAYLOAD = {
    "job_id": "process_84729",
    "global_settings": {"strict_execution": True},
    "processing_steps": [
        {
            "step": 1,
            "action": "trim",
            "parameters": {"start_time": "00:00:15", "end_time": "00:02:45"},
        },
        {
            "step": 2,
            "action": "handle_watermark",
            "parameters": {
                "detect_overlay": True,
                "known_regions": [{"x": 1720, "y": 980, "width": 200, "height": 100}],
                "user_authorized_source": False,
                "execution_instruction": "flag_only",
                "fallback_message": "Third-party watermark detected in the bottom right. "
                "Please confirm you own this source material to enable removal.",
            },
        },
    ],
}


def test_translates_real_world_processing_steps_payload():
    result = validate_analysis_json(REAL_WORLD_PAYLOAD)
    assert len(result.clips) == 1
    clip = result.clips[0]
    assert clip.clip_id == "step_1"
    assert clip.start_seconds == 15
    assert clip.end_seconds == 165
    assert clip.duration_seconds == 150  # deterministically computed, same as any native clip


def test_watermark_step_becomes_display_only_context_warning():
    result = validate_analysis_json(REAL_WORLD_PAYLOAD)
    clip = result.clips[0]
    assert "Third-party watermark detected" in clip.context_warning
    assert "x=1720" in clip.context_warning
    assert "NOT acted on automatically" in clip.context_warning
    # None of the foreign instruction fields exist anywhere on the validated clip
    # except folded into that one display string -- there is no field named
    # action/execution_instruction/detect_overlay/known_regions to dispatch on.
    assert not hasattr(clip, "action")
    assert not hasattr(clip, "execution_instruction")
    assert not hasattr(clip, "known_regions")


def test_multiple_trim_steps_become_multiple_clips():
    payload = {
        "job_id": "x",
        "processing_steps": [
            {"step": 1, "action": "trim", "parameters": {"start_time": "00:00:00", "end_time": "00:00:10"}},
            {"step": 2, "action": "trim", "parameters": {"start_time": "00:01:00", "end_time": "00:01:20"}},
        ],
    }
    result = validate_analysis_json(payload)
    assert [c.clip_id for c in result.clips] == ["step_1", "step_2"]
    assert [c.rank for c in result.clips] == [1, 2]


def test_no_trim_steps_yields_missing_clips_error():
    payload = {"job_id": "x", "processing_steps": [{"step": 1, "action": "handle_watermark", "parameters": {}}]}
    with pytest.raises(MissingRequiredFieldError):
        validate_analysis_json(payload)


def test_document_with_schema_version_is_never_silently_translated():
    """A native-looking document (has schema_version) that's simply missing
    clips must fail loudly as unsupported/invalid -- never get reinterpreted
    as a foreign processing_steps document just because clips is absent."""
    payload = {"schema_version": "9.9", "processing_steps": [{"action": "trim", "parameters": {}}]}
    with pytest.raises(UnsupportedSchemaVersionError):
        validate_analysis_json(payload)


def test_unrelated_unknown_shape_still_fails_clearly():
    # No schema_version and no recognized foreign shape (no processing_steps
    # list) -> falls through to the native "which fields are missing" error.
    with pytest.raises(MissingRequiredFieldError):
        validate_analysis_json({"totally": "unrelated", "shape": True})
