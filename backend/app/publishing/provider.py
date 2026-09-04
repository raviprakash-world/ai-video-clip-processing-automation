"""
PublishingProvider abstraction (section 9).

The queue/worker never contains platform-specific logic (section 9) -- it
only calls `provider.publish(account, metadata, video_path)` and interprets
the PublishError it might raise via `error_class`. Adding a fourth platform
means adding one more subclass here; nothing in queue_manager or worker.py
changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from app.db.models import SocialAccount
from app.publishing.metadata import PublishMetadata


class PublishResult:
    def __init__(self, external_post_id: str):
        self.external_post_id = external_post_id


class PublishingProvider(ABC):
    platform: str

    @abstractmethod
    async def publish(
        self,
        account: SocialAccount,
        metadata: PublishMetadata,
        video_path: Path,
        *,
        public_url: Optional[str] = None,
    ) -> PublishResult:
        """Upload/publish video_path with metadata using account's credentials.

        `public_url`, when given, is a publicly-fetchable HTTPS URL for the
        same file (only Instagram's Content Publishing API needs this --
        Meta's servers fetch the video themselves rather than accepting a
        direct upload for Reels).

        Must raise app.publishing.states.PublishError (never a bare Exception)
        on failure, with an accurate error_class so the worker can decide
        retry vs. terminal state correctly. Must never attempt to work around
        an API-reported restriction (section 4/12/13/14).
        """
        raise NotImplementedError

    @abstractmethod
    async def refresh_token_if_needed(self, account: SocialAccount) -> SocialAccount:
        """Refresh and persist a new access token if the current one is expired/
        about to expire. Returns the (possibly updated) account. Never raises for
        an expected "needs reauthorization" case -- instead the account's status
        should be left/set such that a subsequent capability check reports it."""
        raise NotImplementedError
