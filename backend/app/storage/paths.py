"""
Storage layout + path-traversal prevention (section 8 / 22).

Every path that will be opened for reading/writing based on an ID coming
from the outside world (job_id, clip_id, upload_id) MUST go through
`safe_join`, which resolves the final path and verifies it is still inside
the intended root before returning it.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from app.config import settings
from app.errors import NotFoundError, StorageError, VideoNotFoundError

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_id(value: str, *, kind: str = "id") -> str:
    if not _ID_RE.match(value or ""):
        raise NotFoundError(f"Invalid {kind}.")
    return value


def safe_join(root: Path, *parts: str) -> Path:
    candidate = root.joinpath(*parts).resolve()
    root_resolved = root.resolve()
    if root_resolved != candidate and root_resolved not in candidate.parents:
        raise NotFoundError("Requested path is outside the allowed storage root.")
    return candidate


def job_dir(job_id: str) -> Path:
    validate_id(job_id, kind="job_id")
    return safe_join(settings.JOBS_DIR, job_id)


def upload_dir(upload_id: str) -> Path:
    validate_id(upload_id, kind="upload_id")
    return safe_join(settings.UPLOADS_DIR, upload_id)


def find_upload_source(upload_id: str) -> Path:
    """Locate the previously-uploaded source file inside its upload dir (named source.<ext>)."""
    directory = upload_dir(upload_id)
    matches = sorted(directory.glob("source.*")) if directory.exists() else []
    if not matches:
        raise VideoNotFoundError(f"No uploaded video found for upload_id '{upload_id}'.")
    return matches[0]


def check_storage_quota(incoming_bytes: int) -> None:
    total = sum(f.stat().st_size for f in settings.STORAGE_ROOT.rglob("*") if f.is_file())
    if total + incoming_bytes > settings.MAX_TOTAL_STORAGE_BYTES:
        raise StorageError(
            "Storage quota exceeded. Delete old jobs/uploads or increase MAX_TOTAL_STORAGE_MB.",
            details={"used_bytes": total, "limit_bytes": settings.MAX_TOTAL_STORAGE_BYTES},
        )


def cleanup_old_jobs(max_age_hours: int) -> list[str]:
    """Delete job directories older than max_age_hours. Returns removed job_ids. No-op if max_age_hours <= 0."""
    if max_age_hours <= 0:
        return []
    cutoff = time.time() - max_age_hours * 3600
    removed: list[str] = []
    for entry in settings.JOBS_DIR.iterdir():
        if not entry.is_dir():
            continue
        if entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed
