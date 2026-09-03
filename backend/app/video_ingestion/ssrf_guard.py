"""
SSRF protection for remote URL ingestion (section 22).

Any code that fetches a user-supplied URL server-side MUST call
ensure_public_host() on that URL's hostname before connecting, and again on
every redirect hop's hostname -- a server can redirect to an internal
address even if the original URL was public.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

from app.errors import VideoDownloadFailedError


async def ensure_public_host(hostname: str) -> None:
    """Resolve hostname and reject it if any resolved address is private/loopback/link-local/reserved.

    Best-effort, resolve-then-connect guard: checked at request time (and
    again on every redirect hop), not connection-pinned. A DNS-rebinding
    attacker with control of the resolver in the tiny window between check
    and connect could theoretically still slip through; a hardened
    implementation would resolve once and connect directly to the
    validated IP with the original Host header. Acceptable for this MVP,
    where operators are expected to supply their own trusted URLs.
    """
    if not hostname:
        raise VideoDownloadFailedError("URL has no hostname.")

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror as exc:
        raise VideoDownloadFailedError(f"Could not resolve hostname '{hostname}'.") from exc

    if not infos:
        raise VideoDownloadFailedError(f"Could not resolve hostname '{hostname}'.")

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise VideoDownloadFailedError(
                f"URL resolves to a non-public address ({ip}); direct URL ingestion only allows public hosts.",
                details={"hostname": hostname, "resolved_ip": str(ip)},
            )
