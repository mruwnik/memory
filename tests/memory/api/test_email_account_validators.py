# pyright: reportArgumentType=false
"""Unit tests for the IMAP/SMTP host+port validation in email_accounts.

The audit finding (7ebcae49): EmailAccount.imap_server / imap_port were
unrestricted, so authenticated users could create accounts pointing
``imap_server=postgres, imap_port=5432, use_ssl=false`` and use the
``test_connection`` differential error responses to enumerate which
internal Docker hosts/ports were reachable.

Tests target the helpers directly so they don't need the full FastAPI
+ DB stack.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from memory.api.email_accounts import (
    _validate_imap_settings,
    _validate_smtp_settings,
)


# --- _validate_imap_settings -----------------------------------------------


def test_validate_imap_settings_accepts_none_inputs():
    """``None`` server / port leaves the field unchanged — caller hasn't
    asked to alter it (used by the update path)."""
    _validate_imap_settings(None, None)  # no raise


@pytest.mark.parametrize("port", [143, 993])
def test_validate_imap_settings_accepts_standard_ports(port):
    """143 = plain IMAP, 993 = IMAPS. Both are legitimate."""
    _validate_imap_settings(None, port)  # no raise (server=None skipped)


@pytest.mark.parametrize(
    "port",
    [
        22,        # SSH
        25,        # SMTP — not IMAP
        80,        # HTTP
        443,       # HTTPS
        587,       # submission
        2525,      # alt SMTP
        5432,      # postgres
        6333,      # qdrant
        6379,      # redis
        8000,      # API itself
        65535,     # max valid TCP port
    ],
)
def test_validate_imap_settings_rejects_non_standard_ports(port):
    with pytest.raises(HTTPException) as exc:
        _validate_imap_settings(None, port)
    assert exc.value.status_code == 400
    assert "imap_port" in exc.value.detail


def test_validate_imap_settings_rejects_internal_hostname():
    fake_resolution = [
        (2, 0, 0, "", ("10.0.0.5", 0)),  # AF_INET=2
    ]
    with patch(
        "memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution
    ):
        with pytest.raises(HTTPException) as exc:
            _validate_imap_settings("postgres", 993)
    assert exc.value.status_code == 400
    assert "imap_server" in exc.value.detail


def test_validate_imap_settings_rejects_loopback_literal():
    with pytest.raises(HTTPException) as exc:
        _validate_imap_settings("127.0.0.1", 993)
    assert exc.value.status_code == 400


def test_validate_imap_settings_rejects_imds_literal():
    """169.254.169.254 = AWS / GCP / Azure metadata service."""
    with pytest.raises(HTTPException) as exc:
        _validate_imap_settings("169.254.169.254", 993)
    assert exc.value.status_code == 400


def test_validate_imap_settings_accepts_public_hostname():
    fake_resolution = [
        (2, 0, 0, "", ("8.8.8.8", 0)),
    ]
    with patch(
        "memory.common.ssrf.socket.getaddrinfo", return_value=fake_resolution
    ):
        _validate_imap_settings("imap.example.com", 993)  # no raise


# --- _validate_smtp_settings -----------------------------------------------


@pytest.mark.parametrize("port", [25, 465, 587, 2525])
def test_validate_smtp_settings_accepts_standard_ports(port):
    _validate_smtp_settings(None, port)


@pytest.mark.parametrize("port", [22, 80, 443, 5432, 6379, 8000])
def test_validate_smtp_settings_rejects_non_standard_ports(port):
    with pytest.raises(HTTPException) as exc:
        _validate_smtp_settings(None, port)
    assert exc.value.status_code == 400
    assert "smtp_port" in exc.value.detail


def test_validate_smtp_settings_rejects_loopback_hostname():
    with pytest.raises(HTTPException) as exc:
        _validate_smtp_settings("127.0.0.1", 587)
    assert exc.value.status_code == 400
