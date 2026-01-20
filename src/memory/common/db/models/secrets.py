"""Secrets storage with encryption at rest.

Provides encrypted storage for sensitive values (API keys, tokens, etc.)
with lookup by symbolic name.
"""

from __future__ import annotations
import base64
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Session, relationship, validates, Mapped, mapped_column

from memory.common import settings
from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User

logger = logging.getLogger(__name__)

# Clojure symbol pattern: starts with letter or special char, followed by
# letters, digits, or special chars. No spaces or most punctuation.
# Special chars allowed: * + ! - _ ' ? < > =
SYMBOL_PATTERN = re.compile(r"^[a-zA-Z*+!\-_'?<>=][a-zA-Z0-9*+!\-_'?<>=]*$")


def validate_symbol_name(name: str) -> bool:
    """Check if name is a valid Clojure-style symbol."""
    return bool(SYMBOL_PATTERN.match(name))


def derive_encryption_key(secret: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a secret string.

    Uses PBKDF2 with SHA256 and 480,000 iterations (OWASP recommendation).
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))


def get_encryption_key() -> bytes:
    """Get the encryption key for secrets from settings.

    Derives a Fernet-compatible key from SECRETS_ENCRYPTION_KEY.
    """
    secret = settings.SECRETS_ENCRYPTION_KEY
    if not secret:
        raise ValueError(
            "SECRETS_ENCRYPTION_KEY must be set to encrypt secrets. "
            'Generate with: python -c "import secrets; print(secrets.token_hex(32))"'
        )

    return derive_encryption_key(secret, settings.SECRETS_ENCRYPTION_SALT)


def encrypt_value(plaintext: str) -> bytes:
    """Encrypt a secret value for storage."""
    key = get_encryption_key()
    f = Fernet(key)
    return f.encrypt(plaintext.encode())


def decrypt_value(ciphertext: bytes) -> str:
    """Decrypt a secret value from storage."""
    key = get_encryption_key()
    f = Fernet(key)
    return f.decrypt(ciphertext).decode()


def encrypt_value_with_key(plaintext: str, key: bytes) -> bytes:
    """Encrypt a secret value with a specific key."""
    f = Fernet(key)
    return f.encrypt(plaintext.encode())


def decrypt_value_with_key(ciphertext: bytes, key: bytes) -> str:
    """Decrypt a secret value with a specific key."""
    f = Fernet(key)
    return f.decrypt(ciphertext).decode()


class Secret(Base):
    """Encrypted secret storage.

    Secrets are identified by a symbolic name (Clojure-style identifier)
    and stored encrypted at rest using Fernet symmetric encryption.
    Each secret belongs to a specific user.
    """

    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="unique_secret_per_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user: Mapped[User] = relationship("User", back_populates="secrets")

    @validates("name")
    def validate_name(self, _key: str, name: str) -> str:
        if not validate_symbol_name(name):
            raise ValueError(
                f"Secret name must be a valid symbol: start with letter or "
                f"special char (*+!-_'?<>=), followed by letters, digits, or "
                f"special chars. Got: {name!r}"
            )
        return name

    @property
    def value(self) -> str:
        """Decrypt and return the secret value."""
        return decrypt_value(self.encrypted_value)

    @value.setter
    def value(self, plaintext: str) -> None:
        """Encrypt and store the secret value."""
        self.encrypted_value = encrypt_value(plaintext)
        self.updated_at = datetime.now(timezone.utc)

    def serialize(self) -> dict:
        """Serialize without exposing the secret value."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def find_secret(session: Session, user_id: int, name: str) -> Secret | None:
    """Find a secret by its symbolic name for a specific user."""
    return (
        session.query(Secret)
        .filter(Secret.user_id == user_id, Secret.name == name)
        .first()
    )


def extract(session: Session, user_id: int, name: str) -> str:
    """Extract a secret by name, falling back to the name itself if not found.

    This allows using either literal values or secret references interchangeably:
    - If `name` matches a stored secret for this user, returns the decrypted value
    - Otherwise, returns `name` unchanged (treating it as a literal value)

    Args:
        session: Database session
        user_id: Owner user ID
        name: Secret name to look up, or a literal value

    Returns:
        The decrypted secret value, or the original name if no secret found
    """
    secret = find_secret(session, user_id, name)
    if secret is None:
        logger.debug(f"No secret found for '{name}', using as literal value")
        return name
    return secret.value


def create_secret(
    session: Session,
    user_id: int,
    name: str,
    value: str,
    description: str | None = None,
) -> Secret:
    """Create a new secret for a user.

    Args:
        session: Database session
        user_id: Owner user ID
        name: Symbolic name (must be valid Clojure-style identifier)
        value: The secret value (will be encrypted)
        description: Optional description

    Returns:
        The created Secret object
    """
    secret = Secret(
        user_id=user_id,
        name=name,
        encrypted_value=encrypt_value(value),
        description=description,
    )
    session.add(secret)
    return secret


def update_secret(session: Session, user_id: int, name: str, value: str) -> Secret | None:
    """Update an existing secret's value.

    Args:
        session: Database session
        user_id: Owner user ID
        name: Secret name
        value: New value (will be encrypted)

    Returns:
        The updated Secret, or None if not found
    """
    secret = find_secret(session, user_id, name)
    if secret is None:
        return None
    secret.value = value
    return secret


def delete_secret(session: Session, user_id: int, name: str) -> bool:
    """Delete a secret by name for a specific user.

    Args:
        session: Database session
        user_id: Owner user ID
        name: Secret name

    Returns:
        True if deleted, False if not found
    """
    secret = find_secret(session, user_id, name)
    if secret is None:
        return False
    session.delete(secret)
    return True


def rotate_all_secrets(
    session: Session,
    old_key_secret: str,
    new_key_secret: str,
) -> int:
    """Re-encrypt all secrets with a new master key.

    This is used when rotating the SECRETS_ENCRYPTION_KEY. The process:
    1. Derive encryption keys from both old and new secrets
    2. Decrypt each secret with the old key
    3. Re-encrypt with the new key
    4. Update the stored value

    After running this, update SECRETS_ENCRYPTION_KEY in your environment
    to the new value.

    Args:
        session: Database session
        old_key_secret: The current SECRETS_ENCRYPTION_KEY value
        new_key_secret: The new SECRETS_ENCRYPTION_KEY value

    Returns:
        Number of secrets rotated

    Raises:
        InvalidToken: If old_key_secret doesn't match the current encryption
    """
    salt = settings.SECRETS_ENCRYPTION_SALT
    old_key = derive_encryption_key(old_key_secret, salt)
    new_key = derive_encryption_key(new_key_secret, salt)

    secrets = session.query(Secret).all()
    count = 0

    for secret in secrets:
        # Decrypt with old key
        plaintext = decrypt_value_with_key(secret.encrypted_value, old_key)
        # Re-encrypt with new key
        secret.encrypted_value = encrypt_value_with_key(plaintext, new_key)
        secret.updated_at = datetime.now(timezone.utc)
        count += 1

    return count


def list_secrets(session: Session, user_id: int) -> list[dict]:
    """List all secrets for a user (metadata only, no values)."""
    secrets = session.query(Secret).filter(Secret.user_id == user_id).order_by(Secret.name).all()
    return [s.serialize() for s in secrets]
