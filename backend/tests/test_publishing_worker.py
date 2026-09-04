"""
PublishingWorker (sections 18/19/20/34/35): retry classification, one-clip
and one-platform failure isolation, idempotency skip, pre-publish
validation, and atomic claiming. Providers are faked (no real network) --
the providers' own HTTP/API correctness is exercised by their own modules
and by the cost-guard/metadata tests; this file is about the worker's
decision logic given a provider's outcome.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.base import session_scope
from app.db.models import PublishQueueItem, SocialAccount
from app.publishing import worker
from app.publishing.provider import PublishResult
from app.publishing.states import ErrorClass, PublishError, QueueStatus

pytestmark = pytest.mark.asyncio


class FakeProvider:
    """Records calls and returns/raises whatever the test configures."""

    def __init__(self, platform: str, outcome):
        self.platform = platform
        self.outcome = outcome  # PublishResult, PublishError, or an Exception instance
        self.calls = 0

    async def publish(self, account, metadata, video_path, *, public_url=None):
        self.calls += 1
        if isinstance(self.outcome, PublishError):
            raise self.outcome
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def refresh_token_if_needed(self, account):
        return account


async def _make_item(*, platform="youtube", clip_id="c1", video_path, scheduled_at=None, status=QueueStatus.WAITING) -> str:
    async with session_scope() as session:
        item = PublishQueueItem(
            job_id="job1",
            clip_id=clip_id,
            platform=platform,
            idempotency_key=f"job1:{clip_id}:{platform}",
            scheduled_at=scheduled_at or (datetime.now(timezone.utc) - timedelta(seconds=1)),
            status=status.value,
            output_file_path=str(video_path),
            metadata_json={"title": "T", "caption": "C", "hashtags": ["#h"]},
        )
        session.add(item)
        await session.flush()
        return item.id


async def _make_account(platform="youtube") -> None:
    async with session_scope() as session:
        session.add(
            SocialAccount(
                id=f"{platform}:acct", platform=platform, account_id="acct", account_name="Test",
                access_token_encrypted="enc", status="CONNECTED", extra={"page_id": "p1", "ig_business_account_id": "ig1"},
            )
        )


async def _get(item_id: str) -> PublishQueueItem:
    async with session_scope() as session:
        return await session.get(PublishQueueItem, item_id)


async def test_successful_publish_marks_published(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    fake = FakeProvider("youtube", PublishResult(external_post_id="yt-123"))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    processed = await worker.run_worker_cycle()

    assert processed == 1
    item = await _get(item_id)
    assert item.status == QueueStatus.PUBLISHED.value
    assert item.external_post_id == "yt-123"
    assert fake.calls == 1


async def test_transient_failure_schedules_retry(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    fake = FakeProvider("youtube", PublishError("network blip", error_class=ErrorClass.TRANSIENT))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.RETRYING.value
    assert item.attempt_count == 1
    assert item.scheduled_at > datetime.now(timezone.utc)  # not immediate -- 5 min backoff


async def test_transient_failure_becomes_failed_after_max_attempts(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    fake = FakeProvider("youtube", PublishError("still broken", error_class=ErrorClass.TRANSIENT))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    for _ in range(settings.MAX_PUBLISH_ATTEMPTS):
        async with session_scope() as session:
            db_item = await session.get(PublishQueueItem, item_id)
            db_item.status = QueueStatus.WAITING.value
            db_item.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.FAILED.value
    assert item.attempt_count == settings.MAX_PUBLISH_ATTEMPTS


async def test_auth_error_sets_auth_required_without_counting_as_retryable(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    fake = FakeProvider("youtube", PublishError("invalid_grant", error_class=ErrorClass.AUTH))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.AUTH_REQUIRED.value


async def test_quota_error_waits_and_does_not_hammer_the_api(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    fake = FakeProvider("youtube", PublishError("quota exceeded", error_class=ErrorClass.QUOTA))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.QUOTA_WAIT.value
    assert item.scheduled_at > datetime.now(timezone.utc) + timedelta(minutes=settings.DEFAULT_QUOTA_WAIT_MINUTES - 1)


async def test_unsupported_error_is_terminal_not_retried(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    fake = FakeProvider("youtube", PublishError("account not eligible", error_class=ErrorClass.UNSUPPORTED))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.NOT_SUPPORTED.value


async def test_published_item_is_never_reclaimed_by_the_worker(isolated_db, sample_video_file, monkeypatch):
    """The DB's unique idempotency_key constraint means there is exactly one row
    per (job, clip, platform); once it's PUBLISHED, the worker's due-item query
    (which only matches WAITING/RETRYING/QUOTA_WAIT) can never claim it again --
    the real protection against a double-post lives in retry_item() refusing to
    revert a PUBLISHED item back to WAITING (see test_publishing_queue_manager.py)."""
    await _make_account("youtube")
    item_id = await _make_item(video_path=sample_video_file)
    async with session_scope() as session:
        db_item = await session.get(PublishQueueItem, item_id)
        db_item.status = QueueStatus.PUBLISHED.value
        db_item.external_post_id = "yt-already"

    fake = FakeProvider("youtube", PublishResult(external_post_id="should-not-happen"))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    processed = await worker.run_worker_cycle()

    assert processed == 0
    assert fake.calls == 0
    item = await _get(item_id)
    assert item.external_post_id == "yt-already"


async def test_missing_account_sets_auth_required_without_calling_provider(isolated_db, sample_video_file, monkeypatch):
    item_id = await _make_item(video_path=sample_video_file)  # no SocialAccount created at all
    fake = FakeProvider("youtube", PublishResult(external_post_id="x"))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.AUTH_REQUIRED.value
    assert fake.calls == 0


async def test_missing_output_file_fails_without_calling_provider(isolated_db, tmp_path, monkeypatch):
    await _make_account("youtube")
    item_id = await _make_item(video_path=tmp_path / "does_not_exist.mp4")
    fake = FakeProvider("youtube", PublishResult(external_post_id="x"))
    monkeypatch.setattr(worker, "get_provider", lambda platform: fake)

    await worker.run_worker_cycle()

    item = await _get(item_id)
    assert item.status == QueueStatus.FAILED.value
    assert "does_not_exist" in item.last_error
    assert fake.calls == 0


async def test_one_clip_failure_does_not_block_other_clips(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    good_id = await _make_item(video_path=sample_video_file, clip_id="good")
    bad_id = await _make_item(video_path=sample_video_file, clip_id="bad")

    # The provider interface only receives (account, metadata, video_path), not
    # the clip_id, so each item is run through the worker's internals directly
    # with its own provider swapped in -- still exercising the real _process_item
    # / _publish_one / _handle_failure path, just without needing the provider
    # itself to disambiguate clips it was never told about.
    providers = {
        "good": FakeProvider("youtube", PublishResult(external_post_id="ok")),
        "bad": FakeProvider("youtube", PublishError("boom", error_class=ErrorClass.PERMANENT)),
    }
    for clip_id, item_id in (("good", good_id), ("bad", bad_id)):
        monkeypatch.setattr(worker, "get_provider", lambda platform, c=clip_id: providers[c])
        item = await _get(item_id)
        await worker._process_item(item)

    good_after = await _get(good_id)
    bad_after = await _get(bad_id)
    assert good_after.status == QueueStatus.PUBLISHED.value
    assert bad_after.status == QueueStatus.FAILED.value


async def test_one_platform_failure_does_not_affect_other_platforms_for_same_clip(isolated_db, sample_video_file, monkeypatch):
    await _make_account("youtube")
    await _make_account("facebook")
    yt_id = await _make_item(video_path=sample_video_file, clip_id="c1", platform="youtube")
    fb_id = await _make_item(video_path=sample_video_file, clip_id="c1", platform="facebook")

    providers = {"youtube": FakeProvider("youtube", PublishError("down", error_class=ErrorClass.TRANSIENT)),
                 "facebook": FakeProvider("facebook", PublishResult(external_post_id="fb-1"))}
    monkeypatch.setattr(worker, "get_provider", lambda platform: providers[platform])

    await worker.run_worker_cycle()

    yt_after = await _get(yt_id)
    fb_after = await _get(fb_id)
    assert yt_after.status == QueueStatus.RETRYING.value
    assert fb_after.status == QueueStatus.PUBLISHED.value


async def test_claim_is_atomic_second_claim_finds_nothing(isolated_db, sample_video_file):
    await _make_item(video_path=sample_video_file)
    first = await worker._claim_due_item()
    second = await worker._claim_due_item()
    assert first is not None
    assert second is None
