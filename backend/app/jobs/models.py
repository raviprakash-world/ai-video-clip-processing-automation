"""Job / clip-job state machine (section 17)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.schemas.processing_config import ProcessingConfig
from app.schemas.validated import ValidatedClip
from app.video_metadata.probe import VideoMetadata


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    VALIDATING = "VALIDATING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ClipStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class ClipJob:
    clip: ValidatedClip
    status: ClipStatus = ClipStatus.QUEUED
    progress_pct: float = 0.0
    output_file: Optional[str] = None
    error: Optional[dict] = None

    def to_public_dict(self) -> dict:
        return {
            "clip_id": self.clip.clip_id,
            "rank": self.clip.rank,
            "start_time": self.clip.start_time,
            "end_time": self.clip.end_time,
            "duration_seconds": self.clip.duration_seconds,
            "viral_score": self.clip.viral_score,
            "status": self.status.value,
            "progress_pct": round(self.progress_pct, 1),
            "output_file": self.output_file,
            "error": self.error,
        }


@dataclass
class ProcessingJob:
    job_id: str
    idempotency_key: str
    config: ProcessingConfig
    clips: dict[str, ClipJob]
    status: JobStatus = JobStatus.QUEUED
    source_video: Optional[VideoMetadata] = None
    created_at: float = field(default_factory=time.time)
    error: Optional[dict] = None
    reused: bool = False

    def to_public_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "reused": self.reused,
            "source_video": (
                {
                    "duration_seconds": self.source_video.duration_seconds,
                    "width": self.source_video.width,
                    "height": self.source_video.height,
                    "fps": self.source_video.fps,
                    "video_codec": self.source_video.video_codec,
                    "audio_codec": self.source_video.audio_codec,
                    "audio_present": self.source_video.audio_present,
                }
                if self.source_video
                else None
            ),
            "config": self.config.model_dump(mode="json"),
            "clips": [c.to_public_dict() for c in sorted(self.clips.values(), key=lambda c: c.clip.rank)],
            "error": self.error,
        }
