"""Secrets storage with encryption at rest.

Provides encrypted storage for sensitive values (API keys, tokens, etc.)
with lookup by symbolic name.
"""

from __future__ import annotations
import base64
import functools
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
    update,
)
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


@functools.lru_cache(maxsize=4)
def derive_encryption_key(secret: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a secret string.

    Uses PBKDF2 with SHA256 and 480,000 iterations (OWASP recommendation).
    Cached: every encrypt/decrypt derives the key, and PBKDF2 is deliberately slow.
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
        # Do NOT interpolate `name` into the log message: per this
        # function's contract, `name` may be a literal secret value
        # (e.g. a GitHub PAT passed directly through cloud_claude's
        # GITHUB_TOKEN / GITHUB_TOKEN_WRITE plumbing). Logging it at any
        # level risks leaking plaintext credentials to log aggregation
        # or error trackers if DEBUG is ever enabled.
        logger.debug("No secret found for given name, using as literal value")
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


def is_encrypted_column(column: Column) -> bool:
    """Whether a column holds Fernet ciphertext produced by encrypt_value."""
    return isinstance(column.type, LargeBinary) and (
        column.name.endswith("_encrypted") or column.name == "encrypted_value"
    )


def encrypted_columns(metadata: MetaData = Base.metadata) -> dict[Table, list[Column]]:
    """Every encrypted column in `metadata`: the models', or a database's as reflected."""
    found = {
        table: [c for c in table.columns if is_encrypted_column(c)]
        for table in metadata.sorted_tables
    }
    return {table: columns for table, columns in found.items() if columns}


def reencrypted(token: bytes, target: Fernet, sources: MultiFernet) -> bytes | None:
    """`token` encrypted under `target`, or None when it already is."""
    try:
        target.decrypt(token)
    except InvalidToken:
        return target.encrypt(sources.decrypt(token))
    return None


def rekey_all_secrets(session: Session, target: str, candidates: Iterable[str]) -> int:
    """Re-encrypt every stored encrypted value under `target`, each from
    whichever of `candidates` decrypts it.

    Used to rotate SECRETS_ENCRYPTION_KEY, and after loading a dump taken
    before a rotation. The columns come from the database, not the models:
    after a restore or a rollback its schema can be older or newer than this code.

    Returns:
        Number of values re-encrypted

    Raises:
        InvalidToken: If neither `target` nor any candidate decrypts some
            value; the caller must roll back
    """
    salt = settings.SECRETS_ENCRYPTION_SALT
    to = Fernet(derive_encryption_key(target, salt))
    sources = MultiFernet([to, *(Fernet(derive_encryption_key(k, salt)) for k in candidates)])
    live = MetaData()
    live.reflect(bind=session.connection())

    count = 0
    for table, columns in encrypted_columns(live).items():
        pk = list(table.primary_key.columns)
        for row in session.execute(select(*pk, *columns)).mappings().all():
            values = {
                column: value
                for column in columns
                if row[column] is not None
                and (value := reencrypted(bytes(row[column]), to, sources)) is not None
            }
            if not values:
                continue
            session.execute(
                update(table)
                .where(*(column == row[column] for column in pk))
                .values(values)
            )
            count += len(values)

    return count
