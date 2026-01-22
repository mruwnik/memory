from datetime import datetime, timedelta, timezone

import pytest
from memory.common.db.models.users import (
    hash_password,
    verify_password,
    HumanUser,
    BotUser,
    APIKey,
    APIKeyType,
)


@pytest.mark.parametrize(
    "password",
    [
        "simple_password",
        "complex_P@ssw0rd!",
        "very_long_password_with_many_characters_1234567890",
        "",
        "unicode_password_тест_😀",
        "password with spaces",
    ],
)
def test_hash_password_format(password):
    """Test that hash_password returns correctly formatted bcrypt hash"""
    result = hash_password(password)

    # bcrypt format: $2b$cost$salthash (60 characters total)
    assert result.startswith("$2b$")
    assert len(result) == 60

    # Verify the hash can be used for verification
    assert verify_password(password, result)


def test_hash_password_uniqueness():
    """Test that same password generates different hashes due to random salt"""
    password = "test_password"
    hash1 = hash_password(password)
    hash2 = hash_password(password)

    # Different salts should produce different hashes
    assert hash1 != hash2

    # But both should verify correctly
    assert verify_password(password, hash1)
    assert verify_password(password, hash2)


@pytest.mark.parametrize(
    "password,expected",
    [
        ("correct_password", True),
        ("wrong_password", False),
        ("", False),
        ("CORRECT_PASSWORD", False),  # Case sensitive
    ],
)
def test_verify_password_correctness(password, expected):
    """Test password verification with correct and incorrect passwords"""
    correct_password = "correct_password"
    password_hash = hash_password(correct_password)

    result = verify_password(password, password_hash)
    assert result == expected


@pytest.mark.parametrize(
    "malformed_hash",
    [
        "invalid_format",
        "no_colon_here",
        ":empty_salt",
        "salt:",  # Empty hash
        "",
        "too:many:colons:here",
        "salt:invalid_hex_zzz",
        "salt:too_short_hash",
    ],
)
def test_verify_password_malformed_hash(malformed_hash):
    """Test that verify_password handles malformed hashes gracefully"""
    result = verify_password("any_password", malformed_hash)
    assert result is False


@pytest.mark.parametrize(
    "test_password",
    [
        "simple",
        "complex_P@ssw0rd!123",
        "",
        "unicode_тест_😀",
        "password with spaces and symbols !@#$%^&*()",
    ],
)
def test_hash_verify_roundtrip(test_password):
    """Test that hash and verify work correctly together"""
    password_hash = hash_password(test_password)

    # Correct password should verify
    assert verify_password(test_password, password_hash)

    # Wrong password should not verify
    assert not verify_password(test_password + "_wrong", password_hash)


# Test User Model Hierarchy


def test_create_human_user(db_session):
    """Test creating a HumanUser with password"""
    user = HumanUser.create_with_password(
        email="human@example.com", name="Human User", password="test_password123"
    )
    db_session.add(user)
    db_session.commit()

    assert user.id is not None
    assert user.email == "human@example.com"
    assert user.name == "Human User"
    assert user.user_type == "human"
    assert user.password_hash is not None
    assert user.api_key is None
    assert user.is_valid_password("test_password123")
    assert not user.is_valid_password("wrong_password")


def test_create_bot_user(db_session):
    """Test creating a BotUser with API key"""
    user = BotUser.create_with_api_key(
        name="Test Bot", email="bot@example.com", api_key="test_api_key_123"
    )
    db_session.add(user)
    db_session.commit()

    assert user.id is not None
    assert user.email == "bot@example.com"
    assert user.name == "Test Bot"
    assert user.user_type == "bot"
    assert user.api_key == "test_api_key_123"
    assert user.password_hash is None


def test_create_bot_user_auto_api_key(db_session):
    """Test creating a BotUser with auto-generated API key"""
    user = BotUser.create_with_api_key(name="Auto Bot", email="autobot@example.com")
    db_session.add(user)
    db_session.commit()

    assert user.id is not None
    assert user.api_key is not None
    assert user.api_key.startswith("bot_")
    assert len(user.api_key) == 68  # "bot_" + 32 bytes hex encoded (64 chars)


def test_user_serialization_human(db_session):
    """Test HumanUser serialization"""
    user = HumanUser.create_with_password(
        email="serialize@example.com", name="Serialize User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    serialized = user.serialize()
    assert serialized["user_id"] == user.id
    assert serialized["name"] == "Serialize User"
    assert serialized["email"] == "serialize@example.com"
    assert serialized["user_type"] == "human"
    assert "password_hash" not in serialized  # Should not expose password hash


def test_user_serialization_bot(db_session):
    """Test BotUser serialization"""
    user = BotUser.create_with_api_key(name="Bot", email="bot@example.com")
    db_session.add(user)
    db_session.commit()

    serialized = user.serialize()
    assert serialized["user_id"] == user.id
    assert serialized["name"] == "Bot"
    assert serialized["email"] == "bot@example.com"
    assert serialized["user_type"] == "bot"
    assert "api_key" not in serialized  # Should not expose API key


def test_bot_user_api_key_uniqueness(db_session):
    """Test that API keys must be unique"""
    user1 = BotUser.create_with_api_key(
        name="Bot 1", email="bot1@example.com", api_key="same_key"
    )
    user2 = BotUser.create_with_api_key(
        name="Bot 2", email="bot2@example.com", api_key="same_key"
    )
    db_session.add(user1)
    db_session.commit()

    db_session.add(user2)
    with pytest.raises(Exception):  # IntegrityError from unique constraint
        db_session.commit()


def test_human_user_factory_method(db_session):
    """Test that HumanUser factory method sets all required fields"""
    user = HumanUser.create_with_password(
        email="factory@example.com", name="Factory User", password="test123"
    )

    # Factory method should set all required fields
    assert user.email == "factory@example.com"
    assert user.name == "Factory User"
    assert user.password_hash is not None
    assert user.user_type == "human"
    assert user.api_key is None


def test_bot_user_factory_method(db_session):
    """Test that BotUser factory method sets all required fields"""
    user = BotUser.create_with_api_key(
        name="Factory Bot", email="factorybot@example.com", api_key="test_key"
    )

    # Factory method should set all required fields
    assert user.email == "factorybot@example.com"
    assert user.name == "Factory Bot"
    assert user.api_key == "test_key"
    assert user.user_type == "bot"
    assert user.password_hash is None


# --- APIKey Model Tests ---


def test_api_key_generate_key():
    """Test that generate_key creates properly formatted keys."""
    key = APIKey.generate_key("test")
    assert key.startswith("test_")
    assert len(key) == 69  # "test_" (5) + 64 hex chars


def test_api_key_generate_key_different_prefixes():
    """Test key generation with various prefixes."""
    prefixes = ["internal", "discord", "mcp", "ot"]
    for prefix in prefixes:
        key = APIKey.generate_key(prefix)
        assert key.startswith(f"{prefix}_")


def test_api_key_create(db_session):
    """Test creating an APIKey for a user."""
    user = HumanUser.create_with_password(
        email="apikey_user@example.com", name="API Key User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.INTERNAL,
        name="Test Key",
    )
    db_session.add(api_key)
    db_session.commit()

    assert api_key.id is not None
    assert api_key.user_id == user.id
    assert api_key.key.startswith("internal_")
    assert api_key.name == "Test Key"
    assert api_key.key_type == APIKeyType.INTERNAL
    assert api_key.is_one_time is False
    assert api_key.revoked is False


def test_api_key_create_one_time(db_session):
    """Test creating a one-time API key."""
    user = HumanUser.create_with_password(
        email="onetime@example.com", name="One Time User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.ONE_TIME,
        is_one_time=True,
    )
    db_session.add(api_key)
    db_session.commit()

    assert api_key.key.startswith("ot_")
    assert api_key.is_one_time is True
    assert api_key.key_type == APIKeyType.ONE_TIME


def test_api_key_is_valid(db_session):
    """Test API key validity checking."""
    user = HumanUser.create_with_password(
        email="valid@example.com", name="Valid User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(user_id=user.id)
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is True


def test_api_key_is_valid_revoked(db_session):
    """Test that revoked keys are invalid."""
    user = HumanUser.create_with_password(
        email="revoked@example.com", name="Revoked User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(user_id=user.id)
    api_key.revoked = True
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is False


def test_api_key_is_valid_expired(db_session):
    """Test that expired keys are invalid."""
    user = HumanUser.create_with_password(
        email="expired@example.com", name="Expired User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is False


def test_api_key_is_valid_not_expired(db_session):
    """Test that non-expired keys are valid."""
    user = HumanUser.create_with_password(
        email="notexpired@example.com", name="Not Expired User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(api_key)
    db_session.commit()

    assert api_key.is_valid() is True


def test_api_key_with_scopes(db_session):
    """Test creating an API key with custom scopes."""
    user = HumanUser.create_with_password(
        email="scopes@example.com", name="Scopes User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(
        user_id=user.id,
        scopes=["read", "write", "admin"],
    )
    db_session.add(api_key)
    db_session.commit()

    assert api_key.scopes == ["read", "write", "admin"]


def test_api_key_serialize(db_session):
    """Test API key serialization."""
    user = HumanUser.create_with_password(
        email="serialize_key@example.com", name="Serialize Key User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(
        user_id=user.id,
        name="Serialized Key",
        key_type=APIKeyType.MCP,
    )
    db_session.add(api_key)
    db_session.commit()

    serialized = api_key.serialize()
    assert serialized["id"] == api_key.id
    assert serialized["name"] == "Serialized Key"
    assert serialized["key_type"] == APIKeyType.MCP
    assert serialized["revoked"] is False
    assert serialized["is_one_time"] is False
    # Key preview should show partial key
    assert serialized["key_preview"].startswith("mcp_")
    assert "..." in serialized["key_preview"]
    # Full key should NOT be in serialized output
    assert "key" not in serialized or serialized.get("key_preview") != api_key.key


def test_api_key_uniqueness(db_session):
    """Test that API keys must be unique."""
    user = HumanUser.create_with_password(
        email="unique@example.com", name="Unique User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    key1 = APIKey(user_id=user.id, key="same_key_value", key_type=APIKeyType.INTERNAL)
    db_session.add(key1)
    db_session.commit()

    key2 = APIKey(user_id=user.id, key="same_key_value", key_type=APIKeyType.INTERNAL)
    db_session.add(key2)
    with pytest.raises(Exception):  # IntegrityError
        db_session.commit()


def test_user_api_keys_relationship(db_session):
    """Test the relationship between User and APIKey."""
    user = HumanUser.create_with_password(
        email="relationship@example.com", name="Relationship User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    key1 = APIKey.create(user_id=user.id, name="Key 1")
    key2 = APIKey.create(user_id=user.id, name="Key 2")
    db_session.add(key1)
    db_session.add(key2)
    db_session.commit()

    # Refresh user to get updated relationship
    db_session.refresh(user)
    assert len(user.api_keys) == 2
    assert key1 in user.api_keys
    assert key2 in user.api_keys


def test_api_key_cascade_delete(db_session):
    """Test that API keys are deleted when user is deleted."""
    user = HumanUser.create_with_password(
        email="cascade@example.com", name="Cascade User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(user_id=user.id, name="Will Be Deleted")
    db_session.add(api_key)
    db_session.commit()
    key_id = api_key.id

    # Delete user
    db_session.delete(user)
    db_session.commit()

    # API key should also be deleted
    assert db_session.get(APIKey, key_id) is None


@pytest.mark.parametrize("key_type", [
    APIKeyType.INTERNAL,
    APIKeyType.DISCORD,
    APIKeyType.GOOGLE,
    APIKeyType.GITHUB,
    APIKeyType.MCP,
    APIKeyType.ONE_TIME,
])
def test_api_key_types(db_session, key_type):
    """Test creating API keys with different types."""
    user = HumanUser.create_with_password(
        email=f"{key_type}@example.com", name=f"{key_type} User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    api_key = APIKey.create(user_id=user.id, key_type=key_type)
    db_session.add(api_key)
    db_session.commit()

    assert api_key.key_type == key_type
