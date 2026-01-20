"""Tests for the ApiKey model and related functionality."""

from datetime import datetime, timedelta, timezone

from memory.common.db.models.users import (
    ApiKey,
    ApiKeyType,
    hash_api_key,
    generate_api_key,
    find_api_key_by_hash,
    authenticate_with_api_key,
    HumanUser,
)


# --- Test hash_api_key ---


def test_hash_api_key_produces_consistent_hash():
    """Test that hashing the same key twice produces the same hash."""
    key = "key_abc123def456"
    hash1 = hash_api_key(key)
    hash2 = hash_api_key(key)

    assert hash1 == hash2


def test_hash_api_key_produces_different_hashes_for_different_keys():
    """Test that different keys produce different hashes."""
    key1 = "key_abc123"
    key2 = "key_def456"

    hash1 = hash_api_key(key1)
    hash2 = hash_api_key(key2)

    assert hash1 != hash2


def test_hash_api_key_returns_64_char_hex():
    """Test that hash is a 64-character hex string (SHA-256)."""
    key = "key_test"
    key_hash = hash_api_key(key)

    assert len(key_hash) == 64
    assert all(c in "0123456789abcdef" for c in key_hash)


# --- Test generate_api_key ---


def test_generate_api_key_returns_key_and_hash():
    """Test that generate_api_key returns both plaintext key and hash."""
    key, key_hash = generate_api_key()

    assert key is not None
    assert key_hash is not None
    assert key != key_hash


def test_generate_api_key_default_prefix():
    """Test that generate_api_key uses 'key' as default prefix."""
    key, _ = generate_api_key()

    assert key.startswith("key_")


def test_generate_api_key_custom_prefix():
    """Test that generate_api_key uses custom prefix."""
    key, _ = generate_api_key(prefix="custom")

    assert key.startswith("custom_")


def test_generate_api_key_hash_matches():
    """Test that the returned hash matches hashing the returned key."""
    key, key_hash = generate_api_key()

    assert hash_api_key(key) == key_hash


def test_generate_api_key_produces_unique_keys():
    """Test that each call produces a different key."""
    keys = [generate_api_key()[0] for _ in range(10)]

    assert len(set(keys)) == 10


# --- Test ApiKey model ---


def test_create_api_key_returns_key_and_model(db_session):
    """Test that ApiKey.create returns both the model and plaintext key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id)

    assert api_key is not None
    assert plaintext_key is not None
    assert plaintext_key.startswith("key_")


def test_create_api_key_stores_hash_not_plaintext(db_session):
    """Test that ApiKey stores hash, not plaintext."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id)
    db_session.add(api_key)
    db_session.commit()

    # The stored hash should not equal the plaintext key
    assert api_key.key_hash != plaintext_key
    # But hashing the plaintext key should match the stored hash
    assert hash_api_key(plaintext_key) == api_key.key_hash


def test_create_api_key_with_all_options(db_session):
    """Test creating an API key with all options specified."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    api_key, plaintext_key = ApiKey.create(
        user_id=user.id,
        key_type=ApiKeyType.MCP,
        name="My MCP Key",
        scopes=["read", "observe"],
        expires_at=expires_at,
        is_one_time=True,
        prefix="mcp",
    )
    db_session.add(api_key)
    db_session.commit()

    assert plaintext_key.startswith("mcp_")
    assert api_key.key_type == ApiKeyType.MCP
    assert api_key.name == "My MCP Key"
    assert api_key.scopes == ["read", "observe"]
    assert api_key.expires_at == expires_at
    assert api_key.is_one_time is True


def test_api_key_is_valid_active_key(db_session):
    """Test that is_valid returns True for active, non-expired key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(user_id=user.id)
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is True


def test_api_key_is_valid_inactive_key(db_session):
    """Test that is_valid returns False for inactive key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(user_id=user.id)
    api_key.is_active = False
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is False


def test_api_key_is_valid_expired_key(db_session):
    """Test that is_valid returns False for expired key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is False


def test_api_key_get_effective_scopes_from_key(db_session):
    """Test that get_effective_scopes returns key-specific scopes when set."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    user.scopes = ["read", "observe", "write"]
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(
        user_id=user.id,
        scopes=["read"],  # Key has narrower scopes
    )
    api_key.user = user
    db_session.add(api_key)
    db_session.commit()

    assert api_key.get_effective_scopes() == ["read"]


def test_api_key_get_effective_scopes_from_user(db_session):
    """Test that get_effective_scopes returns user scopes when key scopes are None."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    user.scopes = ["read", "observe"]
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(
        user_id=user.id,
        scopes=None,  # Inherit from user
    )
    api_key.user = user
    db_session.add(api_key)
    db_session.commit()

    assert api_key.get_effective_scopes() == ["read", "observe"]


def test_api_key_mark_used_updates_timestamp(db_session):
    """Test that mark_used updates last_used_at and use_count."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(user_id=user.id)
    db_session.add(api_key)
    db_session.commit()

    assert api_key.use_count == 0
    assert api_key.last_used_at is None

    api_key.mark_used()

    assert api_key.use_count == 1
    assert api_key.last_used_at is not None


def test_api_key_mark_used_deactivates_one_time_key(db_session):
    """Test that mark_used deactivates one-time keys."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(user_id=user.id, is_one_time=True)
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_active is True

    api_key.mark_used()

    assert api_key.is_active is False


def test_api_key_serialize_excludes_sensitive_data(db_session):
    """Test that serialize doesn't expose the key hash."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, _ = ApiKey.create(user_id=user.id, name="Test Key")
    db_session.add(api_key)
    db_session.commit()

    serialized = api_key.serialize()

    assert "key_hash" not in serialized
    assert "key_prefix" in serialized
    assert serialized["name"] == "Test Key"


# --- Test find_api_key_by_hash ---


def test_find_api_key_by_hash_finds_existing_key(db_session):
    """Test that find_api_key_by_hash finds a key by its hash."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id)
    db_session.add(api_key)
    db_session.commit()

    found = find_api_key_by_hash(db_session, hash_api_key(plaintext_key))

    assert found is not None
    assert found.id == api_key.id


def test_find_api_key_by_hash_returns_none_for_unknown_hash(db_session):
    """Test that find_api_key_by_hash returns None for unknown hash."""
    found = find_api_key_by_hash(db_session, "nonexistent_hash")

    assert found is None


# --- Test authenticate_with_api_key ---


def test_authenticate_with_api_key_success(db_session):
    """Test successful authentication with a valid API key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id)
    api_key.user = user  # Set up relationship
    db_session.add(api_key)
    db_session.commit()

    authenticated_user, authenticated_key = authenticate_with_api_key(
        db_session, plaintext_key
    )

    assert authenticated_user is not None
    assert authenticated_user.id == user.id
    assert authenticated_key is not None
    assert authenticated_key.id == api_key.id


def test_authenticate_with_api_key_invalid_key(db_session):
    """Test that authentication fails with an invalid key."""
    user, key = authenticate_with_api_key(db_session, "key_invalid_key")

    assert user is None
    assert key is None


def test_authenticate_with_api_key_inactive_key(db_session):
    """Test that authentication fails with an inactive key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id)
    api_key.is_active = False
    db_session.add(api_key)
    db_session.commit()

    authenticated_user, authenticated_key = authenticate_with_api_key(
        db_session, plaintext_key
    )

    assert authenticated_user is None
    assert authenticated_key is None


def test_authenticate_with_api_key_expired_key(db_session):
    """Test that authentication fails with an expired key."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(api_key)
    db_session.commit()

    authenticated_user, authenticated_key = authenticate_with_api_key(
        db_session, plaintext_key
    )

    assert authenticated_user is None
    assert authenticated_key is None


def test_authenticate_with_api_key_marks_as_used(db_session):
    """Test that successful authentication marks the key as used."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id)
    api_key.user = user
    db_session.add(api_key)
    db_session.commit()

    initial_use_count = api_key.use_count

    authenticate_with_api_key(db_session, plaintext_key)

    assert api_key.use_count == initial_use_count + 1
    assert api_key.last_used_at is not None


def test_authenticate_with_api_key_one_time_key_deactivated_after_use(db_session):
    """Test that one-time keys are deactivated after successful authentication."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key, plaintext_key = ApiKey.create(user_id=user.id, is_one_time=True)
    api_key.user = user
    db_session.add(api_key)
    db_session.commit()

    # First authentication should succeed
    authenticated_user, _ = authenticate_with_api_key(db_session, plaintext_key)
    assert authenticated_user is not None
    assert api_key.is_active is False

    # Second authentication should fail
    authenticated_user, _ = authenticate_with_api_key(db_session, plaintext_key)
    assert authenticated_user is None


# --- Test multiple keys per user ---


def test_user_can_have_multiple_api_keys(db_session):
    """Test that a user can have multiple API keys."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    keys = []
    for i in range(3):
        api_key, plaintext_key = ApiKey.create(
            user_id=user.id, name=f"Key {i}", key_type=ApiKeyType.INTERNAL
        )
        api_key.user = user
        db_session.add(api_key)
        keys.append((api_key, plaintext_key))

    db_session.commit()

    # All keys should work for authentication
    for api_key, plaintext_key in keys:
        authenticated_user, _ = authenticate_with_api_key(db_session, plaintext_key)
        assert authenticated_user is not None
        assert authenticated_user.id == user.id


def test_different_key_types_can_coexist(db_session):
    """Test that a user can have keys of different types."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    mcp_key, mcp_plaintext = ApiKey.create(
        user_id=user.id, key_type=ApiKeyType.MCP, name="MCP Key"
    )
    discord_key, discord_plaintext = ApiKey.create(
        user_id=user.id, key_type=ApiKeyType.DISCORD, name="Discord Key"
    )

    mcp_key.user = user
    discord_key.user = user
    db_session.add_all([mcp_key, discord_key])
    db_session.commit()

    # Both keys should authenticate
    user1, key1 = authenticate_with_api_key(db_session, mcp_plaintext)
    user2, key2 = authenticate_with_api_key(db_session, discord_plaintext)

    assert user1.id == user2.id == user.id
    assert key1.key_type == ApiKeyType.MCP
    assert key2.key_type == ApiKeyType.DISCORD
