"""Per-clip metadata JSON sidecar (section 16) so downstream automation can pair a clip file with its metadata."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from app.schemas.validated import ValidatedClip

ProcessingStatus = Literal["queued", "processing", "completed", "failed", "cancelled"]


def write_clip_metadata(
    *,
    job_dir: Path,
    clip: ValidatedClip,
    output_file: str | None,
    processing_status: ProcessingStatus,
    error: dict | None = None,
) -> Path:
    payload = clip.to_metadata_dict()
    payload["output_file"] = output_file
    payload["processing_status"] = processing_status
    if error:
        payload["error"] = error

    path = job_dir / f"clip_{clip.clip_id}_metadata.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
