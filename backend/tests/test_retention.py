"""Retention/cleanup sweep (section 8/21): old job/upload directories get
deleted automatically so storage doesn't grow without bound, but a
still-active job is never touched regardless of its directory's age."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.jobs.manager import JobManager
from app.storage.paths import cleanup_old_jobs, cleanup_old_uploads


def _make_old_dir(root, name: str, age_hours: float):
    d = root / name
    d.mkdir(parents=True)
    (d / "file.txt").write_text("x")
    old_time = time.time() - age_hours * 3600
    os.utime(d, (old_time, old_time))
    return d


def test_cleanup_old_jobs_removes_only_stale_dirs(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOBS_DIR", tmp_path)
    _make_old_dir(tmp_path, "old_job", age_hours=48)
    _make_old_dir(tmp_path, "fresh_job", age_hours=1)

    removed = cleanup_old_jobs(24)

    assert removed == ["old_job"]
    assert not (tmp_path / "old_job").exists()
    assert (tmp_path / "fresh_job").exists()


def test_cleanup_old_jobs_never_touches_protected_ids(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOBS_DIR", tmp_path)
    _make_old_dir(tmp_path, "old_but_active", age_hours=48)

    removed = cleanup_old_jobs(24, protected_ids=frozenset({"old_but_active"}))

    assert removed == []
    assert (tmp_path / "old_but_active").exists()


def test_cleanup_old_jobs_disabled_when_max_age_zero(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOBS_DIR", tmp_path)
    _make_old_dir(tmp_path, "old_job", age_hours=999)

    assert cleanup_old_jobs(0) == []
    assert (tmp_path / "old_job").exists()


def test_cleanup_old_uploads_removes_stale_dirs(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path)
    _make_old_dir(tmp_path, "old_upload", age_hours=48)

    removed = cleanup_old_uploads(24)

    assert removed == ["old_upload"]
    assert not (tmp_path / "old_upload").exists()


@pytest.mark.asyncio
async def test_purge_expired_evicts_in_memory_state_and_idempotency_entry(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(settings, "IDEMPOTENCY_INDEX_PATH", tmp_path.parent / "idempotency_index.json")

    manager = JobManager()
    _make_old_dir(tmp_path, "stale_job", age_hours=48)

    # Seed in-memory state as if "stale_job" had completed and been indexed.
    from app.jobs.models import JobStatus, ProcessingJob
    from app.schemas.processing_config import ProcessingConfig

    fake_job = ProcessingJob(
        job_id="stale_job", idempotency_key="fakekey", config=ProcessingConfig(), clips={}, status=JobStatus.COMPLETED,
    )
    manager._jobs["stale_job"] = fake_job
    manager._idempotency_index["fakekey"] = "stale_job"

    removed = await manager.purge_expired(24)

    assert removed == ["stale_job"]
    assert "stale_job" not in manager._jobs
    assert "fakekey" not in manager._idempotency_index


@pytest.mark.asyncio
async def test_purge_expired_skips_job_with_running_task(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(settings, "IDEMPOTENCY_INDEX_PATH", tmp_path.parent / "idempotency_index2.json")

    manager = JobManager()
    _make_old_dir(tmp_path, "still_running", age_hours=48)

    async def _never_ending():
        await asyncio.sleep(10)

    manager._tasks["still_running"] = asyncio.create_task(_never_ending())
    try:
        removed = await manager.purge_expired(24)
        assert removed == []
        assert (tmp_path / "still_running").exists()
    finally:
        manager._tasks["still_running"].cancel()
