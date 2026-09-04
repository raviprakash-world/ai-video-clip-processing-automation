"""
Capability / cost guard (section 3, 29).

Before any provider is allowed to publish, this reports whether it's even
possible using ONLY official, free-tier-eligible APIs -- and distinguishes
"no direct per-post fee" from "fully free and unlimited" (section 29):
YouTube/Meta publishing APIs don't charge per call, but they do have quota,
review requirements, and account-eligibility prerequisites that are real
constraints, not something this app can shortcut around.

This never makes a network call -- it reasons from what's already stored
locally (a connected SocialAccount's status/extra) plus static config
(PUBLIC_BASE_URL, whether OAuth client credentials are configured at all).
Runtime failures (quota exhausted, token revoked) surface later as queue
item states (QUOTA_WAIT, AUTH_REQUIRED); this report is about whether it's
even worth trying.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.db.models import SocialAccount


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    CHECK_REQUIRED = "CHECK_REQUIRED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass
class CapabilityReport:
    platform: str
    official_api: bool
    requires_paid_service: bool  # always False for all three -- see module docstring
    status: CapabilityStatus
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "official_api": self.official_api,
            "requires_paid_service": self.requires_paid_service,
            "status": self.status.value,
            "notes": self.notes,
        }


def youtube_capability(account: SocialAccount | None) -> CapabilityReport:
    notes: list[str] = []
    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        return CapabilityReport(
            "youtube", True, False, CapabilityStatus.NOT_AVAILABLE,
            ["GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not configured on the server."],
        )
    if not account or account.status != "CONNECTED":
        return CapabilityReport("youtube", True, False, CapabilityStatus.NOT_AVAILABLE, ["No connected YouTube channel."])
    notes.append("Subject to YouTube Data API v3 daily quota and channel upload limits.")
    return CapabilityReport("youtube", True, False, CapabilityStatus.AVAILABLE, notes)


def facebook_capability(account: SocialAccount | None) -> CapabilityReport:
    if not settings.META_APP_ID or not settings.META_APP_SECRET:
        return CapabilityReport(
            "facebook", True, False, CapabilityStatus.NOT_AVAILABLE,
            ["META_APP_ID / META_APP_SECRET are not configured on the server."],
        )
    if not account or account.status != "CONNECTED":
        return CapabilityReport("facebook", True, False, CapabilityStatus.NOT_AVAILABLE, ["No connected Facebook Page."])
    if not account.extra.get("page_id"):
        return CapabilityReport(
            "facebook", True, False, CapabilityStatus.CHECK_REQUIRED,
            ["Connected, but no Facebook Page with video-publish permission was found on this account."],
        )
    return CapabilityReport(
        "facebook", True, False, CapabilityStatus.AVAILABLE,
        ["Subject to Meta Graph API rate limits and Page-level publishing permissions."],
    )


def instagram_capability(account: SocialAccount | None) -> CapabilityReport:
    if not settings.META_APP_ID or not settings.META_APP_SECRET:
        return CapabilityReport(
            "instagram", True, False, CapabilityStatus.NOT_AVAILABLE,
            ["META_APP_ID / META_APP_SECRET are not configured on the server."],
        )
    if not account or account.status != "CONNECTED":
        return CapabilityReport("instagram", True, False, CapabilityStatus.NOT_AVAILABLE, ["No connected Instagram account."])
    if not account.extra.get("ig_business_account_id"):
        return CapabilityReport(
            "instagram", True, False, CapabilityStatus.CHECK_REQUIRED,
            [
                "Connected Facebook account has no linked Instagram professional "
                "(Business/Creator) account -- required by Meta's Content Publishing API."
            ],
        )
    if not settings.PUBLIC_BASE_URL:
        return CapabilityReport(
            "instagram", True, False, CapabilityStatus.NOT_AVAILABLE,
            [
                "Instagram's Content Publishing API requires a publicly-fetchable video URL "
                "(Meta's servers pull the file); PUBLIC_BASE_URL is not configured on this server."
            ],
        )
    return CapabilityReport(
        "instagram", True, False, CapabilityStatus.AVAILABLE,
        ["Subject to Meta Graph API rate limits and Instagram Content Publishing quotas."],
    )


_CHECKERS = {"youtube": youtube_capability, "facebook": facebook_capability, "instagram": instagram_capability}


def capability_report(platform: str, account: SocialAccount | None) -> CapabilityReport:
    checker = _CHECKERS.get(platform)
    if not checker:
        return CapabilityReport(platform, False, False, CapabilityStatus.NOT_AVAILABLE, [f"Unknown platform '{platform}'."])
    return checker(account)


def full_capability_report(accounts_by_platform: dict[str, SocialAccount | None]) -> list[dict]:
    return [capability_report(p, accounts_by_platform.get(p)).to_dict() for p in ("youtube", "instagram", "facebook")]
