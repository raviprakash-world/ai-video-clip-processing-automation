"""
Cost guard (section 3/29): never report a platform AVAILABLE unless it's
reachable through a free, official API path -- no paid/unsupported
integration may ever be silently enabled.
"""
from app.config import settings
from app.db.models import SocialAccount
from app.publishing.cost_guard import (
    CapabilityStatus,
    facebook_capability,
    instagram_capability,
    youtube_capability,
)


def _account(platform: str, **extra_kwargs) -> SocialAccount:
    return SocialAccount(
        id="x", platform=platform, account_id="acct", status="CONNECTED",
        access_token_encrypted="enc", extra=extra_kwargs,
    )


def test_youtube_not_available_without_oauth_client(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    report = youtube_capability(None)
    assert report.status == CapabilityStatus.NOT_AVAILABLE
    assert report.requires_paid_service is False
    assert report.official_api is True


def test_youtube_not_available_without_connected_account(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    assert youtube_capability(None).status == CapabilityStatus.NOT_AVAILABLE


def test_youtube_available_with_connected_account(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    report = youtube_capability(_account("youtube"))
    assert report.status == CapabilityStatus.AVAILABLE
    assert report.requires_paid_service is False


def test_facebook_check_required_without_page(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "id")
    monkeypatch.setattr(settings, "META_APP_SECRET", "secret")
    report = facebook_capability(_account("facebook"))
    assert report.status == CapabilityStatus.CHECK_REQUIRED


def test_facebook_available_with_page(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "id")
    monkeypatch.setattr(settings, "META_APP_SECRET", "secret")
    report = facebook_capability(_account("facebook", page_id="123"))
    assert report.status == CapabilityStatus.AVAILABLE


def test_instagram_check_required_without_business_account(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "id")
    monkeypatch.setattr(settings, "META_APP_SECRET", "secret")
    report = instagram_capability(_account("instagram", page_id="123"))
    assert report.status == CapabilityStatus.CHECK_REQUIRED


def test_instagram_not_available_without_public_base_url(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "id")
    monkeypatch.setattr(settings, "META_APP_SECRET", "secret")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    report = instagram_capability(_account("instagram", page_id="123", ig_business_account_id="ig1"))
    assert report.status == CapabilityStatus.NOT_AVAILABLE
    assert "public" in report.notes[0].lower()


def test_instagram_available_with_public_base_url(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "id")
    monkeypatch.setattr(settings, "META_APP_SECRET", "secret")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.com")
    report = instagram_capability(_account("instagram", page_id="123", ig_business_account_id="ig1"))
    assert report.status == CapabilityStatus.AVAILABLE


def test_no_platform_ever_reports_requiring_a_paid_service(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(settings, "META_APP_ID", "id")
    monkeypatch.setattr(settings, "META_APP_SECRET", "secret")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.com")
    for report in (
        youtube_capability(_account("youtube")),
        facebook_capability(_account("facebook", page_id="1")),
        instagram_capability(_account("instagram", page_id="1", ig_business_account_id="ig1")),
    ):
        assert report.requires_paid_service is False
