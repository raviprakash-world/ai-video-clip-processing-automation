"""
Meta OAuth via Facebook Login for Business, covering both Facebook Page
publishing and Instagram Content Publishing (section 11/13/14/30).

Meta retired the plain scope-based consent dialog for business permissions
(pages_manage_posts, instagram_content_publish, etc.) in favor of Login
Configurations: you create a named configuration in the App Dashboard
(Facebook Login for Business -> Configurations -> + Create configuration,
"User access token" type, with the assets/permissions this app needs) and
get back a config_id, which replaces `scope` in the authorization URL. See
README.md for the exact current dashboard steps -- Meta's app-creation flow
changes fairly often and this is the part most likely to drift.

One consent flow covers both platforms: Instagram Business/Creator accounts
are only reachable through the Graph API via their linked Facebook Page's
access token, so "Connect Meta" discovers the user's Page(s), picks one, and
checks whether it has a linked Instagram professional account. This
connects Facebook always, and Instagram only when eligible -- exactly the
CHECK_REQUIRED/AVAILABLE distinction the cost guard reports.

Meta has no separate long-lived refresh_token the way Google does: a
still-valid long-lived (~60 day) token can re-exchange itself for a fresh
one (see meta_base.refresh_meta_account_if_needed); an already-expired one
cannot, and requires the user to go through this flow again.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import delete

from app.config import settings
from app.db.base import session_scope
from app.db.models import OAuthState, SocialAccount
from app.publishing.meta_base import GRAPH_API_BASE, graph_request
from app.publishing.states import PublishError
from app.publishing.token_crypto import encrypt_token

_OAUTH_STATE_TTL_MINUTES = 15
# Fallback only -- Meta recommends against scope= once a Login Configuration
# exists, but an app that genuinely has no config_id yet (e.g. mid-setup)
# still gets a URL that Meta will render an explicit error page for, rather
# than this code guessing or silently degrading permissions.
_LEGACY_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish,business_management"


async def build_authorization_url() -> str:
    state = secrets.token_urlsafe(24)
    async with session_scope() as session:
        session.add(OAuthState(state=state, platform="meta"))

    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.META_OAUTH_REDIRECT_URI,
        "state": state,
        "response_type": "code",
        # Without this, Facebook silently reuses whatever was approved on a
        # user's FIRST authorization and never re-prompts for permissions
        # added to the configuration afterwards -- a real failure mode this
        # surfaced (Instagram lookup failing for missing pages_read_engagement
        # after it was added to the config post-first-connect). Forcing
        # rerequest makes every "Connect" click ask for the full current set.
        "auth_type": "rerequest",
    }
    if settings.META_LOGIN_CONFIG_ID:
        params["config_id"] = settings.META_LOGIN_CONFIG_ID
    else:
        params["scope"] = _LEGACY_SCOPES
    return f"{GRAPH_API_BASE.replace('graph.facebook.com', 'www.facebook.com')}/dialog/oauth?{urlencode(params)}"


async def _consume_state(state: str) -> bool:
    async with session_scope() as session:
        record = await session.get(OAuthState, state)
        if not record or record.platform != "meta":
            return False
        if datetime.now(timezone.utc) - record.created_at.replace(tzinfo=timezone.utc) > timedelta(minutes=_OAUTH_STATE_TTL_MINUTES):
            await session.execute(delete(OAuthState).where(OAuthState.state == state))
            return False
        await session.execute(delete(OAuthState).where(OAuthState.state == state))
        return True


async def handle_callback(code: str, state: str) -> tuple[list[SocialAccount], list[str]]:
    """Returns (connected_accounts, warnings). Facebook connecting successfully
    is never blocked by an Instagram-specific problem -- that shows up as a
    warning string instead of an exception, since Instagram is optional."""
    if not await _consume_state(state):
        raise ValueError("Invalid or expired OAuth state (possible CSRF attempt or a stale/replayed callback link).")

    token_response = await graph_request(
        "GET",
        "/oauth/access_token",
        params={
            "client_id": settings.META_APP_ID,
            "redirect_uri": settings.META_OAUTH_REDIRECT_URI,
            "client_secret": settings.META_APP_SECRET,
            "code": code,
        },
    )
    short_lived_token = token_response.get("access_token")
    if not short_lived_token:
        raise ValueError("Meta did not return an access token for this authorization code.")

    long_lived = await graph_request(
        "GET",
        "/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": short_lived_token,
        },
    )
    user_token = long_lived.get("access_token", short_lived_token)
    expires_in = long_lived.get("expires_in")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None

    pages_response = await graph_request("GET", "/me/accounts", params={"access_token": user_token})
    pages = pages_response.get("data", [])
    if not pages:
        raise ValueError("This Facebook account has no Pages you manage -- a Page is required for publishing.")

    page = pages[0]  # Section scope: connect the first available Page.
    page_id = page["id"]
    page_name = page.get("name", "")
    page_token = page.get("access_token", user_token)

    permissions_response = await graph_request("GET", "/me/permissions", params={"access_token": user_token})
    granted_permissions = {
        p["permission"] for p in permissions_response.get("data", []) if p.get("status") == "granted"
    }

    accounts: list[SocialAccount] = []
    warnings: list[str] = []
    async with session_scope() as session:
        fb_id = f"facebook:{page_id}"
        fb_account = await session.get(SocialAccount, fb_id) or SocialAccount(id=fb_id, platform="facebook", account_id=page_id)
        fb_account.platform = "facebook"
        fb_account.account_id = page_id
        fb_account.account_name = page_name
        fb_account.access_token_encrypted = encrypt_token(page_token)
        fb_account.token_expires_at = expires_at
        fb_account.status = "CONNECTED"
        fb_account.extra = {"page_id": page_id}
        session.add(fb_account)
        accounts.append(fb_account)

    # Instagram linkage is a bonus on top of Facebook, not a hard requirement --
    # a permission problem here must not make the whole connect attempt look
    # like it failed when Facebook itself (already saved above) is fine. If it
    # can't be checked, say exactly why (including which permissions actually
    # came back granted) instead of surfacing Meta's generic (#100) error.
    if "pages_read_engagement" not in granted_permissions and "instagram_basic" not in granted_permissions:
        warnings.append(
            "Instagram not linked: Meta did not grant 'pages_read_engagement' or 'instagram_basic' this time "
            f"(granted permissions: {', '.join(sorted(granted_permissions)) or 'none'}). "
            "In the Facebook Login for Business configuration, confirm both permissions are checked and saved, "
            "then remove the app at facebook.com -> Settings -> Apps and Websites and reconnect for a fully fresh consent."
        )
    else:
        try:
            ig_info = await graph_request(
                "GET", f"/{page_id}", params={"fields": "instagram_business_account", "access_token": page_token}
            )
        except PublishError as exc:
            warnings.append(f"Instagram not linked: {exc.message}")
            ig_info = {}

        ig_business = ig_info.get("instagram_business_account")
        if ig_business and ig_business.get("id"):
            ig_id = ig_business["id"]
            ig_username_info = await graph_request(
                "GET", f"/{ig_id}", params={"fields": "username", "access_token": page_token}
            )
            async with session_scope() as session:
                ig_db_id = f"instagram:{ig_id}"
                ig_account = await session.get(SocialAccount, ig_db_id) or SocialAccount(
                    id=ig_db_id, platform="instagram", account_id=ig_id
                )
                ig_account.platform = "instagram"
                ig_account.account_id = ig_id
                ig_account.account_name = ig_username_info.get("username", "")
                ig_account.access_token_encrypted = encrypt_token(page_token)
                ig_account.token_expires_at = expires_at
                ig_account.status = "CONNECTED"
                ig_account.extra = {"page_id": page_id, "ig_business_account_id": ig_id}
                session.add(ig_account)
                accounts.append(ig_account)
        elif not warnings:
            warnings.append(
                "Instagram not linked: this Facebook Page has no linked Instagram professional "
                "(Business/Creator) account. Link one in Meta Business Suite -> your Page -> Linked Accounts."
            )

    return accounts, warnings
