"""
YouTube publishing via the official YouTube Data API v3 (section 12).

Uses google-api-python-client's resumable upload support. All calls are
blocking (the official client isn't async), so they run via asyncio.to_thread
-- same pattern already used for yt-dlp elsewhere in this app.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from app.config import settings
from app.db.base import session_scope
from app.db.models import SocialAccount
from app.publishing.metadata import PublishMetadata
from app.publishing.provider import PublishingProvider, PublishResult
from app.publishing.states import ErrorClass, PublishError
from app.publishing.token_crypto import decrypt_token, encrypt_token

logger = logging.getLogger("clip_pipeline.publishing")

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
_TITLE_MAX_LEN = 100
_DESCRIPTION_MAX_LEN = 5000


def _build_credentials(account: SocialAccount) -> Credentials | None:
    access_token = decrypt_token(account.access_token_encrypted)
    refresh_token = decrypt_token(account.refresh_token_encrypted) if account.refresh_token_encrypted else None
    if access_token is None:
        return None
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=YOUTUBE_SCOPES,
    )


class YouTubePublishingProvider(PublishingProvider):
    platform = "youtube"

    async def refresh_token_if_needed(self, account: SocialAccount) -> SocialAccount:
        creds = _build_credentials(account)
        if creds is None:
            account.status = "REVOKED"
            return account
        if not creds.expired or not creds.refresh_token:
            return account

        def _refresh() -> None:
            creds.refresh(GoogleAuthRequest())

        try:
            await asyncio.to_thread(_refresh)
        except RefreshError:
            account.status = "EXPIRED"
            return account

        async with session_scope() as session:
            db_account = await session.get(SocialAccount, account.id)
            if db_account:
                db_account.access_token_encrypted = encrypt_token(creds.token)
                if creds.expiry:
                    db_account.token_expires_at = creds.expiry.replace(tzinfo=timezone.utc)
                db_account.status = "CONNECTED"
                account = db_account
        return account

    async def publish(
        self, account: SocialAccount, metadata: PublishMetadata, video_path: Path, *, public_url: str | None = None
    ) -> PublishResult:
        title = (metadata.title or "Untitled clip").strip()[:_TITLE_MAX_LEN]
        if not title:
            raise PublishError("Clip has no usable title.", error_class=ErrorClass.PERMANENT)
        description = metadata.combined_caption()[:_DESCRIPTION_MAX_LEN]

        account = await self.refresh_token_if_needed(account)
        if account.status != "CONNECTED":
            raise PublishError("YouTube account requires reauthorization.", error_class=ErrorClass.AUTH)

        creds = _build_credentials(account)
        if creds is None:
            raise PublishError("YouTube account requires reauthorization.", error_class=ErrorClass.AUTH)

        if not video_path.exists():
            raise PublishError(f"Video file not found: {video_path.name}", error_class=ErrorClass.PERMANENT)

        body = {
            "snippet": {"title": title, "description": description, "tags": metadata.hashtags[:15]},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }

        def _upload() -> dict:
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                _status, response = request.next_chunk()
            return response

        try:
            response = await asyncio.to_thread(_upload)
        except HttpError as exc:
            raise PublishError(str(exc), error_class=_classify_http_error(exc), http_status=exc.resp.status) from exc
        except (TimeoutError, ConnectionError) as exc:
            raise PublishError(str(exc), error_class=ErrorClass.TRANSIENT) from exc

        video_id = response.get("id")
        if not video_id:
            raise PublishError("YouTube upload succeeded but returned no video id.", error_class=ErrorClass.PERMANENT)
        return PublishResult(external_post_id=video_id)


def _classify_http_error(exc: HttpError) -> ErrorClass:
    status = exc.resp.status
    reason = str(exc).lower()
    if status in (401,):
        return ErrorClass.AUTH
    if status == 403:
        if "quota" in reason or "dailylimitexceeded" in reason or "ratelimitexceeded" in reason:
            return ErrorClass.QUOTA
        if "forbidden" in reason or "permission" in reason:
            return ErrorClass.AUTH
        return ErrorClass.UNSUPPORTED
    if status == 400:
        return ErrorClass.PERMANENT
    if status >= 500:
        return ErrorClass.TRANSIENT
    return ErrorClass.PERMANENT
