"""Tests for the SSRF guard in memory.common.ssrf."""

from __future__ import annotations

import ipaddress
import socket
from unittest.mock import patch

import pytest

from memory.common.ssrf import (
    UnsafeURLError,
    is_safe_ip,
    validate_public_url,
)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "127.0.0.5",  # loopback range
        "10.0.0.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "172.31.255.254",  # RFC1918
        "192.168.1.1",  # RFC1918
        "169.254.169.254",  # AWS / GCP / Azure metadata IMDS
        "169.254.0.1",  # link-local
        "224.0.0.1",  # multicast
        "0.0.0.0",  # unspecified
        "255.255.255.255",  # broadcast (reserved)
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique-local (private)
        "fd00::1",  # IPv6 unique-local
        "::",  # IPv6 unspecified
        "ff02::1",  # IPv6 multicast
    ],
)
def test_is_safe_ip_rejects_unsafe(ip):
    assert is_safe_ip(ipaddress.ip_address(ip)) is False


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",
        "1.1.1.1",
        "2606:4700:4700::1111",  # Cloudflare DNS
        "2001:4860:4860::8888",  # Google DNS
    ],
)
def test_is_safe_ip_accepts_public(ip):
    assert is_safe_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize(
    "url,reason_substring",
    [
        ("file:///etc/passwd", "scheme"),
        ("gopher://localhost/", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("data:text/plain,hello", "scheme"),
        ("ftp://example.com/", "scheme"),
        ("not even a url", "scheme"),  # urlparse → scheme=""
    ],
)
def test_validate_public_url_rejects_bad_schemes(url, reason_substring):
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url(url)
    assert reason_substring in str(exc_info.value)


def test_validate_public_url_rejects_no_hostname():
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("http:///path-only")
    assert "hostname" in str(exc_info.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:5000/",
        "https://10.0.0.1/",
        "https://192.168.1.1/admin",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://0.0.0.0/",
    ],
)
def test_validate_public_url_rejects_private_ip_literals(url):
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url(url)
    assert "non-public IP" in str(exc_info.value)


def test_validate_public_url_accepts_public_ip_literal():
    # No DNS — pure literal — must not raise.
    validate_public_url("https://8.8.8.8/")


def test_validate_public_url_rejects_resolved_to_private():
    """Hostname that DNS-resolves to an internal IP must be rejected."""
    fake_resolution = [
        (socket.AF_INET, 0, 0, "", ("10.0.0.5", 0)),
    ]
    with patch("memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution):
        with pytest.raises(UnsafeURLError) as exc_info:
            validate_public_url("https://internal-service.example.com/")
    assert "non-public IP" in str(exc_info.value)


def test_validate_public_url_rejects_mixed_resolution():
    """Hostname that resolves to a mix of public + private IPs is rejected
    on the private one — defends against attackers who add a private A
    record alongside a public one (DNS rebinding precursor)."""
    fake_resolution = [
        (socket.AF_INET, 0, 0, "", ("203.0.113.7", 0)),
        (socket.AF_INET, 0, 0, "", ("169.254.169.254", 0)),
    ]
    with patch("memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution):
        with pytest.raises(UnsafeURLError):
            validate_public_url("https://attacker.example.com/")


def test_validate_public_url_accepts_public_resolution():
    fake_resolution = [
        (socket.AF_INET, 0, 0, "", ("8.8.8.8", 0)),
    ]
    with patch("memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution):
        validate_public_url("https://dns.google/")  # no raise


def test_validate_public_url_rejects_unresolvable():
    """An unresolvable hostname can't be inspected → reject rather than
    forward (default-deny posture)."""
    with patch(
        "memory.common.ssrf.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        with pytest.raises(UnsafeURLError) as exc_info:
            validate_public_url("https://no-such-host.example/")
    assert "Could not resolve" in str(exc_info.value)


def test_validate_public_url_rejects_ipv6_private_resolution():
    """IPv6 ULA (fc00::/7) result must be rejected."""
    fake_resolution = [
        (socket.AF_INET6, 0, 0, "", ("fc00::1", 0, 0, 0)),
    ]
    with patch("memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution):
        with pytest.raises(UnsafeURLError):
            validate_public_url("https://internal.example.com/")


def test_validate_public_url_strips_ipv6_scope_id():
    """IPv6 scope-id (fe80::1%eth0) must not crash the validator."""
    fake_resolution = [
        (socket.AF_INET6, 0, 0, "", ("fe80::1%eth0", 0, 0, 0)),
    ]
    with patch("memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution):
        with pytest.raises(UnsafeURLError):
            validate_public_url("https://link-local.example.com/")
