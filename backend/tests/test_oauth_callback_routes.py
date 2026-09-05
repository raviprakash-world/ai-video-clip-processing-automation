"""
An OAuth callback must never return a raw 500 -- the user is mid-flow in
their browser and needs to land somewhere readable. This was a real bug:
handle_callback() can raise PublishError (e.g. Meta's token exchange
failing for any reason) or other exceptions the route didn't catch, only
ValueError was handled, so anything else fell through as an unhandled
exception.
"""
from urllib.parse import unquote, urlparse

import pytest

from app.api import routes_publishing
from app.publishing.states import ErrorClass, PublishError

pytestmark = pytest.mark.asyncio


def _message_from_redirect(response) -> str:
    location = response.headers["location"]
    query = urlparse(location).query
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "message":
            return unquote(value)
    return ""


async def test_meta_callback_survives_publish_error(monkeypatch):
    async def _boom(code, state):
        raise PublishError("Meta Graph API error: Invalid OAuth access token.", error_class=ErrorClass.AUTH)

    monkeypatch.setattr(routes_publishing.meta_oauth, "handle_callback", _boom)

    response = await routes_publishing.meta_callback(code="somecode", state="somestate")

    assert response.status_code == 307
    assert "error" in response.headers["location"]
    assert "Invalid OAuth access token" in _message_from_redirect(response)


async def test_meta_callback_survives_unexpected_exception(monkeypatch):
    async def _boom(code, state):
        raise RuntimeError("something unrelated broke")

    monkeypatch.setattr(routes_publishing.meta_oauth, "handle_callback", _boom)

    response = await routes_publishing.meta_callback(code="somecode", state="somestate")

    assert response.status_code == 307
    assert "error" in response.headers["location"]
    assert "something unrelated broke" in _message_from_redirect(response)


async def test_youtube_callback_survives_unexpected_exception(monkeypatch):
    async def _boom(code, state):
        raise RuntimeError("token exchange failed")

    monkeypatch.setattr(routes_publishing.youtube_oauth, "handle_callback", _boom)

    response = await routes_publishing.youtube_callback(code="somecode", state="somestate")

    assert response.status_code == 307
    assert "error" in response.headers["location"]
    assert "token exchange failed" in _message_from_redirect(response)


async def test_meta_callback_reports_instagram_warning_without_failing_facebook(monkeypatch):
    """Facebook connecting successfully must not be masked by an Instagram-only
    problem -- handle_callback returns (accounts, warnings) precisely so this
    case reads as a (partial) success, not an error page."""
    from app.db.models import SocialAccount

    fb_account = SocialAccount(id="facebook:1", platform="facebook", account_id="1", account_name="meme alchemy")

    async def _partial_success(code, state):
        return [fb_account], ["Instagram not linked: missing pages_read_engagement."]

    monkeypatch.setattr(routes_publishing.meta_oauth, "handle_callback", _partial_success)

    response = await routes_publishing.meta_callback(code="c", state="s")

    assert response.status_code == 307
    assert "oauth=success" in response.headers["location"]
    message = _message_from_redirect(response)
    assert "meme alchemy" in message
    assert "Instagram not linked" in message


async def test_meta_callback_still_reports_invalid_state_cleanly(monkeypatch):
    """The pre-existing ValueError path (bad/expired CSRF state) keeps working."""

    async def _boom(code, state):
        raise ValueError("Invalid or expired OAuth state.")

    monkeypatch.setattr(routes_publishing.meta_oauth, "handle_callback", _boom)

    response = await routes_publishing.meta_callback(code="c", state="s")

    assert "Invalid or expired OAuth state" in _message_from_redirect(response)
