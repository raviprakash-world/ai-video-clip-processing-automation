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


def _cleanup_old_dirs(root: Path, max_age_hours: float, *, protected_ids: frozenset[str] = frozenset()) -> list[str]:
    if max_age_hours <= 0 or not root.exists():
        return []
    cutoff = time.time() - max_age_hours * 3600
    removed: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name in protected_ids:
            continue
        try:
            is_stale = entry.stat().st_mtime < cutoff
        except OSError:
            continue
        if is_stale:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed


def cleanup_old_jobs(max_age_hours: float, *, protected_ids: frozenset[str] = frozenset()) -> list[str]:
    """Delete job directories older than max_age_hours. Returns removed job_ids.

    No-op if max_age_hours <= 0. `protected_ids` (e.g. jobs with a still-running
    processing task) are never removed regardless of their directory's age --
    never pull storage out from under a job that's actively writing to it.
    """
    return _cleanup_old_dirs(settings.JOBS_DIR, max_age_hours, protected_ids=protected_ids)


def cleanup_old_uploads(max_age_hours: float, *, protected_ids: frozenset[str] = frozenset()) -> list[str]:
    """Delete upload directories (raw source videos) older than max_age_hours."""
    return _cleanup_old_dirs(settings.UPLOADS_DIR, max_age_hours, protected_ids=protected_ids)
