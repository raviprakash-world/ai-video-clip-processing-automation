"""Single place mapping platform name -> PublishingProvider instance."""
from __future__ import annotations

from app.publishing.facebook_provider import FacebookPublishingProvider
from app.publishing.instagram_provider import InstagramPublishingProvider
from app.publishing.provider import PublishingProvider
from app.publishing.youtube_provider import YouTubePublishingProvider

PROVIDERS: dict[str, PublishingProvider] = {
    "youtube": YouTubePublishingProvider(),
    "facebook": FacebookPublishingProvider(),
    "instagram": InstagramPublishingProvider(),
}

PLATFORMS = tuple(PROVIDERS.keys())


def get_provider(platform: str) -> PublishingProvider:
    provider = PROVIDERS.get(platform)
    if not provider:
        raise ValueError(f"Unknown publishing platform '{platform}'.")
    return provider
