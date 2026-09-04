"""
PublishingWorker (section 35).

Every cycle: find due items, atomically claim one, verify idempotency,
validate the account and media, publish, record the outcome, continue.
MAX_CONCURRENT_PUBLISHES defaults to 1 (section 36) -- deliberately
conservative. A single platform's failure never touches other platforms'
items (section 18); a single clip's failure never blocks the rest of the
queue (section 18) -- each queue item is claimed, processed, and resolved
completely independently.

The atomic claim (`UPDATE ... WHERE id=? AND status=?`, checking rowcount)
is what makes this restart- and multi-worker-safe (sections 16/21/35): only
one process's UPDATE can ever match a given row's current status.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update

from app.config import settings
from app.db.base import session_scope
from app.db.models import PublishingAttempt, PublishQueueItem, SocialAccount
from app.errors import AppError
from app.publishing.metadata import PublishMetadata
from app.publishing.queue_manager import has_already_published
from app.publishing.registry import get_provider
from app.publishing.states import ErrorClass, PublishError, QueueStatus
from app.video_metadata.probe import probe_video

logger = logging.getLogger("clip_pipeline.publishing")

_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_PUBLISHES)


async def publishing_worker_loop() -> None:
    while True:
        try:
            await run_worker_cycle()
        except Exception:  # noqa: BLE001 - a cycle failure must never kill the loop
            logger.exception("Publishing worker cycle failed; will retry on the next poll.")
        await asyncio.sleep(max(5, settings.PUBLISHING_WORKER_POLL_SECONDS))


async def run_worker_cycle() -> int:
    """Claims and processes every currently-due item. Returns how many were processed."""
    processed = 0
    while True:
        item = await _claim_due_item()
        if item is None:
            break
        await _process_item(item)
        processed += 1
    return processed


async def _claim_due_item() -> PublishQueueItem | None:
    now = datetime.now(timezone.utc)
    async with session_scope() as session:
        stmt = (
            select(PublishQueueItem)
            .where(
                PublishQueueItem.status.in_([s.value for s in QueueStatus.due_statuses()]),
                PublishQueueItem.scheduled_at <= now,
            )
            .order_by(PublishQueueItem.scheduled_at)
            .limit(5)
        )
        candidates = list((await session.execute(stmt)).scalars().all())

        for candidate in candidates:
            claim = await session.execute(
                update(PublishQueueItem)
                .where(PublishQueueItem.id == candidate.id, PublishQueueItem.status == candidate.status)
                .values(status=QueueStatus.PUBLISHING.value)
            )
            if claim.rowcount == 1:
                candidate.status = QueueStatus.PUBLISHING.value
                return candidate
    return None


async def _process_item(item: PublishQueueItem) -> None:
    async with _semaphore:
        try:
            await _publish_one(item)
        except Exception:  # noqa: BLE001 - isolate this item's failure from the rest of the cycle
            logger.exception("Unexpected error processing queue item %s (%s/%s)", item.id, item.clip_id, item.platform)
            await _set_status(item.id, QueueStatus.FAILED, error="Internal error while processing this item.")


async def _publish_one(item: PublishQueueItem) -> None:
    if await has_already_published(item.idempotency_key):
        await _set_status(item.id, QueueStatus.CANCELLED, error="Already published (idempotency skip).")
        return

    video_path = Path(item.output_file_path)
    validation_error = await _pre_publish_validate(video_path)
    if validation_error:
        await _set_status(item.id, QueueStatus.FAILED, error=validation_error)
        return

    account = await _get_connected_account(item.platform)
    if account is None:
        await _set_status(item.id, QueueStatus.AUTH_REQUIRED, error=f"No connected {item.platform} account.")
        return

    metadata = PublishMetadata(
        title=item.metadata_json.get("title"),
        caption=item.metadata_json.get("caption"),
        hashtags=item.metadata_json.get("hashtags") or [],
    )
    provider = get_provider(item.platform)
    public_url = _public_url_for(item) if item.platform == "instagram" else None
    attempt_number = item.attempt_count + 1
    started_at = datetime.now(timezone.utc)

    logger.info("Publish started: clip=%s platform=%s attempt=%d", item.clip_id, item.platform, attempt_number)

    try:
        result = await provider.publish(account, metadata, video_path, public_url=public_url)
    except PublishError as exc:
        await _record_attempt(item.id, attempt_number, outcome="FAILED", error=exc.message, http_status=exc.http_status, started_at=started_at)
        await _handle_failure(item.id, exc, attempt_number)
        return
    except Exception as exc:  # noqa: BLE001 - a provider bug is still a failure, not a crash
        logger.exception("Unhandled exception from %s provider", item.platform)
        await _record_attempt(item.id, attempt_number, outcome="FAILED", error=str(exc), http_status=None, started_at=started_at)
        await _handle_failure(item.id, PublishError(str(exc), error_class=ErrorClass.TRANSIENT), attempt_number)
        return

    await _record_attempt(item.id, attempt_number, outcome="SUCCESS", error=None, http_status=None, started_at=started_at)
    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item.id)
        if db_item:
            db_item.status = QueueStatus.PUBLISHED.value
            db_item.external_post_id = result.external_post_id
            db_item.published_at = datetime.now(timezone.utc)
            db_item.attempt_count = attempt_number
            db_item.last_error = None
    logger.info("Publish succeeded: clip=%s platform=%s external_id=%s", item.clip_id, item.platform, result.external_post_id)


async def _handle_failure(item_id: str, exc: PublishError, attempt_number: int) -> None:
    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item_id)
        if not db_item:
            return
        db_item.attempt_count = attempt_number
        db_item.last_error = exc.message

        if exc.error_class == ErrorClass.AUTH:
            db_item.status = QueueStatus.AUTH_REQUIRED.value
        elif exc.error_class == ErrorClass.UNSUPPORTED:
            db_item.status = QueueStatus.NOT_SUPPORTED.value
        elif exc.error_class == ErrorClass.QUOTA:
            db_item.status = QueueStatus.QUOTA_WAIT.value
            db_item.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=settings.DEFAULT_QUOTA_WAIT_MINUTES)
        elif exc.error_class == ErrorClass.TRANSIENT and attempt_number < settings.MAX_PUBLISH_ATTEMPTS:
            delays = settings.RETRY_DELAYS_MINUTES
            delay = delays[min(attempt_number, len(delays) - 1)]
            db_item.status = QueueStatus.RETRYING.value
            db_item.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=delay)
        else:
            db_item.status = QueueStatus.FAILED.value

        logger.warning(
            "Publish failed: item=%s platform=%s attempt=%d class=%s -> %s (%s)",
            item_id, db_item.platform, attempt_number, exc.error_class.value, db_item.status, exc.message,
        )


async def _record_attempt(
    queue_item_id: str, attempt_number: int, *, outcome: str, error: str | None, http_status: int | None, started_at: datetime
) -> None:
    async with session_scope() as session:
        session.add(
            PublishingAttempt(
                queue_item_id=queue_item_id,
                attempt_number=attempt_number,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                outcome=outcome,
                error=error,
                http_status=http_status,
            )
        )


async def _set_status(item_id: str, status: QueueStatus, *, error: str | None) -> None:
    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item_id)
        if db_item:
            db_item.status = status.value
            db_item.last_error = error


async def _get_connected_account(platform: str) -> SocialAccount | None:
    async with session_scope() as session:
        stmt = (
            select(SocialAccount)
            .where(SocialAccount.platform == platform, SocialAccount.status == "CONNECTED")
            .order_by(SocialAccount.updated_at.desc())
        )
        return (await session.execute(stmt)).scalars().first()


def _public_url_for(item: PublishQueueItem) -> str | None:
    if not settings.PUBLIC_BASE_URL:
        return None
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/jobs/{item.job_id}/clips/{item.clip_id}/file"


async def _pre_publish_validate(video_path: Path) -> str | None:
    """Section 34's checklist, reusing the same ffprobe machinery the core
    pipeline already uses for output validation -- no new video-inspection code."""
    if not video_path.exists():
        return f"Output file no longer exists: {video_path.name}"
    if not video_path.is_file() or video_path.stat().st_size == 0:
        return f"Output file is empty or unreadable: {video_path.name}"
    try:
        meta = await probe_video(video_path)
    except AppError as exc:
        return f"Output file failed validation: {exc.message}"
    if meta.duration_seconds <= 0 or meta.width <= 0 or meta.height <= 0:
        return "Output file has invalid duration or dimensions."
    return None
