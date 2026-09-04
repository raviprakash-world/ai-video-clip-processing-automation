"""Pure scheduling logic (section 6/26/38): rank/score ordering and one-slot-per-clip interval assignment."""
from datetime import datetime, timedelta, timezone

from app.publishing.queue_scheduler import assign_slots, order_clips, reschedule_from_now
from app.schemas.validated import ValidatedClip


def _clip(clip_id: str, rank: int, viral_score: int) -> ValidatedClip:
    return ValidatedClip(
        clip_id=clip_id, rank=rank, start_time="00:00:00", end_time="00:00:05",
        start_seconds=0, end_seconds=5, duration_seconds=5, ai_reported_duration_seconds=5,
        duration_mismatch=False, viral_score=viral_score, category="c", speaker="s", hook="h",
        title_options={}, caption="c", hashtags=[], reason="r", payoff="p",
        context_warning=None, copyright_warning=None,
    )


def test_order_by_rank_ascending():
    clips = [_clip("c3", rank=3, viral_score=50), _clip("c1", rank=1, viral_score=50), _clip("c2", rank=2, viral_score=50)]
    ordered = order_clips(clips)
    assert [c.clip_id for c in ordered] == ["c1", "c2", "c3"]


def test_tiebreak_by_viral_score_descending():
    clips = [_clip("low", rank=1, viral_score=10), _clip("high", rank=1, viral_score=90)]
    ordered = order_clips(clips)
    assert [c.clip_id for c in ordered] == ["high", "low"]


def test_never_random_always_deterministic():
    clips = [_clip("c2", rank=2, viral_score=50), _clip("c1", rank=1, viral_score=50)]
    result1 = [c.clip_id for c in order_clips(clips)]
    result2 = [c.clip_id for c in order_clips(list(reversed(clips)))]
    assert result1 == result2 == ["c1", "c2"]


def test_assign_slots_one_clip_per_interval():
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    clips = [_clip("c1", 1, 90), _clip("c2", 2, 80), _clip("c3", 3, 70)]
    slots = assign_slots(clips, start_at=start, interval_minutes=60)
    assert slots["c1"] == start
    assert slots["c2"] == start + timedelta(hours=1)
    assert slots["c3"] == start + timedelta(hours=2)


def test_assign_slots_respects_configurable_interval():
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    clips = [_clip("c1", 1, 90), _clip("c2", 2, 80)]
    slots = assign_slots(clips, start_at=start, interval_minutes=30)
    assert slots["c2"] - slots["c1"] == timedelta(minutes=30)


def test_reschedule_from_now_never_schedules_into_the_past():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    slots = reschedule_from_now(["a", "b", "c"], now=now, interval_minutes=60)
    assert all(t >= now for t in slots.values())
    assert slots["a"] == now
    assert slots["b"] == now + timedelta(hours=1)
    assert slots["c"] == now + timedelta(hours=2)
