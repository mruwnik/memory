"""Tests for memory.common.crypto."""

from memory.common import crypto


_DEFAULT_SALT = b"memory:test:salt:v1"


# --- derive_transfer_token_secret ----------------------------------------
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
    a = crypto.derive_transfer_token_secret("master-key-abc", _DEFAULT_SALT)
    b = crypto.derive_transfer_token_secret("master-key-abc", _DEFAULT_SALT)
    assert a == b


def test_derive_transfer_token_secret_changes_with_input():
    """Different master keys produce different derived secrets. Without
    this, the HKDF would be a no-op and transfer URLs minted under one
    deployment would verify under another."""
    a = crypto.derive_transfer_token_secret("master-key-abc", _DEFAULT_SALT)
    b = crypto.derive_transfer_token_secret("master-key-xyz", _DEFAULT_SALT)
    assert a != b


def test_derive_transfer_token_secret_is_distinct_from_master():
    """The HKDF derivation MUST produce a value that is not equal to the
    master key — otherwise a leak of the transfer secret would directly
    disclose the at-rest AES-GCM secret."""
    master = "master-key-with-256-bits-of-entropy-deadbeef"
    derived = crypto.derive_transfer_token_secret(master, _DEFAULT_SALT)
    assert derived != master
    # And the derivation is one-way: hex output can't simply embed the
    # input by accident.
    assert master not in derived
    assert master.encode("utf-8").hex() not in derived


def test_derive_transfer_token_secret_returns_hex():
    """The derived secret is hex-encoded for greppability in logs/process
    listings if it ever leaks. 32-byte output → 64 hex chars."""
    derived = crypto.derive_transfer_token_secret("any-master-key", _DEFAULT_SALT)
    assert len(derived) == 64
    assert all(c in "0123456789abcdef" for c in derived)


def test_derive_transfer_token_secret_includes_versioned_info():
    """The info string includes ``v1`` so a future scheme rotation
    (e.g. switching to a different hash) cleanly invalidates all
    existing tokens by bumping the version tag. Pin the constant so a
    silent edit is caught by this test."""
    assert crypto.TRANSFER_TOKEN_SECRET_HKDF_INFO == (
        b"memory:transfer-token-secret:v1"
    )


def test_derive_transfer_token_secret_uses_salt():
    """The HKDF derivation salts the master key, so two deployments with
    the same master key but different salts produce different transfer
    secrets. Standard cross-deployment isolation property."""
    derived_a = crypto.derive_transfer_token_secret("k", b"salt-a-v1")
    derived_b = crypto.derive_transfer_token_secret("k", b"salt-b-v1")
    assert derived_a != derived_b
