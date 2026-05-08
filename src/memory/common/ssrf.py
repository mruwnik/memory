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

# Known limitation: DNS-rebinding TOCTOU

This module closes the **single-resolve** SSRF attacker — anyone whose
authoritative DNS returns a single private IP for a malicious URL gets
caught here at validation time. It does **not** close the
**DNS-rebinding** attacker who controls authoritative DNS for a public
domain and can flip the A record between two lookups (TTL=0): the
validation lookup sees ``8.8.8.8``, the subsequent fetch lookup sees
``169.254.169.254``.

This is a structural property of any "validate then fetch" guard that
doesn't pin the resolved IP across both calls. The proper fix is a
custom transport that dials the validated IP directly while keeping
the original hostname in the Host header (for TLS SNI / virtual
hosts) — see follow-up task ``5a471003`` on the kanban. We treat that
as out of scope here because exploitation requires running an
authoritative DNS server for a public domain (non-trivial threshold).
Until then, callers should at minimum re-call ``validate_public_url``
immediately before each network operation to keep the rebinding
window narrow.
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

    DNS-rebinding TOCTOU caveat: this function does an independent
    ``getaddrinfo`` from the one ``requests``/``aiohttp`` will perform
    when actually dialing the URL. An attacker who controls
    authoritative DNS for a public domain can return a public IP here
    and a private IP at fetch time. We accept that residual risk
    (exploitation requires running auth DNS for a public domain). See
    the module docstring + follow-up task ``5a471003`` for the proper
    fix (pin the validated IP across validate→fetch).
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


def validate_public_hostname(hostname: str) -> None:
    """Schemeless variant of :func:`validate_public_url` for non-HTTP servers.

    Use for things like IMAP/SMTP/CalDAV server names where the user
    supplies a bare hostname (no ``https://`` scheme) and the worker
    later opens a TCP connection to it. Same ``is_safe_ip`` policy: an
    IP-literal or every resolved A/AAAA record must be public.
    Raises ``UnsafeURLError`` on violation; returns silently on success.

    Caller should re-validate immediately before connecting (DNS
    rebinding window).

    Same DNS-rebinding TOCTOU caveat as :func:`validate_public_url`
    applies — see the module docstring + follow-up task ``5a471003``.
    """
    if not hostname or not isinstance(hostname, str):
        raise UnsafeURLError("hostname must be a non-empty string")
    hostname = hostname.strip()
    if not hostname:
        raise UnsafeURLError("hostname must not be blank")

    # Direct IP literal — no DNS lookup.
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if not is_safe_ip(ip):
            raise UnsafeURLError(f"hostname targets non-public IP: {ip}")
        return

    addrs = resolve_hostname(hostname)
    if not addrs:
        raise UnsafeURLError(f"Could not resolve hostname: {hostname}")

    for addr in addrs:
        if not is_safe_ip(addr):
            raise UnsafeURLError(
                f"Hostname {hostname} resolves to non-public IP: {addr}"
            )
