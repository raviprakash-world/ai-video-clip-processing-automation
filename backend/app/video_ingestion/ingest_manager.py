"""
Tracks the progress of a server-side video download (direct URL or YouTube)
so the frontend can show a real, non-fake progress bar (section 18: "Do not
fake progress" applies just as much to ingestion as to ffmpeg encoding).

The browser can watch its own upload progress for a plain file upload (via
XHR's upload.onprogress) without any of this -- this manager exists only for
the cases where the bytes move server-to-server, invisible to the browser.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import settings
from app.errors import AppError, NotFoundError
from app.storage.paths import check_storage_quota, upload_dir
from app.video_ingestion.remote_source import DirectUrlSource, YouTubeSource, is_youtube_url
from app.video_metadata.probe import VideoMetadata, probe_video


@dataclass
class IngestState:
    ingest_id: str
    status: str = "downloading"  # downloading | completed | failed
    created_at: float = field(default_factory=time.time)
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    upload_id: Optional[str] = None
    filename: Optional[str] = None
    source_type: Optional[str] = None
    video: Optional[dict] = None
    error: Optional[dict] = None

    @property
    def progress_pct(self) -> Optional[float]:
        if self.total_bytes:
            return round(min(100.0, (self.downloaded_bytes / self.total_bytes) * 100), 1)
        return None

    def to_public_dict(self) -> dict:
        return {
            "ingest_id": self.ingest_id,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "upload_id": self.upload_id,
            "filename": self.filename,
            "source_type": self.source_type,
            "video": self.video,
            "error": self.error,
        }


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


class IngestManager:
    def __init__(self) -> None:
        self._states: dict[str, IngestState] = {}
        self._semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)

    def get(self, ingest_id: str) -> IngestState:
        state = self._states.get(ingest_id)
        if not state:
            raise NotFoundError(f"No ingest found with id '{ingest_id}'.")
        return state

    def start(self, url: str) -> IngestState:
        check_storage_quota(settings.MAX_UPLOAD_SIZE_BYTES)
        ingest_id = uuid.uuid4().hex[:16]
        state = IngestState(ingest_id=ingest_id)
        self._states[ingest_id] = state
        asyncio.create_task(self._run(state, url))
        return state

    async def _run(self, state: IngestState, url: str) -> None:
        async with self._semaphore:
            new_upload_id = uuid.uuid4().hex[:16]
            dest_dir = upload_dir(new_upload_id)

            source = YouTubeSource(url) if is_youtube_url(url) else DirectUrlSource(url)

            def on_progress(downloaded: int, total: Optional[int]) -> None:
                state.downloaded_bytes = downloaded
                state.total_bytes = total

            try:
                source_path = await source.obtain(dest_dir, on_progress=on_progress)
                metadata = await probe_video(source_path)
            except AppError as exc:
                for f in dest_dir.glob("*"):
                    f.unlink(missing_ok=True)
                state.status = "failed"
                state.error = exc.to_dict()
                return
            except Exception as exc:  # noqa: BLE001 - surface any unexpected failure as a clear ingest error
                for f in dest_dir.glob("*"):
                    f.unlink(missing_ok=True)
                state.status = "failed"
                state.error = {"error": "VIDEO_DOWNLOAD_FAILED", "message": str(exc), "details": {}}
                return

            state.status = "completed"
            state.upload_id = new_upload_id
            state.filename = source_path.name
            state.source_type = source.source_type
            state.video = _video_dict(metadata)
            state.downloaded_bytes = state.total_bytes or state.downloaded_bytes

    def purge_old(self, max_age_hours: float) -> int:
        """Drop finished (completed/failed) ingest records older than max_age_hours
        so this dict doesn't grow unbounded over a long-running process. An
        in-progress download is never touched regardless of age."""
        cutoff = time.time() - max_age_hours * 3600
        stale = [
            ingest_id
            for ingest_id, state in self._states.items()
            if state.status in ("completed", "failed") and state.created_at < cutoff
        ]
        for ingest_id in stale:
            self._states.pop(ingest_id, None)
        return len(stale)


ingest_manager = IngestManager()
