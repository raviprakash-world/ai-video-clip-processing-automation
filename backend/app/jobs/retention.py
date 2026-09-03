"""
Background retention sweep (section 8/21): "clean temporary files after
successful completion according to the configured retention policy."

Runs as an asyncio task for the lifetime of the FastAPI process (see
app.main's lifespan) rather than relying on an external OS cron entry --
no extra permissions or platform-specific setup required. It runs once
immediately at startup (so anything already stale from before a restart
gets swept right away) and then on a fixed interval.

If you'd rather have cleanup happen even while the server isn't running,
see scripts/cleanup_expired.py for a standalone equivalent you can wire
into an actual system cron/launchd job yourself.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.jobs.manager import job_manager
from app.storage.paths import cleanup_old_uploads
from app.video_ingestion.ingest_manager import ingest_manager

logger = logging.getLogger("clip_pipeline.retention")


async def run_once() -> dict:
    removed_jobs = await job_manager.purge_expired(settings.RETENTION_HOURS)
    removed_uploads = cleanup_old_uploads(settings.RETENTION_HOURS)
    removed_ingest_records = ingest_manager.purge_old(max_age_hours=1)

    if removed_jobs or removed_uploads:
        logger.info(
            "Retention sweep: removed %d job dir(s), %d upload dir(s) older than %dh (%d stale ingest record(s) pruned).",
            len(removed_jobs), len(removed_uploads), settings.RETENTION_HOURS, removed_ingest_records,
        )
    return {"removed_jobs": removed_jobs, "removed_uploads": removed_uploads}


async def retention_loop() -> None:
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            logger.exception("Retention sweep failed; will retry on the next interval.")
        await asyncio.sleep(max(60, settings.RETENTION_CHECK_INTERVAL_MINUTES * 60))
