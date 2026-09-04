"""
Queue manager (sections 8, 16, 20, 21, 23, 38, 39): scheduling from a completed
job, idempotent queue creation, warning-based blocking, pause/resume, and the
per-item human-control actions. Uses the isolated_db fixture (tests/conftest.py)
so nothing here touches the real storage/app.db.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models import PublishQueueItem
from app.jobs.models import ClipJob, ClipStatus, JobStatus, ProcessingJob
from app.publishing import queue_manager
from app.publishing.states import QueueStatus
from app.schemas.processing_config import ProcessingConfig
from app.schemas.validated import ValidatedClip

pytestmark = pytest.mark.asyncio


def _clip(clip_id: str, rank: int, viral_score: int = 50, copyright_warning=None) -> ValidatedClip:
    return ValidatedClip(
        clip_id=clip_id, rank=rank, start_time="00:00:00", end_time="00:00:05",
        start_seconds=0, end_seconds=5, duration_seconds=5, ai_reported_duration_seconds=5,
        duration_mismatch=False, viral_score=viral_score, category="c", speaker="s", hook="hook",
        title_options={"curiosity": f"Title for {clip_id}"}, caption="caption", hashtags=["#t"],
        reason="r", payoff="p", context_warning=None, copyright_warning=copyright_warning,
    )


def _job(job_id: str, clip_statuses: dict[str, tuple[ValidatedClip, ClipStatus]]) -> ProcessingJob:
    clips = {}
    for clip_id, (clip, status) in clip_statuses.items():
        cj = ClipJob(clip=clip, status=status)
        if status == ClipStatus.COMPLETED:
            cj.output_file = f"{clip_id}.mp4"
        clips[clip_id] = cj
    return ProcessingJob(job_id=job_id, idempotency_key="k", config=ProcessingConfig(), clips=clips, status=JobStatus.COMPLETED)


async def test_create_queue_orders_by_rank_and_covers_every_platform(isolated_db):
    job = _job("job1", {
        "c2": (_clip("c2", rank=2), ClipStatus.COMPLETED),
        "c1": (_clip("c1", rank=1), ClipStatus.COMPLETED),
    })
    summary = await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube", "facebook"])
    assert summary["items_created"] == 4
    items = await queue_manager.list_queue("job1")
    assert len(items) == 4
    c1_items = [i for i in items if i.clip_id == "c1"]
    c2_items = [i for i in items if i.clip_id == "c2"]
    assert {i.platform for i in c1_items} == {"youtube", "facebook"}
    # same clip, both platforms share one scheduled_at slot (section 26)
    assert c1_items[0].scheduled_at == c1_items[1].scheduled_at
    # rank 1 scheduled before rank 2
    assert c1_items[0].scheduled_at < c2_items[0].scheduled_at


async def test_failed_clips_are_never_queued(isolated_db):
    job = _job("job2", {
        "ok": (_clip("ok", rank=1), ClipStatus.COMPLETED),
        "bad": (_clip("bad", rank=2), ClipStatus.FAILED),
    })
    summary = await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    assert summary["queued_clips"] == ["ok"]
    items = await queue_manager.list_queue("job2")
    assert {i.clip_id for i in items} == {"ok"}


async def test_idempotent_recreation_never_duplicates(isolated_db):
    job = _job("job3", {"c1": (_clip("c1", rank=1), ClipStatus.COMPLETED)})
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    second = await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    assert second["items_created"] == 0
    items = await queue_manager.list_queue("job3")
    assert len(items) == 1


async def test_copyright_warning_blocks_by_default(isolated_db):
    job = _job("job4", {"c1": (_clip("c1", rank=1, copyright_warning="Contains licensed music"), ClipStatus.COMPLETED)})
    summary = await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"], block_warnings=True)
    assert summary["queued_clips"] == []
    assert len(summary["blocked_clips"]) == 1
    assert summary["blocked_clips"][0]["clip_id"] == "c1"
    items = await queue_manager.list_queue("job4")
    assert items == []


async def test_copyright_warning_does_not_block_when_disabled(isolated_db):
    job = _job("job5", {"c1": (_clip("c1", rank=1, copyright_warning="Contains licensed music"), ClipStatus.COMPLETED)})
    summary = await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"], block_warnings=False)
    assert summary["queued_clips"] == ["c1"]


async def test_pause_and_resume_reschedules_only_pending_items(isolated_db):
    job = _job("job6", {
        "c1": (_clip("c1", rank=1), ClipStatus.COMPLETED),
        "c2": (_clip("c2", rank=2), ClipStatus.COMPLETED),
    })
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"], interval_minutes=60)
    items = await queue_manager.list_queue("job6")
    c1_item = next(i for i in items if i.clip_id == "c1")

    # Simulate c1 already published before the pause.
    from app.db.base import session_scope

    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, c1_item.id)
        db_item.status = QueueStatus.PUBLISHED.value
        db_item.published_at = datetime.now(timezone.utc)
        db_item.external_post_id = "yt123"

    paused = await queue_manager.pause_job_queue("job6")
    assert paused == 1  # only c2's WAITING item, not c1's now-PUBLISHED one

    resumed = await queue_manager.resume_job_queue("job6", interval_minutes=60)
    assert resumed == 1

    items_after = await queue_manager.list_queue("job6")
    c1_after = next(i for i in items_after if i.clip_id == "c1")
    c2_after = next(i for i in items_after if i.clip_id == "c2")
    assert c1_after.status == QueueStatus.PUBLISHED.value
    assert c1_after.external_post_id == "yt123"  # untouched by resume
    assert c2_after.status == QueueStatus.WAITING.value
    assert c2_after.scheduled_at >= datetime.now(timezone.utc) - timedelta(seconds=5)


async def test_retry_resets_attempts_and_reschedules_now(isolated_db):
    job = _job("job7", {"c1": (_clip("c1", rank=1), ClipStatus.COMPLETED)})
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    item = (await queue_manager.list_queue("job7"))[0]

    from app.db.base import session_scope

    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item.id)
        db_item.status = QueueStatus.FAILED.value
        db_item.attempt_count = 3
        db_item.last_error = "boom"

    retried = await queue_manager.retry_item(item.id)
    assert retried.status == QueueStatus.WAITING.value
    assert retried.attempt_count == 0
    assert retried.last_error is None


async def test_retry_refuses_to_revert_a_published_item(isolated_db):
    """Section 20: retrying an already-published item must never cause a second
    real-world post. There's only one row per idempotency_key, so once it's
    PUBLISHED, retry_item must leave it exactly as-is."""
    job = _job("job11", {"c1": (_clip("c1", rank=1), ClipStatus.COMPLETED)})
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    item = (await queue_manager.list_queue("job11"))[0]

    from app.db.base import session_scope

    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item.id)
        db_item.status = QueueStatus.PUBLISHED.value
        db_item.external_post_id = "already-live"

    result = await queue_manager.retry_item(item.id)
    assert result.status == QueueStatus.PUBLISHED.value
    assert result.external_post_id == "already-live"


async def test_skip_and_cancel_and_publish_now(isolated_db):
    job = _job("job8", {"c1": (_clip("c1", rank=1), ClipStatus.COMPLETED)})
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"], interval_minutes=60)
    item = (await queue_manager.list_queue("job8"))[0]

    skipped = await queue_manager.skip_item(item.id)
    assert skipped.status == QueueStatus.CANCELLED.value

    # publish_now on a cancelled item is a no-op (only due/paused statuses apply)
    unchanged = await queue_manager.publish_now(item.id)
    assert unchanged.status == QueueStatus.CANCELLED.value

    retried = await queue_manager.retry_item(item.id)  # bring it back to WAITING first
    now_result = await queue_manager.publish_now(retried.id)
    assert now_result.scheduled_at <= datetime.now(timezone.utc)


async def test_has_already_published(isolated_db):
    job = _job("job9", {"c1": (_clip("c1", rank=1), ClipStatus.COMPLETED)})
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    item = (await queue_manager.list_queue("job9"))[0]

    assert await queue_manager.has_already_published(item.idempotency_key) is False

    from app.db.base import session_scope

    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item.id)
        db_item.status = QueueStatus.PUBLISHED.value

    assert await queue_manager.has_already_published(item.idempotency_key) is True


async def test_restart_safety_published_item_survives_a_fresh_query(isolated_db):
    """Section 21: nothing is ever recreated from scratch -- a PUBLISHED row stays
    PUBLISHED across whatever stands in for 'the app restarted' in this test (just
    re-querying with fresh function calls, since the DB itself is the persistence)."""
    job = _job("job10", {
        "c1": (_clip("c1", rank=1), ClipStatus.COMPLETED),
        "c2": (_clip("c2", rank=2), ClipStatus.COMPLETED),
    })
    await queue_manager.create_queue_from_job(job, enabled_platforms=["youtube"])
    items = await queue_manager.list_queue("job10")
    c1_id = next(i for i in items if i.clip_id == "c1").id

    from app.db.base import session_scope

    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, c1_id)
        db_item.status = QueueStatus.PUBLISHED.value
        db_item.external_post_id = "already-posted"

    # "Restart": nothing calls create_queue_from_job again; the worker would just
    # resume finding due items normally.
    items_again = await queue_manager.list_queue("job10")
    c1_again = next(i for i in items_again if i.clip_id == "c1")
    c2_again = next(i for i in items_again if i.clip_id == "c2")
    assert c1_again.status == QueueStatus.PUBLISHED.value
    assert c1_again.external_post_id == "already-posted"
    assert c2_again.status == QueueStatus.WAITING.value
