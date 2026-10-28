"""Encryption at rest for stored credentials, keyed by SECRETS_ENCRYPTION_KEY."""

from cryptography.fernet import Fernet

from memory.common import settings
from memory.common.crypto import derive_encryption_key


def get_encryption_key() -> bytes:
    """The Fernet key derived from SECRETS_ENCRYPTION_KEY."""
    secret = settings.SECRETS_ENCRYPTION_KEY
    if not secret:
        raise ValueError(
            "SECRETS_ENCRYPTION_KEY must be set to encrypt secrets. "
            'Generate with: python -c "import secrets; print(secrets.token_hex(32))"'
        )

    return derive_encryption_key(secret, settings.SECRETS_ENCRYPTION_SALT)


def encrypt_value(plaintext: str) -> bytes:
    return Fernet(get_encryption_key()).encrypt(plaintext.encode())


def decrypt_value(ciphertext: bytes) -> str:
    return Fernet(get_encryption_key()).decrypt(ciphertext).decode()
