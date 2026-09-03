"""Central, environment-driven configuration. No secrets, no magic numbers scattered in modules."""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


def _float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val else default


class Settings:
    # Storage
    STORAGE_ROOT: Path = Path(os.environ.get("STORAGE_ROOT", "storage")).resolve()
    UPLOADS_DIR: Path = STORAGE_ROOT / "uploads"
    JOBS_DIR: Path = STORAGE_ROOT / "jobs"
    IDEMPOTENCY_INDEX_PATH: Path = STORAGE_ROOT / "idempotency_index.json"

    # Limits
    MAX_UPLOAD_SIZE_BYTES: int = _int("MAX_UPLOAD_SIZE_MB", 2048) * 1024 * 1024
    MAX_TOTAL_STORAGE_BYTES: int = _int("MAX_TOTAL_STORAGE_MB", 20480) * 1024 * 1024
    PROCESSING_TIMEOUT_SECONDS: int = _int("PROCESSING_TIMEOUT_SECONDS", 900)
    DOWNLOAD_TIMEOUT_SECONDS: int = _int("DOWNLOAD_TIMEOUT_SECONDS", 300)
    MAX_CLIPS_PER_JOB: int = _int("MAX_CLIPS_PER_JOB", 50)
    MAX_DOWNLOAD_REDIRECTS: int = _int("MAX_DOWNLOAD_REDIRECTS", 5)
    YTDLP_MAX_HEIGHT: int = _int("YTDLP_MAX_HEIGHT", 1080)

    # Concurrency
    MAX_CONCURRENT_JOBS: int = _int("MAX_CONCURRENT_JOBS", 2)

    # Retention (hours). 0 disables automatic cleanup.
    RETENTION_HOURS: int = _int("RETENTION_HOURS", 0)

    # Default encoding / output config (all overridable per-job via ProcessingConfig)
    DEFAULT_OUTPUT_WIDTH: int = _int("DEFAULT_OUTPUT_WIDTH", 1080)
    DEFAULT_OUTPUT_HEIGHT: int = _int("DEFAULT_OUTPUT_HEIGHT", 1920)
    DEFAULT_VIDEO_CODEC: str = os.environ.get("DEFAULT_VIDEO_CODEC", "libx264")
    DEFAULT_AUDIO_CODEC: str = os.environ.get("DEFAULT_AUDIO_CODEC", "aac")
    DEFAULT_CRF: int = _int("DEFAULT_CRF", 21)
    DEFAULT_FPS: int = _int("DEFAULT_FPS", 0)  # 0 = keep source fps

    ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    SUPPORTED_SCHEMA_VERSIONS = ("1.0",)


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
settings.JOBS_DIR.mkdir(parents=True, exist_ok=True)
