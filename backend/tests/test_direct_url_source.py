"""
DirectUrlSource tests against a local HTTP server serving a real synthetic
video. The SSRF guard is unit-tested separately (test_ssrf_guard.py) and is
patched to a no-op here since this test server necessarily runs on
loopback -- the guard would otherwise (correctly) reject it.
"""
from __future__ import annotations

import http.server
import shutil
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings
from app.errors import UnsupportedVideoFormatError, VideoDownloadFailedError
from app.video_ingestion.remote_source import DirectUrlSource
from app.video_metadata.probe import probe_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available on PATH")


@pytest.fixture
def file_server(tmp_path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(tmp_path / "video.mp4"),
        ],
        check=True,
        capture_output=True,
    )
    (tmp_path / "not-a-video.txt").write_bytes(b"hello world")

    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(*args, directory=str(tmp_path), **kwargs)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_direct_url_download_success(file_server, tmp_path):
    dest_dir = tmp_path / "job"
    with patch("app.video_ingestion.remote_source.ensure_public_host", return_value=None):
        source = DirectUrlSource(f"{file_server}/video.mp4")
        result_path = await source.obtain(dest_dir)

    assert result_path.exists()
    assert result_path.name == "source.mp4"
    metadata = await probe_video(result_path)
    assert metadata.width == 320 and metadata.height == 240


@pytest.mark.asyncio
async def test_direct_url_404_rejected(file_server, tmp_path):
    with patch("app.video_ingestion.remote_source.ensure_public_host", return_value=None):
        source = DirectUrlSource(f"{file_server}/does-not-exist.mp4")
        with pytest.raises(VideoDownloadFailedError):
            await source.obtain(tmp_path / "job")


@pytest.mark.asyncio
async def test_direct_url_size_cap_enforced(file_server, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 100)  # smaller than the test video
    with patch("app.video_ingestion.remote_source.ensure_public_host", return_value=None):
        source = DirectUrlSource(f"{file_server}/video.mp4")
        with pytest.raises(VideoDownloadFailedError):
            await source.obtain(tmp_path / "job")


@pytest.mark.asyncio
async def test_disallowed_scheme_rejected_without_network(tmp_path):
    source = DirectUrlSource("ftp://example.com/video.mp4")
    with pytest.raises(UnsupportedVideoFormatError):
        await source.obtain(tmp_path / "job")


@pytest.mark.asyncio
async def test_non_video_content_is_still_caught_by_ffprobe(file_server, tmp_path):
    """The download itself will succeed for any file; the real content check
    happens when the caller re-probes with ffprobe (section 7: never trust a
    download as a valid video without ffprobe confirmation)."""
    dest_dir = tmp_path / "job"
    with patch("app.video_ingestion.remote_source.ensure_public_host", return_value=None):
        source = DirectUrlSource(f"{file_server}/not-a-video.txt")
        result_path = await source.obtain(dest_dir)

    from app.errors import VideoCorruptedError

    with pytest.raises(VideoCorruptedError):
        await probe_video(result_path)
