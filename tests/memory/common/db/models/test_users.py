import pytest
from memory.common.db.models.users import (
    hash_password,
    verify_password,
    User,
    HumanUser,
    BotUser,
    DiscordBotUser,
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


def test_create_discord_bot_user(db_session):
    """Test creating a DiscordBotUser"""
    from memory.common.db.models import DiscordUser

    # Create a Discord user for the bot
    discord_user = DiscordUser(
        id=123456789,
        username="botuser",
    )
    db_session.add(discord_user)
    db_session.commit()

    user = DiscordBotUser.create_with_api_key(
        discord_users=[discord_user],
        name="Discord Bot",
        email="discordbot@example.com",
        api_key="discord_key_123",
    )
    db_session.add(user)
    db_session.commit()

    assert user.id is not None
    assert user.email == "discordbot@example.com"
    assert user.name == "Discord Bot"
    assert user.user_type == "discord_bot"
    assert user.api_key == "discord_key_123"
    assert len(user.discord_users) == 1


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
