"""Tests for Cloud Claude Code session management API."""

from unittest.mock import MagicMock, patch

import pytest

from memory.api.cloud_claude import (
    get_user_id_from_session,
    make_session_id,
    user_owns_session,
)
from memory.common.db.models.secrets import decrypt_value, encrypt_value
from memory.common.db.models.users import HumanUser


# Tests for session ID generation and parsing


def test_make_session_id_includes_user_id():
    """Test that session IDs include the user ID prefix."""
    session_id = make_session_id(42)
    assert session_id.startswith("u42-")
    # Should have random hex after the prefix
    random_part = session_id.split("-")[1]
    assert len(random_part) == 12  # 6 bytes = 12 hex chars


def test_make_session_id_unique():
    """Test that session IDs are unique."""
    ids = {make_session_id(1) for _ in range(100)}
    assert len(ids) == 100  # All unique


def test_get_user_id_from_session_valid():
    """Test extracting user ID from valid session IDs."""
    assert get_user_id_from_session("u42-abc123") == 42
    assert get_user_id_from_session("u1-deadbeef") == 1
    assert get_user_id_from_session("u999-xyz") == 999


def test_get_user_id_from_session_invalid():
    """Test that invalid session IDs return None."""
    assert get_user_id_from_session("abc123") is None  # No u prefix
    assert get_user_id_from_session("uabc-123") is None  # Non-numeric user id
    assert get_user_id_from_session("") is None  # Empty
    assert get_user_id_from_session("u") is None  # Just prefix


def test_user_owns_session():
    """Test user ownership check."""
    user = MagicMock()
    user.id = 42

    assert user_owns_session(user, "u42-abc123") is True
    assert user_owns_session(user, "u42-xyz789") is True
    assert user_owns_session(user, "u1-abc123") is False
    assert user_owns_session(user, "invalid") is False


# Tests for SSH key encryption (in users.py, using secrets module)


def test_ssh_key_encryption_roundtrip():
    """Test that SSH keys can be encrypted and decrypted."""
    test_key = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBbeW91cl9rZXlfaGVyZV0AAAAA
-----END OPENSSH PRIVATE KEY-----"""

    with patch(
        "memory.common.settings.SECRETS_ENCRYPTION_KEY",
        "test-secret-key-32-chars-minimum!",
    ):
        encrypted = encrypt_value(test_key)
        decrypted = decrypt_value(encrypted)

    assert decrypted == test_key
    assert encrypted != test_key.encode()


def test_ssh_key_encryption_requires_secret():
    """Test that encryption fails without a secret."""
    with patch("memory.common.settings.SECRETS_ENCRYPTION_KEY", ""):
        with pytest.raises(ValueError) as exc_info:
            encrypt_value("test key")

    assert "SECRETS_ENCRYPTION_KEY must be set" in str(exc_info.value)


def test_ssh_key_user_property(db_session):
    """Test that User.ssh_private_key property encrypts/decrypts."""
    with patch(
        "memory.common.settings.SECRETS_ENCRYPTION_KEY",
        "test-secret-key-32-chars-minimum!",
    ):
        user = HumanUser.create_with_password(
            email="ssh@example.com", name="SSH User", password="test123"
        )
        db_session.add(user)
        db_session.commit()

        # Set private key
        user.ssh_private_key = "test-private-key"
        db_session.commit()

        # Verify it's stored encrypted
        assert user.ssh_private_key_encrypted is not None
        assert user.ssh_private_key_encrypted != b"test-private-key"

        # Verify it decrypts correctly
        assert user.ssh_private_key == "test-private-key"


def test_ssh_key_user_property_none(db_session):
    """Test that None ssh_private_key is handled correctly."""
    with patch(
        "memory.common.settings.SECRETS_ENCRYPTION_KEY",
        "test-secret-key-32-chars-minimum!",
    ):
        user = HumanUser.create_with_password(
            email="nossh@example.com", name="No SSH User", password="test123"
        )
        db_session.add(user)
        db_session.commit()

        # Should be None by default
        assert user.ssh_private_key is None
        assert user.ssh_private_key_encrypted is None

        # Setting to None should work
        user.ssh_private_key = None
        assert user.ssh_private_key_encrypted is None
