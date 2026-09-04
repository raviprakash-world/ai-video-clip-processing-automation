"""
Instagram Reels publishing via the official Meta Content Publishing API
(section 13).

Three-step flow, all documented Graph API behavior:
  1. POST /{ig-business-account-id}/media (video_url, media_type=REELS, caption)
     -> creation_id
  2. Poll GET /{creation_id}?fields=status_code until FINISHED (or ERROR)
  3. POST /{ig-business-account-id}/media_publish (creation_id) -> published media id

Step 1 requires a publicly-fetchable video_url -- Meta's servers fetch the
file themselves; there is no direct-binary-upload option for Reels. If the
caller has no public_url (PUBLIC_BASE_URL not configured), this fails with
NOT_AVAILABLE via the cost guard before we ever get here -- but publish()
still guards against it defensively.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.models import SocialAccount
from app.publishing.meta_base import graph_request, refresh_meta_account_if_needed
from app.publishing.metadata import PublishMetadata
from app.publishing.provider import PublishingProvider, PublishResult
from app.publishing.states import ErrorClass, PublishError
from app.publishing.token_crypto import decrypt_token

_POLL_INTERVAL_SECONDS = 3
_MAX_POLL_ATTEMPTS = 40  # ~2 minutes


class InstagramPublishingProvider(PublishingProvider):
    platform = "instagram"

    async def refresh_token_if_needed(self, account: SocialAccount) -> SocialAccount:
        return await refresh_meta_account_if_needed(account)

    async def publish(
        self, account: SocialAccount, metadata: PublishMetadata, video_path: Path, *, public_url: str | None = None
    ) -> PublishResult:
        account = await self.refresh_token_if_needed(account)
        if account.status != "CONNECTED":
            raise PublishError("Instagram account requires reauthorization.", error_class=ErrorClass.AUTH)

        ig_account_id = account.extra.get("ig_business_account_id")
        if not ig_account_id:
            raise PublishError(
                "No Instagram professional (Business/Creator) account is linked to this Facebook Page.",
                error_class=ErrorClass.UNSUPPORTED,
            )
        if not public_url:
            raise PublishError(
                "Instagram publishing requires a publicly-fetchable video URL; PUBLIC_BASE_URL is not configured.",
                error_class=ErrorClass.UNSUPPORTED,
            )

        token = decrypt_token(account.access_token_encrypted)
        if token is None:
            raise PublishError("Instagram account requires reauthorization.", error_class=ErrorClass.AUTH)

        caption = metadata.combined_caption()

        creation = await graph_request(
            "POST",
            f"/{ig_account_id}/media",
            params={"video_url": public_url, "media_type": "REELS", "caption": caption, "access_token": token},
        )
        creation_id = creation.get("id")
        if not creation_id:
            raise PublishError("Instagram media container creation returned no id.", error_class=ErrorClass.PERMANENT)

        for _ in range(_MAX_POLL_ATTEMPTS):
            status = await graph_request(
                "GET", f"/{creation_id}", params={"fields": "status_code", "access_token": token}
            )
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code == "ERROR":
                raise PublishError("Instagram media processing failed (status_code=ERROR).", error_class=ErrorClass.PERMANENT)
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        else:
            raise PublishError(
                "Instagram media container did not finish processing in time.", error_class=ErrorClass.TRANSIENT
            )

        published = await graph_request(
            "POST", f"/{ig_account_id}/media_publish", params={"creation_id": creation_id, "access_token": token}
        )
        media_id = published.get("id")
        if not media_id:
            raise PublishError("Instagram media_publish succeeded but returned no id.", error_class=ErrorClass.PERMANENT)
        return PublishResult(external_post_id=media_id)
