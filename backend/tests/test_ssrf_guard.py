import socket
from unittest.mock import patch

import pytest

from app.errors import VideoDownloadFailedError
from app.video_ingestion.ssrf_guard import ensure_public_host


def _fake_addrinfo(ip: str):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.mark.asyncio
async def test_public_ip_allowed():
    with patch("socket.getaddrinfo", return_value=_fake_addrinfo("8.8.8.8")):
        await ensure_public_host("example.com")  # should not raise


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",       # loopback
        "10.0.0.5",        # private
        "192.168.1.1",     # private
        "172.16.0.1",      # private
        "169.254.169.254", # link-local / cloud metadata endpoint
        "0.0.0.0",         # unspecified
        "::1",             # loopback IPv6
    ],
)
@pytest.mark.asyncio
async def test_non_public_ip_rejected(ip):
    with patch("socket.getaddrinfo", return_value=_fake_addrinfo(ip)):
        with pytest.raises(VideoDownloadFailedError):
            await ensure_public_host("attacker-controlled.example")


@pytest.mark.asyncio
async def test_unresolvable_host_rejected():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
        with pytest.raises(VideoDownloadFailedError):
            await ensure_public_host("does-not-exist.invalid")


@pytest.mark.asyncio
async def test_empty_hostname_rejected():
    with pytest.raises(VideoDownloadFailedError):
        await ensure_public_host("")
