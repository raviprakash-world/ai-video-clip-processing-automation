"""
Persistent models for the auto-publishing queue (section 32).

Deliberately no separate `published_posts` table: a PublishQueueItem row with
status=PUBLISHED already carries external_post_id/published_at, which is
exactly what such a table would duplicate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(20), index=True)  # youtube | instagram | facebook
    account_id: Mapped[str] = mapped_column(String(128))  # external channel/page/business-account id
    account_name: Mapped[str] = mapped_column(String(256), default="")
    access_token_encrypted: Mapped[str] = mapped_column(String)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="CONNECTED")  # CONNECTED | EXPIRED | REVOKED
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"page_id": ..., "ig_business_account_id": ...}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, onupdate=_utcnow)


class PublishQueueItem(Base):
    __tablename__ = "publishing_queue"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_publishing_queue_idempotency_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    clip_id: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(20), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), index=True)

    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="WAITING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    external_post_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    output_file_path: Mapped[str] = mapped_column(String)
    # Snapshot of title/caption/hashtags at queue-creation time, so this item's
    # content doesn't silently change if the underlying job/clip is touched later.
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, onupdate=_utcnow)

    attempts: Mapped[list["PublishingAttempt"]] = relationship(back_populates="queue_item", cascade="all, delete-orphan")


class PublishingAttempt(Base):
    __tablename__ = "publishing_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    queue_item_id: Mapped[str] = mapped_column(ForeignKey("publishing_queue.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # SUCCESS | FAILED
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

    queue_item: Mapped[PublishQueueItem] = relationship(back_populates="attempts")


class OAuthState(Base):
    """Short-lived CSRF state tokens for the OAuth authorization-code flow."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
