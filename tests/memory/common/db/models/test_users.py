import pytest
from memory.common.db.models.users import hash_password, verify_password


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
    """Test that hash_password returns correctly formatted hash"""
    result = hash_password(password)

    # Should be in format "salt:hash"
    assert ":" in result
    parts = result.split(":", 1)
    assert len(parts) == 2

    salt, hash_value = parts
    # Salt should be 32 hex characters (16 bytes * 2)
    assert len(salt) == 32
    assert all(c in "0123456789abcdef" for c in salt)

    # Hash should be 64 hex characters (SHA-256 = 32 bytes * 2)
    assert len(hash_value) == 64
    assert all(c in "0123456789abcdef" for c in hash_value)


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
