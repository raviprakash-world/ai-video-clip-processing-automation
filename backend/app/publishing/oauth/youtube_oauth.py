"""
Google OAuth for YouTube Data API v3 (section 11/12/30).

Uses google_auth_oauthlib.flow.Flow, the officially-documented client
library flow, rather than hand-rolling the authorization-code exchange.
`access_type=offline` + `prompt=consent` ensure a refresh_token is issued
even on a repeat authorization.

The token exchange happens entirely server-side inside the callback
handler; the frontend only ever sees a redirect to Google's consent
screen and back -- it never touches a client_secret or a token.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy import delete

from app.config import settings
from app.db.base import session_scope
from app.db.models import OAuthState, SocialAccount
from app.publishing.token_crypto import encrypt_token
from app.publishing.youtube_provider import YOUTUBE_SCOPES

_OAUTH_STATE_TTL_MINUTES = 15


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_OAUTH_REDIRECT_URI],
        }
    }


async def build_authorization_url() -> str:
    state = secrets.token_urlsafe(24)
    async with session_scope() as session:
        session.add(OAuthState(state=state, platform="youtube"))

    flow = Flow.from_client_config(_client_config(), scopes=YOUTUBE_SCOPES, redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI)
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true", state=state)
    return auth_url


async def _consume_state(state: str, expected_platform: str) -> bool:
    async with session_scope() as session:
        record = await session.get(OAuthState, state)
        if not record or record.platform != expected_platform:
            return False
        if datetime.now(timezone.utc) - record.created_at.replace(tzinfo=timezone.utc) > timedelta(minutes=_OAUTH_STATE_TTL_MINUTES):
            await session.execute(delete(OAuthState).where(OAuthState.state == state))
            return False
        await session.execute(delete(OAuthState).where(OAuthState.state == state))
        return True


async def handle_callback(code: str, state: str) -> SocialAccount:
    if not await _consume_state(state, "youtube"):
        raise ValueError("Invalid or expired OAuth state (possible CSRF attempt or a stale/replayed callback link).")

    flow = Flow.from_client_config(_client_config(), scopes=YOUTUBE_SCOPES, redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI)
    flow.fetch_token(code=code)
    creds = flow.credentials

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    channels = youtube.channels().list(part="snippet", mine=True).execute()
    items = channels.get("items", [])
    if not items:
        raise ValueError("Google account has no YouTube channel to connect.")
    channel = items[0]
    channel_id = channel["id"]
    channel_title = channel.get("snippet", {}).get("title", "")

    async with session_scope() as session:
        existing = await session.get(SocialAccount, channel_id)
        account = existing or SocialAccount(id=channel_id, platform="youtube", account_id=channel_id)
        account.platform = "youtube"
        account.account_id = channel_id
        account.account_name = channel_title
        account.access_token_encrypted = encrypt_token(creds.token)
        account.refresh_token_encrypted = encrypt_token(creds.refresh_token) if creds.refresh_token else account.refresh_token_encrypted
        account.token_expires_at = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
        account.status = "CONNECTED"
        session.add(account)
        await session.flush()
        return account
