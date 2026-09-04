"""
Platform-neutral publish metadata (section 15).

The queue and worker only ever deal with PublishMetadata; each provider
adapts it into whatever shape its own API expects. This is built once, at
queue-creation time, from the AI JSON -- it is a SNAPSHOT (see
PublishQueueItem.metadata_json), not a live reference back to the clip, so
editing the original analysis later never silently changes a post that's
already scheduled or published.

Only clip.title_options / clip.caption / clip.hashtags / clip.hook are
read here. Nothing about "what to post" is invented -- if the JSON has no
usable title, callers fall back through title_options -> hook, never to a
generated string.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.validated import ValidatedClip


@dataclass(frozen=True)
class PublishMetadata:
    title: str | None
    caption: str | None
    hashtags: list[str] = field(default_factory=list)

    def combined_caption(self) -> str:
        """caption + blank line + space-joined hashtags -- the common IG/FB shape (section 13)."""
        body = (self.caption or "").strip()
        tags = " ".join(self.hashtags)
        if body and tags:
            return f"{body}\n\n{tags}"
        return body or tags


def build_publish_metadata(clip: ValidatedClip) -> PublishMetadata:
    title = clip.title_options.get("curiosity") or clip.title_options.get("direct") or clip.hook or None
    return PublishMetadata(title=title, caption=clip.caption or None, hashtags=list(clip.hashtags))
