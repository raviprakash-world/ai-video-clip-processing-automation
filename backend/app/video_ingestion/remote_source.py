"""
Remote video sources (section 6/7/22).

DirectUrlSource: downloads a URL that points straight at a video file.
  - http/https only
  - SSRF-guarded: hostname resolution is checked against private/loopback/
    link-local/reserved ranges before every connection, including redirects
  - hard size cap enforced while streaming (never trusts Content-Length alone)
  - connect/read timeouts
  - writes to a temp path and only exposes it after the stream completes;
    the caller (job creation path) always re-probes with ffprobe before
    trusting the file, so a stream that "completes" but isn't a real video
    still gets rejected downstream

YouTubeSource: downloads via yt-dlp, restricted to youtube.com/youtu.be
hosts, with no cookies/auth/age-gate/geo/DRM bypass of any kind -- if
yt-dlp can't fetch it anonymously and legitimately, this raises rather than
trying harder. The caller is responsible for only submitting URLs they are
authorized to download.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.errors import UnsupportedVideoFormatError, VideoDownloadFailedError, VideoNotFoundError
from app.video_ingestion.source import VideoSource
from app.video_ingestion.ssrf_guard import ensure_public_host

_ALLOWED_SCHEMES = ("http", "https")
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class DirectUrlSource(VideoSource):
    source_type = "direct_video_url"

    def __init__(self, url: str):
        self.url = url

    async def obtain(self, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_dir / "source.download"

        current_url = self.url
        last_parsed = None
        timeout = httpx.Timeout(connect=10.0, read=settings.DOWNLOAD_TIMEOUT_SECONDS, write=10.0, pool=10.0)

        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            for _hop in range(settings.MAX_DOWNLOAD_REDIRECTS + 1):
                parsed = urlparse(current_url)
                if parsed.scheme not in _ALLOWED_SCHEMES:
                    raise UnsupportedVideoFormatError(
                        f"Unsupported URL scheme '{parsed.scheme}'. Only http/https are allowed."
                    )
                await ensure_public_host(parsed.hostname or "")
                last_parsed = parsed

                try:
                    async with client.stream("GET", current_url) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise VideoDownloadFailedError("Redirect response is missing a Location header.")
                            current_url = urljoin(current_url, location)
                            continue

                        if response.status_code != 200:
                            raise VideoDownloadFailedError(
                                f"Download failed with HTTP {response.status_code}.",
                                details={"status_code": response.status_code, "url": current_url},
                            )

                        content_length = response.headers.get("content-length")
                        if content_length and int(content_length) > settings.MAX_UPLOAD_SIZE_BYTES:
                            raise VideoDownloadFailedError(
                                "Remote file's reported size exceeds the maximum allowed download size."
                            )

                        size = 0
                        with tmp_path.open("wb") as f:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                size += len(chunk)
                                if size > settings.MAX_UPLOAD_SIZE_BYTES:
                                    raise VideoDownloadFailedError(
                                        "Remote file exceeded the maximum allowed download size while streaming."
                                    )
                                f.write(chunk)
                    break
                except httpx.TimeoutException as exc:
                    raise VideoDownloadFailedError("Timed out while downloading the video URL.") from exc
                except httpx.HTTPError as exc:
                    raise VideoDownloadFailedError(f"Network error while downloading the video URL: {exc}") from exc
            else:
                raise VideoDownloadFailedError("Too many redirects while downloading the video URL.")

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            tmp_path.unlink(missing_ok=True)
            raise VideoDownloadFailedError("Download completed but produced an empty file.")

        suffix = Path(last_parsed.path).suffix.lower() if last_parsed else ""
        if suffix not in settings.ALLOWED_UPLOAD_EXTENSIONS:
            suffix = ".mp4"
        final_path = dest_dir / f"source{suffix}"
        tmp_path.replace(final_path)
        return final_path


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


def is_youtube_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in _YOUTUBE_HOSTS


class YouTubeSource(VideoSource):
    source_type = "youtube_url"

    def __init__(self, url: str):
        self.url = url

    async def obtain(self, dest_dir: Path) -> Path:
        parsed = urlparse(self.url)
        if parsed.scheme not in _ALLOWED_SCHEMES or (parsed.hostname or "").lower() not in _YOUTUBE_HOSTS:
            raise UnsupportedVideoFormatError(
                "Only youtube.com / youtu.be URLs are supported for YouTube ingestion.",
                details={"url": self.url},
            )

        try:
            import yt_dlp
        except ImportError as exc:
            raise VideoDownloadFailedError(
                "yt-dlp is not installed on the server; YouTube ingestion is unavailable."
            ) from exc

        dest_dir.mkdir(parents=True, exist_ok=True)
        for stale in dest_dir.glob("source.*"):
            stale.unlink(missing_ok=True)

        max_height = settings.YTDLP_MAX_HEIGHT
        ydl_opts = {
            "format": f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/best[height<={max_height}][ext=mp4]/best",
            "outtmpl": str(dest_dir / "source.%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "max_filesize": settings.MAX_UPLOAD_SIZE_BYTES,
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            # Deliberately no cookies, no age-gate/geo/DRM bypass, no authentication of
            # any kind: only content yt-dlp can fetch anonymously and legitimately is
            # supported. If a video requires sign-in or is otherwise restricted, this
            # raises rather than working around the restriction.
        }

        def _download() -> None:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

        try:
            await asyncio.wait_for(asyncio.to_thread(_download), timeout=settings.DOWNLOAD_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise VideoDownloadFailedError("Timed out while downloading the YouTube video.") from exc
        except Exception as exc:  # yt_dlp raises its own DownloadError/ExtractorError, etc.
            raise VideoDownloadFailedError(f"YouTube download failed: {exc}") from exc

        candidates = sorted(dest_dir.glob("source.*"))
        if not candidates:
            raise VideoNotFoundError("YouTube download completed but no output file was found.")
        return candidates[0]
