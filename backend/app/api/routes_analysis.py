from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.errors import InvalidJsonError
from app.json_validation.validator import validate_analysis_json
from app.json_validation.video_bounds import check_all_clips
from app.storage.paths import find_upload_source
from app.video_metadata.probe import probe_video

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class ValidateRequest(BaseModel):
    json_text: Optional[str] = None
    json_data: Optional[dict[str, Any]] = None
    upload_id: Optional[str] = None


@router.post("/validate")
async def validate_analysis(payload: ValidateRequest):
    raw = payload.json_data if payload.json_data is not None else payload.json_text
    if raw is None:
        raise InvalidJsonError("Provide either json_text or json_data in the request body.")

    validated = validate_analysis_json(raw)

    bounds_errors: dict[str, dict] = {}
    video_summary: Optional[dict] = None
    if payload.upload_id:
        source_path = find_upload_source(payload.upload_id)
        video = await probe_video(source_path)
        video_summary = {"duration_seconds": video.duration_seconds, "width": video.width, "height": video.height}
        errors = check_all_clips(validated.clips, video)
        bounds_errors = {clip_id: exc.to_dict() for clip_id, exc in errors.items()}

    clips_out = []
    for clip in validated.clips:
        error = bounds_errors.get(clip.clip_id)
        clips_out.append(
            {
                **clip.to_metadata_dict(),
                "ai_reported_duration_seconds": clip.ai_reported_duration_seconds,
                "duration_mismatch": clip.duration_mismatch,
                "valid": error is None,
                "error": error,
            }
        )

    return {
        "schema_version": validated.schema_version,
        "analysis": validated.analysis.model_dump(),
        "video": video_summary,
        "clips": clips_out,
    }
