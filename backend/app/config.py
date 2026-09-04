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
    MAX_CONCURRENT_DOWNLOADS: int = _int("MAX_CONCURRENT_DOWNLOADS", 4)

    # Retention (hours). 0 disables automatic cleanup. Default: delete generated
    # clips/uploads a day after their job directory was created, to keep disk
    # usage bounded (section 8/21). Runs as a background sweep inside this
    # process -- see app.jobs.retention -- every RETENTION_CHECK_INTERVAL_MINUTES.
    RETENTION_HOURS: int = _int("RETENTION_HOURS", 24)
    RETENTION_CHECK_INTERVAL_MINUTES: int = _int("RETENTION_CHECK_INTERVAL_MINUTES", 60)

    # Default encoding / output config (all overridable per-job via ProcessingConfig)
    DEFAULT_OUTPUT_WIDTH: int = _int("DEFAULT_OUTPUT_WIDTH", 1080)
    DEFAULT_OUTPUT_HEIGHT: int = _int("DEFAULT_OUTPUT_HEIGHT", 1920)
    DEFAULT_VIDEO_CODEC: str = os.environ.get("DEFAULT_VIDEO_CODEC", "libx264")
    DEFAULT_AUDIO_CODEC: str = os.environ.get("DEFAULT_AUDIO_CODEC", "aac")
    DEFAULT_CRF: int = _int("DEFAULT_CRF", 21)
    DEFAULT_FPS: int = _int("DEFAULT_FPS", 0)  # 0 = keep source fps

    ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    SUPPORTED_SCHEMA_VERSIONS = ("1.0",)

    # -- Publishing queue -------------------------------------------------------
    DATABASE_PATH: Path = STORAGE_ROOT / "app.db"
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "") or f"sqlite+aiosqlite:///{DATABASE_PATH}"

    PUBLISH_INTERVAL_MINUTES: int = _int("PUBLISH_INTERVAL_MINUTES", 60)
    MAX_CONCURRENT_PUBLISHES: int = _int("MAX_CONCURRENT_PUBLISHES", 1)  # section 36: deliberately conservative
    PUBLISHING_WORKER_POLL_SECONDS: int = _int("PUBLISHING_WORKER_POLL_SECONDS", 30)
    MAX_PUBLISH_ATTEMPTS: int = _int("MAX_PUBLISH_ATTEMPTS", 3)
    # Delay before each attempt (index 0 = first attempt, no delay). Section 19.
    RETRY_DELAYS_MINUTES: tuple[int, ...] = (0, 5, 30)
    DEFAULT_QUOTA_WAIT_MINUTES: int = _int("DEFAULT_QUOTA_WAIT_MINUTES", 60)

    # Section 39: a clip carrying a meaningful copyright_warning is blocked from
    # auto-publishing by default. This is a safety policy, not a legal opinion --
    # operators who understand their own rights situation can disable it.
    BLOCK_WARNINGS: bool = os.environ.get("BLOCK_WARNINGS", "true").lower() not in ("false", "0", "")

    # Fernet key for encrypting OAuth tokens at rest. MUST be set via env in any
    # real deployment -- an ephemeral key generated at import time means tokens
    # become undecryptable the moment the process restarts. See app.publishing.token_crypto.
    TOKEN_ENCRYPTION_KEY: str = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

    # Google OAuth (YouTube Data API v3). Create these in Google Cloud Console:
    # APIs & Services -> Credentials -> OAuth client ID (Web application), with
    # the YouTube Data API v3 enabled on the project.
    GOOGLE_OAUTH_CLIENT_ID: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    GOOGLE_OAUTH_REDIRECT_URI: str = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8077/api/publishing/youtube/callback"
    )

    # Meta OAuth (Facebook Login, used for both Facebook Page publishing and
    # Instagram Content Publishing via a linked Page). Create at
    # developers.facebook.com -> your App -> Facebook Login product.
    META_APP_ID: str = os.environ.get("META_APP_ID", "")
    META_APP_SECRET: str = os.environ.get("META_APP_SECRET", "")
    META_OAUTH_REDIRECT_URI: str = os.environ.get(
        "META_OAUTH_REDIRECT_URI", "http://localhost:8077/api/publishing/meta/callback"
    )

    # Instagram's Content Publishing API requires a publicly-fetchable video_url
    # (Meta's servers pull the file themselves) -- there is no direct-binary-upload
    # option for Reels. Set this to your server's real public HTTPS origin to
    # enable Instagram; leave empty to keep it reported as NOT_AVAILABLE. Facebook
    # Page video publishing does NOT need this (it accepts direct binary upload).
    PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "")


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
settings.JOBS_DIR.mkdir(parents=True, exist_ok=True)
