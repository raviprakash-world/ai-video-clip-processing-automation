"""Publishing-queue states (section 17) and error classification (section 19)."""
from __future__ import annotations

from enum import Enum


class QueueStatus(str, Enum):
    WAITING = "WAITING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    QUOTA_WAIT = "QUOTA_WAIT"

    @classmethod
    def due_statuses(cls) -> tuple["QueueStatus", ...]:
        """Statuses the worker will consider claiming once scheduled_at has arrived."""
        return (cls.WAITING, cls.RETRYING, cls.QUOTA_WAIT)

    @classmethod
    def terminal_statuses(cls) -> tuple["QueueStatus", ...]:
        return (cls.PUBLISHED, cls.FAILED, cls.CANCELLED, cls.NOT_SUPPORTED)


class ErrorClass(str, Enum):
    """How a publish failure should be handled -- never inferred from string-matching
    alone where a provider can tell us directly (e.g. an HTTP status code)."""

    TRANSIENT = "TRANSIENT"  # retry with backoff: network blip, 5xx, timeout
    AUTH = "AUTH"  # invalid/expired/revoked OAuth -> AUTH_REQUIRED, needs reauthorization
    QUOTA = "QUOTA"  # rate limit / quota exhausted -> QUOTA_WAIT, retry after a cooldown
    UNSUPPORTED = "UNSUPPORTED"  # account/media/policy doesn't support this -> NOT_SUPPORTED, no retry
    PERMANENT = "PERMANENT"  # invalid metadata, permission denied, etc. -> FAILED, no retry


class PublishError(Exception):
    """Raised by a PublishingProvider.publish() call. Carries enough for the worker
    to decide retry vs. terminal state without string-sniffing the message."""

    def __init__(self, message: str, *, error_class: ErrorClass, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.error_class = error_class
        self.http_status = http_status
