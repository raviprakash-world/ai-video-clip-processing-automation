"""
Remote video sources -- NOT implemented in this MVP.

These classes exist to fix the interface contract so a future implementation
plugs in without touching the processing engine, the API layer's job-creation
code, or anything downstream of VideoSource.obtain(). See section 6/7/22.

When implementing DirectUrlSource, it MUST:
  - restrict schemes to http/https only
  - resolve the hostname and reject loopback/private/link-local/metadata
    IP ranges before connecting (SSRF protection)
  - enforce a hard download size cap while streaming (never trust
    Content-Length alone) and a connect/read timeout
  - write to a temp path and only rename/expose it after the stream
    completes successfully and ffprobe confirms a valid video
  - never pass the URL, or any part of it, through a shell

When implementing YouTubeSource, it MUST only operate on content the
requesting user is authorized to download, and must never attempt to
bypass DRM, sign-in walls, or platform access controls.
"""
from __future__ import annotations

from pathlib import Path

from app.video_ingestion.source import VideoSource


class DirectUrlSource(VideoSource):
    source_type = "direct_video_url"

    def __init__(self, url: str):
        self.url = url

    async def obtain(self, dest_dir: Path) -> Path:
        raise NotImplementedError(
            "DirectUrlSource is not implemented in this MVP. Local file upload is the "
            "only supported ingestion path today; see module docstring for the "
            "security requirements a future implementation must satisfy."
        )


class YouTubeSource(VideoSource):
    source_type = "youtube_url"

    def __init__(self, url: str):
        self.url = url

    async def obtain(self, dest_dir: Path) -> Path:
        raise NotImplementedError(
            "YouTubeSource is not implemented in this MVP. Local file upload is the "
            "only supported ingestion path today; see module docstring for the "
            "authorization requirements a future implementation must satisfy."
        )
