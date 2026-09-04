"""
Shared Meta Graph API plumbing for the Facebook and Instagram providers
(section 13/14). Both platforms authenticate through the same Meta/Facebook
Login OAuth flow and the same Page access token model, so the HTTP client,
error classification, and token-refresh logic live here once.

Uses raw httpx calls against Meta's documented REST endpoints -- this is
"official API usage" (we're calling Meta's own published Graph API contract
correctly), just without the heavyweight facebook-business SDK, which is
overkill for the handful of endpoints this app needs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.db.base import session_scope
from app.db.models import SocialAccount
from app.publishing.states import ErrorClass, PublishError
from app.publishing.token_crypto import decrypt_token, encrypt_token

logger = logging.getLogger("clip_pipeline.publishing")

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Meta error codes worth distinguishing (see Meta's Graph API error reference).
_AUTH_CODES = {190}  # OAuthException: invalid/expired/revoked token
_QUOTA_CODES = {4, 17, 32, 613}  # application/user rate limiting


def classify_graph_error(payload: dict, http_status: int) -> ErrorClass:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    code = error.get("code")
    if code in _AUTH_CODES:
        return ErrorClass.AUTH
    if code in _QUOTA_CODES:
        return ErrorClass.QUOTA
    if http_status >= 500:
        return ErrorClass.TRANSIENT
    if http_status == 403:
        return ErrorClass.UNSUPPORTED
    return ErrorClass.PERMANENT


async def graph_request(method: str, path: str, *, params: dict | None = None, files: dict | None = None) -> dict:
    url = f"{GRAPH_API_BASE}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
            response = await client.request(method, url, params=params, files=files)
    except httpx.TimeoutException as exc:
        raise PublishError(f"Meta Graph API timed out: {exc}", error_class=ErrorClass.TRANSIENT) from exc
    except httpx.HTTPError as exc:
        raise PublishError(f"Meta Graph API network error: {exc}", error_class=ErrorClass.TRANSIENT) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        message = payload.get("error", {}).get("message", response.text[:500])
        raise PublishError(
            f"Meta Graph API error: {message}",
            error_class=classify_graph_error(payload, response.status_code),
            http_status=response.status_code,
        )
    return payload


async def refresh_meta_account_if_needed(account: SocialAccount) -> SocialAccount:
    """Exchange the current long-lived token for a fresh one if it's within 7 days
    of expiring. Meta has no separate refresh_token -- a still-valid long-lived
    token can extend itself; an already-expired one cannot, and requires the
    user to reauthorize."""
    if not account.token_expires_at:
        return account
    now = datetime.now(timezone.utc)
    if account.token_expires_at - now > timedelta(days=7):
        return account

    current_token = decrypt_token(account.access_token_encrypted)
    if current_token is None or account.token_expires_at <= now:
        account.status = "EXPIRED"
        return account

    try:
        payload = await graph_request(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "fb_exchange_token": current_token,
            },
        )
    except PublishError:
        account.status = "EXPIRED"
        return account

    new_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not new_token:
        account.status = "EXPIRED"
        return account

    async with session_scope() as session:
        db_account = await session.get(SocialAccount, account.id)
        if db_account:
            db_account.access_token_encrypted = encrypt_token(new_token)
            if expires_in:
                db_account.token_expires_at = now + timedelta(seconds=int(expires_in))
            db_account.status = "CONNECTED"
            account = db_account
    return account
