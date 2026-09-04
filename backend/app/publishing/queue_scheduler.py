"""
Pure scheduling logic (section 6/7/26/38) -- no I/O, easy to test exhaustively.

Ordering: rank ASC, then viral_score DESC as the tiebreaker (section 6).
Slotting: "one clip every hour" means one scheduled_at per CLIP, shared by
every enabled platform for that clip (section 26) -- never staggered
per-platform unless a caller explicitly asks for that (which nothing in
this app does).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.schemas.validated import ValidatedClip


def order_clips(clips: list[ValidatedClip]) -> list[ValidatedClip]:
    return sorted(clips, key=lambda c: (c.rank, -c.viral_score))


def assign_slots(ordered_clips: list[ValidatedClip], *, start_at: datetime, interval_minutes: int) -> dict[str, datetime]:
    """clip_id -> scheduled_at, first clip at start_at, each subsequent one interval_minutes later."""
    return {clip.clip_id: start_at + timedelta(minutes=interval_minutes * i) for i, clip in enumerate(ordered_clips)}


def reschedule_from_now(pending_ids_in_order: list[str], *, now: datetime, interval_minutes: int) -> dict[str, datetime]:
    """Recompute schedule for still-pending items on resume (section 38): never
    schedule into the past, preserve relative order and spacing, and never touch
    anything not passed in here (already-published/failed/cancelled items)."""
    return {item_id: now + timedelta(minutes=interval_minutes * i) for i, item_id in enumerate(pending_ids_in_order)}
