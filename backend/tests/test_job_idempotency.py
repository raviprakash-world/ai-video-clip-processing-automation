"""
Idempotency (section 21) should skip reprocessing an identical
(source, clips, config) submission -- but only while the previous run's
actual output files are still on disk. A cache entry that says COMPLETED
but whose files were removed (retention cleanup, manual deletion, disk
loss) must not be trusted; the real bug this guards against: resubmitting
after such a cleanup silently "succeeded" with a filename that doesn't
exist, instead of reprocessing.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.jobs.manager import JobManager
from app.jobs.models import JobStatus
from app.json_validation.validator import validate_analysis_json
from app.schemas.processing_config import ProcessingConfig
from app.storage.paths import job_dir

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available on PATH")

ANALYSIS = {
    "schema_version": "1.0",
    "analysis": {"source_type": "video", "language": "en", "total_clips_found": 1, "analysis_status": "complete"},
    "clips": [
        {
            "clip_id": "clip_001", "rank": 1, "start_time": "00:00:00", "end_time": "00:00:02",
            "duration_seconds": 2, "viral_score": 50, "category": "test", "speaker": "n/a",
            "hook": "h", "title_options": {}, "caption": "c", "hashtags": [], "reason": "r",
            "payoff": "p", "context_warning": None, "copyright_warning": None,
        }
    ],
}


def _make_synthetic_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=15",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


async def _wait_for_terminal(job, timeout=30):
    for _ in range(int(timeout * 10)):
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"job did not reach a terminal state in time (status={job.status})")


@pytest.mark.asyncio
async def test_stale_completed_cache_entry_triggers_reprocessing_not_a_false_success(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(settings, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(settings, "IDEMPOTENCY_INDEX_PATH", tmp_path / "idempotency_index.json")

    manager = JobManager()
    source_path = tmp_path / "source.mp4"
    _make_synthetic_video(source_path)

    validated = validate_analysis_json(ANALYSIS)
    config = ProcessingConfig()

    job1 = await manager.create_job(upload_path=source_path, selected_clips=validated.clips, config=config)
    await _wait_for_terminal(job1)
    assert job1.status == JobStatus.COMPLETED
    assert job1.reused is False

    output_path = job_dir(job1.job_id) / job1.clips["clip_001"].output_file
    assert output_path.exists()

    # Resubmitting identical input immediately should reuse the cache.
    job2 = await manager.create_job(upload_path=source_path, selected_clips=validated.clips, config=config)
    assert job2.job_id == job1.job_id
    assert job2.reused is True

    # Now simulate the file having been cleaned up out from under the cache.
    shutil.rmtree(job_dir(job1.job_id))

    job3 = await manager.create_job(upload_path=source_path, selected_clips=validated.clips, config=config)
    await _wait_for_terminal(job3)
    assert job3.job_id != job1.job_id, "must not reuse a completed record whose output file is gone"
    assert job3.status == JobStatus.COMPLETED
    assert job3.reused is False
    new_output_path = job_dir(job3.job_id) / job3.clips["clip_001"].output_file
    assert new_output_path.exists(), "reprocessing must actually produce the file again"
