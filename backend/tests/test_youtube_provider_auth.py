"""A revoked/expired Google refresh token must become an AUTH failure and persist
EXPIRED -- not a TRANSIENT retry loop with the account still shown as AVAILABLE."""
import pytest
from google.auth.exceptions import RefreshError

from app.db.models import SocialAccount
from app.publishing import youtube_provider
from app.publishing.metadata import PublishMetadata
from app.publishing.states import ErrorClass, PublishError
from app.publishing.token_crypto import encrypt_token

pytestmark = pytest.mark.asyncio


async def test_revoked_refresh_token_is_auth_error_and_persists_expired(isolated_db, monkeypatch, sample_video_file):
    from app.db.base import session_scope

    async with session_scope() as session:
        session.add(SocialAccount(
            id="youtube:1", platform="youtube", account_id="1", account_name="x",
            access_token_encrypted=encrypt_token("a"), refresh_token_encrypted=encrypt_token("r"), status="CONNECTED",
        ))
        account = await session.get(SocialAccount, "youtube:1")

    def _revoked(*a, **k):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(youtube_provider, "build", _revoked)

    with pytest.raises(PublishError) as excinfo:
        await youtube_provider.YouTubePublishingProvider().publish(
            account, PublishMetadata(title="t", caption="c", hashtags=[]), sample_video_file
        )
    assert excinfo.value.error_class == ErrorClass.AUTH

    async with session_scope() as session:
        assert (await session.get(SocialAccount, "youtube:1")).status == "EXPIRED"
