from __future__ import annotations

import io
import zipfile
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.errors import InvalidJsonError, NotFoundError
from app.jobs.manager import job_manager
from app.jobs.models import ClipStatus
from app.json_validation.validator import validate_analysis_json
from app.schemas.processing_config import ProcessingConfig
from app.storage.paths import find_upload_source, job_dir

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    upload_id: str
    json_text: Optional[str] = None
    json_data: Optional[dict[str, Any]] = None
    selected_clip_ids: Optional[list[str]] = None  # None = all clips
    config: ProcessingConfig = ProcessingConfig()


@router.post("")
async def create_job(payload: CreateJobRequest):
    raw = payload.json_data if payload.json_data is not None else payload.json_text
    if raw is None:
        raise InvalidJsonError("Provide either json_text or json_data in the request body.")

    # Defensive re-validation: never trust that the client-side validation step ran.
    validated = validate_analysis_json(raw)

    if payload.selected_clip_ids is not None:
        wanted = set(payload.selected_clip_ids)
        selected = [c for c in validated.clips if c.clip_id in wanted]
        missing = wanted - {c.clip_id for c in selected}
        if missing:
            raise NotFoundError(f"selected_clip_ids not found in analysis JSON: {', '.join(sorted(missing))}")
    else:
        selected = validated.clips

    source_path = find_upload_source(payload.upload_id)

    job = await job_manager.create_job(upload_path=source_path, selected_clips=selected, config=payload.config)
    return job.to_public_dict()


@router.get("")
async def list_jobs():
    return [j.to_public_dict() for j in job_manager.list_jobs()]


@router.get("/{job_id}")
async def get_job(job_id: str):
    return job_manager.get_job(job_id).to_public_dict()


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    return job_manager.cancel_job(job_id).to_public_dict()


@router.get("/{job_id}/clips/{clip_id}/file")
async def download_clip(job_id: str, clip_id: str):
    job = job_manager.get_job(job_id)
    clip_job = job.clips.get(clip_id)
    if not clip_job or clip_job.status != ClipStatus.COMPLETED or not clip_job.output_file:
        raise NotFoundError(f"No completed output available for clip '{clip_id}' in job '{job_id}'.")
    path = job_dir(job_id) / clip_job.output_file
    if not path.exists():
        raise NotFoundError("Output file is no longer available (it may have been cleaned up).")
    return FileResponse(path, media_type="video/mp4", filename=clip_job.output_file)


@router.get("/{job_id}/download-all")
async def download_all(job_id: str):
    job = job_manager.get_job(job_id)
    jdir = job_dir(job_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for clip_job in job.clips.values():
            if clip_job.status == ClipStatus.COMPLETED and clip_job.output_file:
                video_path = jdir / clip_job.output_file
                if video_path.exists():
                    zf.write(video_path, arcname=video_path.name)
                metadata_path = jdir / f"clip_{clip_job.clip.clip_id}_metadata.json"
                if metadata_path.exists():
                    zf.write(metadata_path, arcname=metadata_path.name)
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}_clips.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)
