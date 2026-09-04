"""
Publishing-queue CRUD and state transitions (sections 8, 16, 20, 23, 32, 38, 39).

This module owns the database side of the queue. It never talks to a
PublishingProvider directly -- that's worker.py's job -- and it never
re-runs ffmpeg or regenerates a clip (section 33): it only ever reads an
already-COMPLETED ClipJob's output_file.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.base import session_scope
from app.db.models import PublishQueueItem
from app.jobs.models import ClipStatus, ProcessingJob
from app.publishing.metadata import build_publish_metadata
from app.publishing.queue_scheduler import assign_slots, order_clips, reschedule_from_now
from app.publishing.registry import PLATFORMS
from app.publishing.states import QueueStatus
from app.storage.paths import job_dir

logger = logging.getLogger("clip_pipeline.publishing")


def _idempotency_key(job_id: str, clip_id: str, platform: str) -> str:
    return f"{job_id}:{clip_id}:{platform}"


async def create_queue_from_job(
    job: ProcessingJob,
    *,
    enabled_platforms: list[str],
    interval_minutes: int | None = None,
    block_warnings: bool | None = None,
) -> dict:
    """Build publish_queue rows for every COMPLETED clip in `job`, ordered by
    rank/viral_score, one slot per clip shared across every enabled platform.

    Idempotent: calling this twice for the same job/platforms never creates
    duplicate rows (unique idempotency_key constraint absorbs the retry).
    """
    interval_minutes = interval_minutes if interval_minutes is not None else settings.PUBLISH_INTERVAL_MINUTES
    block_warnings = settings.BLOCK_WARNINGS if block_warnings is None else block_warnings
    enabled_platforms = [p for p in enabled_platforms if p in PLATFORMS]

    completed_clips = [cj.clip for cj in job.clips.values() if cj.status == ClipStatus.COMPLETED]
    ordered = order_clips(completed_clips)

    queued_clip_ids: list[str] = []
    blocked: list[dict] = []
    eligible = []
    for clip in ordered:
        if block_warnings and clip.copyright_warning:
            blocked.append({"clip_id": clip.clip_id, "reason": f"copyright_warning: {clip.copyright_warning}"})
            continue
        eligible.append(clip)

    slots = assign_slots(eligible, start_at=datetime.now(timezone.utc), interval_minutes=interval_minutes)
    jdir = job_dir(job.job_id)

    created_count = 0
    async with session_scope() as session:
        for clip in eligible:
            clip_job = job.clips[clip.clip_id]
            output_path = jdir / clip_job.output_file
            snapshot = build_publish_metadata(clip)
            scheduled_at = slots[clip.clip_id]

            any_created_for_clip = False
            for platform in enabled_platforms:
                key = _idempotency_key(job.job_id, clip.clip_id, platform)
                item = PublishQueueItem(
                    job_id=job.job_id,
                    clip_id=clip.clip_id,
                    platform=platform,
                    idempotency_key=key,
                    scheduled_at=scheduled_at,
                    status=QueueStatus.WAITING.value,
                    output_file_path=str(output_path),
                    metadata_json={
                        "title": snapshot.title,
                        "caption": snapshot.caption,
                        "hashtags": snapshot.hashtags,
                        "context_warning": clip.context_warning,
                        "rank": clip.rank,
                        "viral_score": clip.viral_score,
                    },
                )
                session.add(item)
                try:
                    await session.flush()
                    created_count += 1
                    any_created_for_clip = True
                except IntegrityError:
                    # Already queued for this job/clip/platform -- idempotent no-op.
                    await session.rollback()
            if any_created_for_clip:
                queued_clip_ids.append(clip.clip_id)

    logger.info(
        "Publishing queue created for job %s: %d item(s) across %d clip(s), %d blocked by warnings.",
        job.job_id, created_count, len(queued_clip_ids), len(blocked),
    )
    return {"queued_clips": queued_clip_ids, "blocked_clips": blocked, "items_created": created_count}


async def list_queue(job_id: str | None = None) -> list[PublishQueueItem]:
    async with session_scope() as session:
        stmt = select(PublishQueueItem).order_by(PublishQueueItem.scheduled_at)
        if job_id:
            stmt = stmt.where(PublishQueueItem.job_id == job_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_item(item_id: str) -> PublishQueueItem | None:
    async with session_scope() as session:
        return await session.get(PublishQueueItem, item_id)


async def has_already_published(idempotency_key: str) -> bool:
    """Section 20's explicit pre-publish guard, on top of the DB unique
    constraint and the worker's atomic row claim (defense in depth)."""
    async with session_scope() as session:
        stmt = select(PublishQueueItem.id).where(
            PublishQueueItem.idempotency_key == idempotency_key, PublishQueueItem.status == QueueStatus.PUBLISHED.value
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def pause_job_queue(job_id: str) -> int:
    async with session_scope() as session:
        result = await session.execute(
            update(PublishQueueItem)
            .where(
                PublishQueueItem.job_id == job_id,
                PublishQueueItem.status.in_([s.value for s in QueueStatus.due_statuses()]),
            )
            .values(status=QueueStatus.PAUSED.value)
        )
        return result.rowcount or 0


async def resume_job_queue(job_id: str, *, interval_minutes: int | None = None) -> int:
    interval_minutes = interval_minutes if interval_minutes is not None else settings.PUBLISH_INTERVAL_MINUTES
    async with session_scope() as session:
        stmt = (
            select(PublishQueueItem)
            .where(PublishQueueItem.job_id == job_id, PublishQueueItem.status == QueueStatus.PAUSED.value)
            .order_by(PublishQueueItem.scheduled_at)
        )
        result = await session.execute(stmt)
        pending = list(result.scalars().all())
        if not pending:
            return 0

        # Group by clip so every platform for the same clip keeps the same slot,
        # same as initial scheduling (section 26).
        clip_order: list[str] = []
        for item in pending:
            if item.clip_id not in clip_order:
                clip_order.append(item.clip_id)
        slots = reschedule_from_now(clip_order, now=datetime.now(timezone.utc), interval_minutes=interval_minutes)

        for item in pending:
            item.scheduled_at = slots[item.clip_id]
            item.status = QueueStatus.WAITING.value
        return len(pending)


async def retry_item(item_id: str) -> PublishQueueItem | None:
    """Section 20/23: retrying an item that already succeeded must never cause a
    duplicate real-world post -- a PUBLISHED item is left untouched rather than
    being reverted to WAITING (the DB's idempotency_key unique constraint means
    there is exactly one row per job/clip/platform, so there is no "other" row
    left to fall back on once this one's status changes)."""
    async with session_scope() as session:
        item = await session.get(PublishQueueItem, item_id)
        if not item:
            return None
        if item.status == QueueStatus.PUBLISHED.value:
            return item
        item.status = QueueStatus.WAITING.value
        item.scheduled_at = datetime.now(timezone.utc)
        item.attempt_count = 0
        item.last_error = None
        return item


async def skip_item(item_id: str) -> PublishQueueItem | None:
    async with session_scope() as session:
        item = await session.get(PublishQueueItem, item_id)
        if not item:
            return None
        item.status = QueueStatus.CANCELLED.value
        item.last_error = "Skipped by user."
        return item


async def cancel_item(item_id: str) -> PublishQueueItem | None:
    async with session_scope() as session:
        item = await session.get(PublishQueueItem, item_id)
        if not item:
            return None
        item.status = QueueStatus.CANCELLED.value
        return item


async def publish_now(item_id: str) -> PublishQueueItem | None:
    async with session_scope() as session:
        item = await session.get(PublishQueueItem, item_id)
        if not item:
            return None
        if item.status not in (s.value for s in QueueStatus.due_statuses()) and item.status != QueueStatus.PAUSED.value:
            return item
        item.scheduled_at = datetime.now(timezone.utc)
        item.status = QueueStatus.WAITING.value
        return item
