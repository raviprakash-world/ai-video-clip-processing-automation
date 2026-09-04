"""
Meta's authorization dialog for business permissions (pages_manage_posts,
instagram_content_publish, etc.) requires a Login Configuration ID rather
than a plain scope= list -- this was discovered to be a real gap (the code
built a classic scope= URL that doesn't work with apps created through
Meta's current use-case-based app creation flow) and fixed here.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import settings
from app.publishing.oauth import meta_oauth

pytestmark = pytest.mark.asyncio


async def test_uses_config_id_when_configured(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "app123")
    monkeypatch.setattr(settings, "META_LOGIN_CONFIG_ID", "cfg456")

    url = await meta_oauth.build_authorization_url()

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.facebook.com"
    assert query["config_id"] == ["cfg456"]
    assert "scope" not in query
    assert query["client_id"] == ["app123"]
    assert query["response_type"] == ["code"]
    assert "state" in query


async def test_falls_back_to_legacy_scope_without_config_id(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_ID", "app123")
    monkeypatch.setattr(settings, "META_LOGIN_CONFIG_ID", "")

    url = await meta_oauth.build_authorization_url()

    query = parse_qs(urlparse(url).query)
    assert "config_id" not in query
    assert "pages_manage_posts" in query["scope"][0]
    assert "instagram_content_publish" in query["scope"][0]


async def test_dialog_uses_same_api_version_as_graph_calls(isolated_db, monkeypatch):
    from app.publishing.meta_base import GRAPH_API_BASE

    monkeypatch.setattr(settings, "META_APP_ID", "app123")
    monkeypatch.setattr(settings, "META_LOGIN_CONFIG_ID", "cfg456")

    url = await meta_oauth.build_authorization_url()
    version = GRAPH_API_BASE.rsplit("/", 1)[-1]
    assert f"/{version}/dialog/oauth" in url
