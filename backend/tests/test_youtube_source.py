"""
Offline checks for YouTubeSource's authorization/host boundary. The actual
network download is exercised manually (see README) rather than in the
automated suite, to keep CI fast and independent of YouTube's availability.
"""
import pytest

from app.errors import UnsupportedVideoFormatError
from app.video_ingestion.remote_source import YouTubeSource, is_youtube_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://youtu.be/dQw4w9WgXcQ", True),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://vimeo.com/12345", False),
        ("https://example.com/video.mp4", False),
        ("https://evil.com/?redirect=youtube.com", False),
    ],
)
def test_is_youtube_url(url, expected):
    assert is_youtube_url(url) is expected


@pytest.mark.asyncio
async def test_non_youtube_host_rejected(tmp_path):
    source = YouTubeSource("https://evil.com/watch?v=abc")
    with pytest.raises(UnsupportedVideoFormatError):
        await source.obtain(tmp_path)


@pytest.mark.asyncio
async def test_disallowed_scheme_rejected(tmp_path):
    source = YouTubeSource("ftp://youtube.com/watch?v=abc")
    with pytest.raises(UnsupportedVideoFormatError):
        await source.obtain(tmp_path)
