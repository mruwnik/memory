"""Tests for the FastAPI startup safety validators."""

import pytest

from memory.api import safety
from memory.api.safety import validate_disable_auth_safety
from memory.common import settings


@pytest.fixture
def safe_disable_auth_settings(monkeypatch):
    """Default to a safe (loopback) configuration with DISABLE_AUTH=True."""
    monkeypatch.setattr(settings, "DISABLE_AUTH", True)
    monkeypatch.setattr(settings, "SERVER_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", False)
    monkeypatch.setattr(
        settings,
        "OAUTH_REDIRECT_URI_ALLOWLIST",
        ["http://localhost", "http://127.0.0.1"],
    )
    monkeypatch.setattr(settings, "DISABLE_AUTH_CONFIRM", "")


def test_validate_disable_auth_off_does_nothing(monkeypatch):
    """When DISABLE_AUTH=False, the validator is a no-op even with prod signals."""
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    monkeypatch.setattr(
        settings, "OAUTH_REDIRECT_URI_ALLOWLIST", ["https://app.example.com"]
    )
    validate_disable_auth_safety()  # must not raise


def test_validate_disable_auth_safe_loopback(safe_disable_auth_settings):
    """Loopback dev config with DISABLE_AUTH=True is allowed."""
    validate_disable_auth_safety()  # must not raise


@pytest.mark.parametrize(
    "loopback_url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
    ],
)
def test_loopback_urls_recognized(safe_disable_auth_settings, monkeypatch, loopback_url):
    monkeypatch.setattr(settings, "SERVER_URL", loopback_url)
    validate_disable_auth_safety()


@pytest.mark.parametrize(
    "prod_url",
    [
        "https://memory.example.com",
        "http://192.168.1.10",
        "https://10.0.0.5:8080",
        "http://memory.local",
        # 0.0.0.0 is INADDR_ANY (bind on all interfaces). A SERVER_URL of
        # 0.0.0.0 is a statement of intent to reach the API from elsewhere
        # on the network, NOT a loopback declaration — it must NOT bypass
        # the DISABLE_AUTH=true safety check. See is_loopback_url
        # docstring for the OS-dependent dialing footguns.
        "http://0.0.0.0:8000",
        "http://0.0.0.0",
    ],
)
def test_non_loopback_server_url_blocks(safe_disable_auth_settings, monkeypatch, prod_url):
    monkeypatch.setattr(settings, "SERVER_URL", prod_url)
    with pytest.raises(RuntimeError, match="DISABLE_AUTH"):
        validate_disable_auth_safety()


def test_s3_backup_enabled_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    with pytest.raises(RuntimeError, match="S3_BACKUP_ENABLED"):
        validate_disable_auth_safety()


def test_non_loopback_oauth_allowlist_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(
        settings,
        "OAUTH_REDIRECT_URI_ALLOWLIST",
        ["http://localhost", "https://app.example.com"],
    )
    with pytest.raises(RuntimeError, match="OAUTH_REDIRECT_URI_ALLOWLIST"):
        validate_disable_auth_safety()


def test_wildcard_oauth_allowlist_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REDIRECT_URI_ALLOWLIST", ["*"])
    with pytest.raises(RuntimeError, match="wildcard"):
        validate_disable_auth_safety()


def test_explicit_override_allows_prod(safe_disable_auth_settings, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    monkeypatch.setattr(settings, "DISABLE_AUTH_CONFIRM", "yes-i-am-sure")
    with caplog.at_level("WARNING"):
        validate_disable_auth_safety()
    assert any(
        "DISABLE_AUTH" in record.message and "Proceeding" in record.message
        for record in caplog.records
    )


def test_wrong_override_value_still_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    # Anything other than the exact magic string must not bypass.
    monkeypatch.setattr(settings, "DISABLE_AUTH_CONFIRM", "yes")
    with pytest.raises(RuntimeError):
        validate_disable_auth_safety()


def test_error_message_lists_all_signals(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    monkeypatch.setattr(
        settings, "OAUTH_REDIRECT_URI_ALLOWLIST", ["https://app.example.com"]
    )
    with pytest.raises(RuntimeError) as exc:
        validate_disable_auth_safety()
    msg = str(exc.value)
    assert "SERVER_URL" in msg
    assert "S3_BACKUP_ENABLED" in msg
    assert "OAUTH_REDIRECT_URI_ALLOWLIST" in msg


# --- is_loopback_url ---


def test_is_loopback_url_recognises_canonical_hostnames():
    """Only the exact loopback hostnames count as loopback."""
    for url in [
        "http://localhost",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
        "http://[::1]",
        "http://[::1]:8000",
    ]:
        assert safety.is_loopback_url(url), url


def test_is_loopback_url_rejects_wildcard_and_public_hosts():
    """``0.0.0.0`` is the wildcard bind address (INADDR_ANY), not loopback."""
    for url in [
        "http://0.0.0.0",
        "http://0.0.0.0:8000",
        "https://memory.example.com",
        "http://192.168.1.10",
        "http://memory.local",
    ]:
        assert not safety.is_loopback_url(url), url


def test_is_loopback_url_treats_empty_as_loopback():
    """Empty URL → caller has nothing to validate; treat as safe."""
    assert safety.is_loopback_url("") is True
