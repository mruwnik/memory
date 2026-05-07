"""SSRF protection for endpoints that fetch user-supplied URLs.

User-controlled URLs reaching server-side HTTP requests are SSRF sinks
(CWE-918). Without IP-range checks, an attacker can pivot to:

- AWS / GCP / Azure metadata services (credential extraction)
- Internal services on the Docker network (qdrant, postgres, redis)
- Loopback to the API itself (auth bypass via header smuggling)
- Internal port scanning / reconnaissance

``validate_public_url`` resolves the hostname and rejects anything in
private / loopback / link-local / multicast / reserved ranges. Re-resolve
at fetch time when feasible to defeat DNS rebinding.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    """Raised when a user-supplied URL would target a private/internal address."""


# http and https only — block file://, gopher://, ftp://, data:, javascript: etc.
ALLOWED_SCHEMES = frozenset({"http", "https"})


def is_safe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True iff ``ip`` is a routable public address.

    Excludes loopback (127.0.0.0/8, ::1), private (RFC1918), link-local
    (169.254.0.0/16 incl. AWS IMDS), multicast, reserved, and the
    "unspecified" 0.0.0.0/::. We deliberately also exclude link-local
    multicast addresses and the IPv6 unique-local fc00::/7 range.
    """
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_hostname(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``hostname`` to all A / AAAA records (no caching).

    Returns ``[]`` if resolution fails — caller should treat that as
    untrusted (we reject rather than fetch).
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for family, _, _, _, sockaddr in infos:
        host = str(sockaddr[0])
        if family == socket.AF_INET:
            out.append(ipaddress.IPv4Address(host))
        elif family == socket.AF_INET6:
            # AF_INET6 sockaddr host may carry a %scope suffix; strip it.
            out.append(ipaddress.IPv6Address(host.split("%", 1)[0]))
    return out


def validate_public_url(url: str) -> None:
    """Raise ``UnsafeURLError`` if ``url`` is not safe to fetch server-side.

    Checks scheme, hostname presence, and DNS resolution to ensure the
    URL doesn't point at a private/internal host. Caller should re-call
    this immediately before the fetch (e.g. inside the worker job too)
    to limit DNS-rebinding windows.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"URL scheme not allowed: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL has no hostname")

    # Direct IP literal — no DNS lookup.
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if not is_safe_ip(ip):
            raise UnsafeURLError(
                f"URL targets non-public IP: {ip}"
            )
        return

    addrs = resolve_hostname(hostname)
    if not addrs:
        raise UnsafeURLError(f"Could not resolve hostname: {hostname}")

    for addr in addrs:
        if not is_safe_ip(addr):
            raise UnsafeURLError(
                f"Hostname {hostname} resolves to non-public IP: {addr}"
            )
