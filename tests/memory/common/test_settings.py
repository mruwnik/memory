"""Tests for memory.common.settings safety validators."""

import pytest

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
    settings.validate_disable_auth_safety()  # must not raise


def test_validate_disable_auth_safe_loopback(safe_disable_auth_settings):
    """Loopback dev config with DISABLE_AUTH=True is allowed."""
    settings.validate_disable_auth_safety()  # must not raise


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
    settings.validate_disable_auth_safety()


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
        # the DISABLE_AUTH=true safety check. See _is_loopback_url
        # docstring for the OS-dependent dialing footguns.
        "http://0.0.0.0:8000",
        "http://0.0.0.0",
    ],
)
def test_non_loopback_server_url_blocks(safe_disable_auth_settings, monkeypatch, prod_url):
    monkeypatch.setattr(settings, "SERVER_URL", prod_url)
    with pytest.raises(RuntimeError, match="DISABLE_AUTH"):
        settings.validate_disable_auth_safety()


def test_s3_backup_enabled_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    with pytest.raises(RuntimeError, match="S3_BACKUP_ENABLED"):
        settings.validate_disable_auth_safety()


def test_non_loopback_oauth_allowlist_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(
        settings,
        "OAUTH_REDIRECT_URI_ALLOWLIST",
        ["http://localhost", "https://app.example.com"],
    )
    with pytest.raises(RuntimeError, match="OAUTH_REDIRECT_URI_ALLOWLIST"):
        settings.validate_disable_auth_safety()


def test_wildcard_oauth_allowlist_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REDIRECT_URI_ALLOWLIST", ["*"])
    with pytest.raises(RuntimeError, match="wildcard"):
        settings.validate_disable_auth_safety()


def test_explicit_override_allows_prod(safe_disable_auth_settings, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    monkeypatch.setattr(settings, "DISABLE_AUTH_CONFIRM", "yes-i-am-sure")
    with caplog.at_level("WARNING"):
        settings.validate_disable_auth_safety()
    assert any(
        "DISABLE_AUTH" in record.message and "Proceeding" in record.message
        for record in caplog.records
    )


def test_wrong_override_value_still_blocks(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    # Anything other than the exact magic string must not bypass.
    monkeypatch.setattr(settings, "DISABLE_AUTH_CONFIRM", "yes")
    with pytest.raises(RuntimeError):
        settings.validate_disable_auth_safety()


def test_error_message_lists_all_signals(safe_disable_auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    monkeypatch.setattr(settings, "S3_BACKUP_ENABLED", True)
    monkeypatch.setattr(
        settings, "OAUTH_REDIRECT_URI_ALLOWLIST", ["https://app.example.com"]
    )
    with pytest.raises(RuntimeError) as exc:
        settings.validate_disable_auth_safety()
    msg = str(exc.value)
    assert "SERVER_URL" in msg
    assert "S3_BACKUP_ENABLED" in msg
    assert "OAUTH_REDIRECT_URI_ALLOWLIST" in msg


# --- TRANSFER_TOKEN_SECRET HKDF derivation ---------------------------------
#
# When the operator hasn't set TRANSFER_TOKEN_SECRET explicitly but DOES
# have SECRETS_ENCRYPTION_KEY configured, settings.py derives the transfer
# secret via HKDF-SHA256 with a domain-separating ``info`` string. The
# property under test: the derived secret is mathematically distinct from
# the input, so a leak of either does not trivially compromise the other.


def test_derive_transfer_token_secret_is_deterministic():
    """Same master key → same derived secret. Required so all API
    instances in a deployment compute the same HMAC key without any
    coordination/round-trip."""
    a = settings._derive_transfer_token_secret("master-key-abc")
    b = settings._derive_transfer_token_secret("master-key-abc")
    assert a == b


def test_derive_transfer_token_secret_changes_with_input():
    """Different master keys produce different derived secrets. Without
    this, the HKDF would be a no-op and transfer URLs minted under one
    deployment would verify under another."""
    a = settings._derive_transfer_token_secret("master-key-abc")
    b = settings._derive_transfer_token_secret("master-key-xyz")
    assert a != b


def test_derive_transfer_token_secret_is_distinct_from_master():
    """REGRESSION GUARD: the previous bare ``or`` fallback returned the
    master key itself. The HKDF replacement MUST produce a value that is
    not equal to the master key — otherwise a leak of the transfer
    secret discloses the at-rest AES-GCM secret."""
    master = "master-key-with-256-bits-of-entropy-deadbeef"
    derived = settings._derive_transfer_token_secret(master)
    assert derived != master
    # And the derivation is one-way: hex output can't simply embed the
    # input by accident.
    assert master not in derived
    assert master.encode("utf-8").hex() not in derived


def test_derive_transfer_token_secret_returns_hex():
    """The derived secret is hex-encoded for greppability in logs/process
    listings if it ever leaks. 32-byte output → 64 hex chars."""
    derived = settings._derive_transfer_token_secret("any-master-key")
    assert len(derived) == 64
    assert all(c in "0123456789abcdef" for c in derived)


def test_derive_transfer_token_secret_includes_versioned_info():
    """The info string includes ``v1`` so a future scheme rotation
    (e.g. switching to a different hash) cleanly invalidates all
    existing tokens by bumping the version tag. Pin the constant so a
    silent edit is caught by this test."""
    assert settings._TRANSFER_TOKEN_SECRET_HKDF_INFO == (
        b"memory:transfer-token-secret:v1"
    )


def test_derive_transfer_token_secret_uses_secrets_encryption_salt():
    """The HKDF derivation salts with SECRETS_ENCRYPTION_SALT, so two
    deployments with the same SECRETS_ENCRYPTION_KEY but different
    salts produce different transfer secrets. Standard cross-deployment
    isolation property of the rest of the secrets infrastructure."""
    # Re-run the derivation with a monkey-patched salt and check the
    # output changes. We do this through the real function rather than
    # re-implementing HKDF here, so the test would catch a future edit
    # that drops the salt.
    original_salt = settings.SECRETS_ENCRYPTION_SALT
    try:
        derived_a = settings._derive_transfer_token_secret("k")
        settings.SECRETS_ENCRYPTION_SALT = b"different-salt-v1"
        derived_b = settings._derive_transfer_token_secret("k")
    finally:
        settings.SECRETS_ENCRYPTION_SALT = original_salt
    assert derived_a != derived_b
