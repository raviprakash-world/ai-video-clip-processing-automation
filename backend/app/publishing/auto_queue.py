"""
Bridges the existing video-processing pipeline to the publishing queue
without modifying JobManager at all (section 33: consume the already-
generated clip, don't touch the core pipeline).

`GENERATE + QUEUE` (section 24) creates a normal processing job exactly as
the existing UI does, then spawns this watcher, which polls the job's
status and, once every clip has resolved (COMPLETED or FAILED), builds the
publish queue from whatever succeeded. A job that fails entirely still
resolves (no clips queued) rather than watching forever.
"""
from __future__ import annotations

import asyncio
import logging

from app.jobs.manager import JobManager
from app.jobs.models import ClipStatus, JobStatus
from app.publishing.queue_manager import create_queue_from_job

logger = logging.getLogger("clip_pipeline.publishing")

_POLL_SECONDS = 2
_MAX_WAIT_SECONDS = 3600 * 6  # generous ceiling so a stuck job can't be watched forever


async def watch_job_and_enqueue(
    job_manager: JobManager, job_id: str, *, enabled_platforms: list[str], interval_minutes: int | None
) -> None:
    waited = 0
    while waited < _MAX_WAIT_SECONDS:
        job = job_manager.get_job(job_id)
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            break
        all_clips_resolved = all(
            cj.status in (ClipStatus.COMPLETED, ClipStatus.FAILED, ClipStatus.CANCELLED) for cj in job.clips.values()
        )
        if all_clips_resolved and job.clips:
            break
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS
    else:
        logger.warning("Gave up waiting for job %s to finish before queueing publishes.", job_id)
        return

    job = job_manager.get_job(job_id)
    completed = [cj for cj in job.clips.values() if cj.status == ClipStatus.COMPLETED]
    if not completed:
        logger.info("Job %s finished with no completed clips; nothing to queue for publishing.", job_id)
        return

    summary = await create_queue_from_job(job, enabled_platforms=enabled_platforms, interval_minutes=interval_minutes)
    logger.info("Auto-queue for job %s: %s", job_id, summary)
