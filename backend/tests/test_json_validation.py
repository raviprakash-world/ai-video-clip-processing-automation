import pytest

from app.errors import (
    DuplicateClipIdError,
    InvalidJsonError,
    InvalidSchemaError,
    InvalidTimestampError,
    MissingRequiredFieldError,
    UnsupportedSchemaVersionError,
)
from app.json_validation.validator import validate_analysis_json
from tests.fixtures import valid_analysis


def test_valid_analysis_passes():
    result = validate_analysis_json(valid_analysis())
    assert result.schema_version == "1.0"
    assert len(result.clips) == 2
    assert result.clips[0].clip_id == "clip_001"
    assert result.clips[0].duration_seconds == 55


def test_invalid_json_text():
    with pytest.raises(InvalidJsonError):
        validate_analysis_json("{not valid json")


def test_missing_top_level_field():
    data = valid_analysis()
    del data["analysis"]
    with pytest.raises(MissingRequiredFieldError):
        validate_analysis_json(data)


def test_missing_clip_field():
    data = valid_analysis()
    del data["clips"][0]["hook"]
    with pytest.raises(MissingRequiredFieldError):
        validate_analysis_json(data)


def test_unsupported_schema_version():
    data = valid_analysis()
    data["schema_version"] = "2.0"
    with pytest.raises(UnsupportedSchemaVersionError):
        validate_analysis_json(data)


def test_duplicate_clip_ids():
    data = valid_analysis()
    data["clips"][1]["clip_id"] = "clip_001"
    with pytest.raises(DuplicateClipIdError):
        validate_analysis_json(data)


def test_wrong_type_viral_score():
    data = valid_analysis()
    data["clips"][0]["viral_score"] = "ninety-two"
    with pytest.raises(InvalidSchemaError):
        validate_analysis_json(data)


def test_viral_score_out_of_range():
    data = valid_analysis()
    data["clips"][0]["viral_score"] = 150
    with pytest.raises(InvalidSchemaError):
        validate_analysis_json(data)


def test_invalid_timestamp_format():
    data = valid_analysis()
    data["clips"][0]["start_time"] = "5:20"
    with pytest.raises(InvalidTimestampError):
        validate_analysis_json(data)


def test_start_after_end():
    data = valid_analysis()
    data["clips"][0]["start_time"] = "00:10:00"
    data["clips"][0]["end_time"] = "00:05:00"
    with pytest.raises(InvalidTimestampError):
        validate_analysis_json(data)


def test_duration_mismatch_is_recalculated_not_trusted():
    data = valid_analysis()
    data["clips"][0]["duration_seconds"] = 99999  # AI lied; must be recalculated, not blindly trusted
    result = validate_analysis_json(data)
    clip = result.clips[0]
    assert clip.duration_seconds == 55  # deterministically recomputed from timestamps
    assert clip.ai_reported_duration_seconds == 99999
    assert clip.duration_mismatch is True


def test_extra_metadata_fields_are_preserved_not_rejected():
    data = valid_analysis()
    data["clips"][0]["future_field"] = "some new metadata"
    result = validate_analysis_json(data)
    assert result.clips[0].extra.get("future_field") == "some new metadata"


def test_hashtags_must_be_array():
    data = valid_analysis()
    data["clips"][0]["hashtags"] = "#ai"
    with pytest.raises(InvalidSchemaError):
        validate_analysis_json(data)


def test_rank_must_be_integer():
    data = valid_analysis()
    data["clips"][0]["rank"] = "one"
    with pytest.raises(InvalidSchemaError):
        validate_analysis_json(data)
