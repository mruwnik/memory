# pyright: reportArgumentType=false
"""Unit tests for the CalDAV URL validator in calendar_accounts.

The audit finding (44ad5aed): CalendarAccountCreate accepted a
free-form ``caldav_url`` and the worker later POSTed Basic Auth
(the user's caldav_password) to whatever URL was stored. An
authenticated attacker could:

  * set ``caldav_url=http://attacker.example.com/caldav`` to capture
    credentials (laundering: forward upstream, user sees no error);
  * set ``caldav_url=http://qdrant:6333`` to probe internal services;
  * set ``caldav_url=http://github.com/...`` (plain HTTP) to leak
    Basic-Auth creds to any on-path observer.

Tests target ``_validate_caldav_url`` directly with mocked DNS so we
don't need network access.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from memory.api.calendar_accounts import _validate_caldav_url


def test_validate_caldav_url_accepts_none():
    """``None`` means "field unchanged" on update — no-op."""
    _validate_caldav_url(None)  # no raise


@pytest.mark.parametrize(
    "url",
    [
        "http://caldav.example.com/dav/",
        "HTTP://caldav.example.com/dav/",
        "http://attacker.example.com/caldav",
    ],
)
def test_validate_caldav_url_rejects_plain_http(url):
    """Plain HTTP would send Basic Auth (caldav_password) in clear text."""
    fake_resolution = [(2, 0, 0, "", ("8.8.8.8", 0))]
    with patch(
        "memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution
    ):
        with pytest.raises(HTTPException) as exc:
            _validate_caldav_url(url)
    assert exc.value.status_code == 400
    assert "https" in exc.value.detail.lower()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/caldav",
        "https://10.0.0.5/dav/",
        "https://192.168.1.1/dav/",
        "https://169.254.169.254/dav/",  # AWS / GCP / Azure metadata
        "http://[::1]/dav/",
    ],
)
def test_validate_caldav_url_rejects_private_ip_literals(url):
    with pytest.raises(HTTPException) as exc:
        _validate_caldav_url(url)
    assert exc.value.status_code == 400


def test_validate_caldav_url_rejects_internal_hostname():
    """A hostname that DNS-resolves to an internal IP is rejected even if HTTPS."""
    fake_resolution = [(2, 0, 0, "", ("10.0.0.5", 0))]
    with patch(
        "memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution
    ):
        with pytest.raises(HTTPException) as exc:
            _validate_caldav_url("https://qdrant.docker.internal/dav/")
    assert exc.value.status_code == 400


def test_validate_caldav_url_rejects_non_http_schemes():
    """file://, ext::, etc. are rejected by the underlying URL validator."""
    with pytest.raises(HTTPException) as exc:
        _validate_caldav_url("file:///etc/passwd")
    assert exc.value.status_code == 400


def test_validate_caldav_url_accepts_public_https():
    """Real-world case: public CalDAV server reachable over HTTPS."""
    fake_resolution = [(2, 0, 0, "", ("8.8.8.8", 0))]
    with patch(
        "memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution
    ):
        _validate_caldav_url("https://caldav.example.com/dav/")  # no raise


def test_validate_caldav_url_rejects_unresolvable_hostname():
    """Default-deny: an unresolvable hostname can't be inspected."""
    import socket

    with patch(
        "memory.common.ssrf.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        with pytest.raises(HTTPException) as exc:
            _validate_caldav_url("https://no-such-server.example/dav/")
    assert exc.value.status_code == 400


def test_validate_caldav_url_error_does_not_echo_user_input():
    """The 400 detail explains the rule but doesn't reflect raw input.

    A future contributor relaxing the rules elsewhere shouldn't turn
    the error path into a credential-reflection trap (the user might
    have pasted a URL containing embedded creds like
    ``https://user:pw@host/dav``).
    """
    raw = "https://alice:hunter2@10.0.0.5/dav/"
    with pytest.raises(HTTPException) as exc:
        _validate_caldav_url(raw)
    # We DO surface the parsed reason (which contains the resolved IP)
    # but not the embedded password.
    assert "hunter2" not in exc.value.detail
