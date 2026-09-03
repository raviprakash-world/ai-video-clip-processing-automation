"""Validator for analysis JSON schema_version 1.0."""
from __future__ import annotations

import json as jsonlib
from typing import Any

from pydantic import ValidationError

from app.clip_processing.timecode import validate_clip_range
from app.errors import (
    DuplicateClipIdError,
    InvalidJsonError,
    InvalidSchemaError,
    MissingRequiredFieldError,
)
from app.schemas.analysis import AnalysisDocument
from app.schemas.validated import ValidatedAnalysis, ValidatedClip

_TOP_LEVEL_REQUIRED = ("schema_version", "analysis", "clips")
_ANALYSIS_REQUIRED = ("source_type", "language", "total_clips_found", "analysis_status")
_CLIP_REQUIRED = (
    "clip_id", "rank", "start_time", "end_time", "duration_seconds", "viral_score",
    "category", "speaker", "hook", "title_options", "caption", "hashtags", "reason",
    "payoff", "context_warning", "copyright_warning",
)


def _check_required_fields(raw: dict, required: tuple[str, ...], *, where: str) -> None:
    missing = [f for f in required if f not in raw]
    if missing:
        raise MissingRequiredFieldError(
            f"{where} is missing required field(s): {', '.join(missing)}.",
            details={"where": where, "missing_fields": missing},
        )


def parse_json_only(raw_json: str | dict) -> dict:
    """Stage 0: raw text/dict -> dict. No assumptions about shape yet.

    Used both by the normal validation pipeline and by the foreign-format
    translation layer (app.json_validation.foreign_formats), which needs a
    plain dict to run its detectors against before any required-field
    checks happen.
    """
    if isinstance(raw_json, str):
        try:
            data = jsonlib.loads(raw_json)
        except jsonlib.JSONDecodeError as exc:
            raise InvalidJsonError(f"The provided text is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
    else:
        data = raw_json

    if not isinstance(data, dict):
        raise InvalidJsonError("The top-level JSON value must be an object.")
    return data


def parse_and_check_shape(raw_json: str | dict) -> dict:
    """Stage 1: raw text/dict -> dict with all required keys present (pre-pydantic)."""
    data = parse_json_only(raw_json)

    _check_required_fields(data, _TOP_LEVEL_REQUIRED, where="Top-level document")

    analysis = data.get("analysis")
    if not isinstance(analysis, dict):
        raise MissingRequiredFieldError("'analysis' must be an object.", details={"where": "analysis"})
    _check_required_fields(analysis, _ANALYSIS_REQUIRED, where="analysis")

    clips = data.get("clips")
    if not isinstance(clips, list) or len(clips) == 0:
        raise MissingRequiredFieldError("'clips' must be a non-empty array.", details={"where": "clips"})
    for idx, clip in enumerate(clips):
        if not isinstance(clip, dict):
            raise InvalidSchemaError(f"clips[{idx}] must be an object.", details={"index": idx})
        _check_required_fields(clip, _CLIP_REQUIRED, where=f"clips[{idx}] (clip_id={clip.get('clip_id', '?')})")

    return data


def validate(raw_json: str | dict) -> ValidatedAnalysis:
    """Full validation pipeline for schema_version 1.0.

    Order: shape/required-fields -> pydantic types/ranges -> clip_id uniqueness
    -> per-clip timestamp parsing -> deterministic duration recalculation.
    """
    data = parse_and_check_shape(raw_json)

    try:
        doc = AnalysisDocument.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise InvalidSchemaError(
            f"Field '{loc}' is invalid: {first['msg']}.",
            details={"errors": exc.errors()},
        ) from exc

    seen_ids: set[str] = set()
    for clip in doc.clips:
        if clip.clip_id in seen_ids:
            raise DuplicateClipIdError(f"Duplicate clip_id '{clip.clip_id}' found in clips[].", details={"clip_id": clip.clip_id})
        seen_ids.add(clip.clip_id)

    validated_clips: list[ValidatedClip] = []
    for clip in doc.clips:
        start_seconds, end_seconds, computed_duration = validate_clip_range(
            clip.start_time, clip.end_time, clip_id=clip.clip_id
        )
        validated_clips.append(
            ValidatedClip(
                clip_id=clip.clip_id,
                rank=clip.rank,
                start_time=clip.start_time,
                end_time=clip.end_time,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=computed_duration,  # deterministically recalculated, never trusted from AI
                ai_reported_duration_seconds=clip.duration_seconds,
                duration_mismatch=(computed_duration != clip.duration_seconds),
                viral_score=clip.viral_score,
                category=clip.category,
                speaker=clip.speaker,
                hook=clip.hook,
                title_options=clip.title_options,
                caption=clip.caption,
                hashtags=clip.hashtags,
                reason=clip.reason,
                payoff=clip.payoff,
                context_warning=clip.context_warning,
                copyright_warning=clip.copyright_warning,
                extra={k: v for k, v in clip.model_dump().items() if k not in _CLIP_REQUIRED},
            )
        )

    return ValidatedAnalysis(schema_version=doc.schema_version, analysis=doc.analysis, clips=validated_clips)
