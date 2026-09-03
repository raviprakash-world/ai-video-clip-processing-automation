from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.errors import AppError, StorageError, UnsupportedVideoFormatError
from app.storage.paths import check_storage_quota, find_upload_source, upload_dir
from app.video_ingestion.ingest_manager import ingest_manager
from app.video_metadata.probe import VideoMetadata, probe_video

router = APIRouter(prefix="/api/video", tags=["video"])


def _video_dict(metadata: VideoMetadata) -> dict:
    return {
        "duration_seconds": metadata.duration_seconds,
        "width": metadata.width,
        "height": metadata.height,
        "fps": metadata.fps,
        "video_codec": metadata.video_codec,
        "audio_codec": metadata.audio_codec,
        "audio_present": metadata.audio_present,
        "size_bytes": metadata.size_bytes,
    }


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise UnsupportedVideoFormatError(
            f"Unsupported file extension '{suffix or '(none)'}'. "
            f"Allowed: {', '.join(sorted(settings.ALLOWED_UPLOAD_EXTENSIONS))}."
        )

    check_storage_quota(settings.MAX_UPLOAD_SIZE_BYTES)

    upload_id = uuid.uuid4().hex[:16]
    dest_dir = upload_dir(upload_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"source{suffix}"

    size = 0
    try:
        with dest_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.MAX_UPLOAD_SIZE_BYTES:
                    raise StorageError(
                        f"Upload exceeds the maximum allowed size of "
                        f"{settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
                    )
                out.write(chunk)
    except AppError:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    try:
        metadata = await probe_video(dest_path)
    except AppError:
        dest_path.unlink(missing_ok=True)
        raise

    return {"upload_id": upload_id, "filename": dest_path.name, "video": _video_dict(metadata)}


@router.get("/{upload_id}")
async def get_video_metadata(upload_id: str):
    source_path = find_upload_source(upload_id)
    metadata = await probe_video(source_path)
    return {"upload_id": upload_id, "filename": source_path.name, "video": _video_dict(metadata)}


class IngestUrlRequest(BaseModel):
    url: str


@router.post("/ingest-url")
async def start_ingest_video_url(payload: IngestUrlRequest):
    """Start ingesting a video from a remote URL (section 6/7) in the background and
    return immediately with an ingest_id to poll. See GET /ingest-url/{ingest_id} for
    progress, and app.video_ingestion.remote_source for the SSRF/size/timeout/
    authorization constraints the download itself goes through."""
    parsed = urlparse(payload.url)
    if parsed.scheme not in ("http", "https"):
        raise UnsupportedVideoFormatError(f"Unsupported URL scheme '{parsed.scheme}'. Only http/https are allowed.")

    state = ingest_manager.start(payload.url)
    return state.to_public_dict()


@router.get("/ingest-url/{ingest_id}")
async def get_ingest_progress(ingest_id: str):
    """Poll the real (not faked) download progress of a started URL ingestion."""
    return ingest_manager.get(ingest_id).to_public_dict()
