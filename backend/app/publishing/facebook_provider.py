"""
Facebook Page video publishing via the official Meta Graph API (section 14).

Unlike Instagram's Content Publishing API, Facebook's /{page-id}/videos
endpoint accepts a direct binary upload -- no public URL required, so this
provider needs no PUBLIC_BASE_URL configuration to work.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from app.db.models import SocialAccount
from app.publishing.meta_base import GRAPH_API_BASE, classify_graph_error, refresh_meta_account_if_needed
from app.publishing.metadata import PublishMetadata
from app.publishing.provider import PublishingProvider, PublishResult
from app.publishing.states import ErrorClass, PublishError
from app.publishing.token_crypto import decrypt_token


class FacebookPublishingProvider(PublishingProvider):
    platform = "facebook"

    async def refresh_token_if_needed(self, account: SocialAccount) -> SocialAccount:
        return await refresh_meta_account_if_needed(account)

    async def publish(
        self, account: SocialAccount, metadata: PublishMetadata, video_path: Path, *, public_url: str | None = None
    ) -> PublishResult:
        account = await self.refresh_token_if_needed(account)
        if account.status != "CONNECTED":
            raise PublishError("Facebook account requires reauthorization.", error_class=ErrorClass.AUTH)

        page_id = account.extra.get("page_id")
        if not page_id:
            raise PublishError(
                "No Facebook Page with video-publish permission is linked to this account.",
                error_class=ErrorClass.UNSUPPORTED,
            )

        page_token = decrypt_token(account.access_token_encrypted)
        if page_token is None:
            raise PublishError("Facebook account requires reauthorization.", error_class=ErrorClass.AUTH)

        if not video_path.exists():
            raise PublishError(f"Video file not found: {video_path.name}", error_class=ErrorClass.PERMANENT)

        description = metadata.combined_caption()
        url = f"{GRAPH_API_BASE}/{page_id}/videos"

        try:
            with video_path.open("rb") as f:
                files = {"source": (video_path.name, f, "video/mp4")}
                data = {"description": description, "access_token": page_token}
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
                    response = await client.post(url, data=data, files=files)
        except httpx.TimeoutException as exc:
            raise PublishError(f"Facebook upload timed out: {exc}", error_class=ErrorClass.TRANSIENT) from exc
        except httpx.HTTPError as exc:
            raise PublishError(f"Facebook upload network error: {exc}", error_class=ErrorClass.TRANSIENT) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            message = payload.get("error", {}).get("message", response.text[:500])
            raise PublishError(
                f"Facebook publish failed: {message}",
                error_class=classify_graph_error(payload, response.status_code),
                http_status=response.status_code,
            )

        video_id = payload.get("id")
        if not video_id:
            raise PublishError("Facebook upload succeeded but returned no video id.", error_class=ErrorClass.PERMANENT)
        return PublishResult(external_post_id=video_id)
