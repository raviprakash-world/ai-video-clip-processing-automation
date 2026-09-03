"""Upload endpoint for a user's own overlay image, used only by watermark.mode=authorized_overlay."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from app.config import settings
from app.errors import UnsupportedVideoFormatError

router = APIRouter(prefix="/api/watermark", tags=["watermark"])

_ALLOWED_IMAGE_EXTENSIONS = {".png"}
_MAX_OVERLAY_BYTES = 10 * 1024 * 1024


@router.post("/upload")
async def upload_overlay(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        raise UnsupportedVideoFormatError(
            f"Unsupported overlay image extension '{suffix or '(none)'}'. Only PNG (with transparency) is supported."
        )

    asset_id = uuid.uuid4().hex[:16]
    dest_path = settings.UPLOADS_DIR / f"{asset_id}.png"

    size = 0
    with dest_path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_OVERLAY_BYTES:
                out.close()
                dest_path.unlink(missing_ok=True)
                raise UnsupportedVideoFormatError("Overlay image exceeds the 10 MB limit.")
            out.write(chunk)

    return {"overlay_asset_id": asset_id}
