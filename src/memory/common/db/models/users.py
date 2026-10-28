from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import bcrypt
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql import func

from memory.common.db.models.agents import money
from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.discord import DiscordBot
    from memory.common.db.models.sources import Person
    from memory.common.db.models.slack import SlackUserCredentials


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with salt.

    Returns a hash in the format: bcrypt2:$2b$12$...
    The prefix allows us to identify the hashing algorithm.
    """
    # Generate bcrypt hash (automatically includes salt)
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return f"{hashed.decode('utf-8')}"


def check_timezone(name: str) -> str:
    """Return ``name`` if it is an IANA timezone, else raise ValueError."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown timezone: {name!r}") from None
    return name


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash.

    Returns:
        bool: True if password is correct
    """
    # Check for bcrypt format
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    user_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # Discriminator column

    # Make these nullable since subclasses will use them selectively
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # MCP tool scopes - controls which tools this user can access
    # Example: ["read", "observe", "github"] or ["*"] for full access
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=["read"]
    )
    # IANA name; cron schedules owned by this user run on its wall clock
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="UTC", server_default="UTC"
    )

    default_ssh_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ssh_keys.id", ondelete="SET NULL", use_alter=True, name="users_default_ssh_key_fk"), nullable=True
    )

    @validates("timezone")
    def validate_timezone(self, key: str, value: str) -> str:
        return check_timezone(value)

    # Relationship to sessions
    sessions: Mapped[list[UserSession]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )
    oauth_states: Mapped[list[OAuthState]] = relationship(
        "OAuthState", back_populates="user", cascade="all, delete-orphan"
    )
    oauth_refresh_tokens: Mapped[list[OAuthRefreshToken]] = relationship(
        "OAuthRefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list[APIKey]] = relationship(
        "APIKey", back_populates="user", cascade="all, delete-orphan"
    )

    # Discord relationships
    discord_bots: Mapped[list[DiscordBot]] = relationship(
        "DiscordBot",
        back_populates="owner",
    )

    # Slack relationships (per-user OAuth credentials)
    slack_credentials: Mapped[list[SlackUserCredentials]] = relationship(
        "SlackUserCredentials", back_populates="user", cascade="all, delete-orphan"
    )

    # Link to Person record (for rich contact info about this user)
    person: Mapped["Person | None"] = relationship(
        "Person", back_populates="user", foreign_keys="Person.user_id"
    )

    __mapper_args__: dict[str, Any] = {
        "polymorphic_on": user_type,
        "polymorphic_identity": "user",
    }

    def serialize(self) -> dict[str, Any]:
        return {
            "user_id": self.id,
            "name": self.name,
            "email": self.email,
            "user_type": self.user_type,
            "scopes": self.scopes or [],
            "timezone": self.timezone,
            "discord_accounts": {
                # str keys: Discord ids are int snowflakes, but JSON object keys
                # are always strings — coerce here so in-process consumers see the
                # same shape clients get after the JSON-RPC boundary.
                str(account.id): account.username
                for account in (self.person.discord_accounts if self.person else [])
            },
            "discord_bots": [bot.id for bot in self.discord_bots],
        }


class HumanUser(User):
    """Human user with password authentication"""

    __mapper_args__: dict[str, Any] = {
        "polymorphic_identity": "human",
    }

    def is_valid_password(self, password: str) -> bool:
        """Check if the provided password is valid for this user.

        Automatically upgrades legacy SHA-256 hashes to bcrypt on successful login.
        """
        if self.password_hash is None:
            return False
        return verify_password(password, self.password_hash)

    @classmethod
    def create_with_password(cls, email: str, name: str, password: str) -> HumanUser:
        """Create a new human user with a hashed password"""
        return cls(
            email=email,
            name=name,
            password_hash=hash_password(password),
            user_type="human",
        )


class BotUser(User):
    """Bot user with API key authentication"""

    __mapper_args__: dict[str, Any] = {
        "polymorphic_identity": "bot",
    }


class APIKeyType(str):
    """API key type constants for categorization.

    Implemented as str subclass (not enum.StrEnum) to allow storing
    arbitrary string values in the database for forward compatibility.
    Use the class constants for type-safe access to standard key types.

    Standard key types:
        INTERNAL: General-purpose internal API access
        DISCORD: Discord bot integration
        GOOGLE: Google services integration
        GITHUB: GitHub integration
        MCP: MCP server access
    """

    INTERNAL = "internal"
    DISCORD = "discord"
    GOOGLE = "google"
    GITHUB = "github"
    MCP = "mcp"

    # Tuple of all standard key types for validation
    ALL_TYPES = (INTERNAL, DISCORD, GOOGLE, GITHUB, MCP)


# "bot" and "user" prefixed keys made before key types existed.
KNOWN_KEY_PREFIXES = (*APIKeyType.ALL_TYPES, "bot", "user")


def hash_api_key(key: str) -> str:
    """The stored lookup hash of an API key.

    Keys are 256-bit random tokens, so an unsalted SHA-256 can't be brute-forced
    and needs no server secret: rotating secrets or restoring a backup elsewhere
    leaves keys working.
    """
    return hashlib.sha256(key.encode()).hexdigest()


def api_key_preview(key: str) -> str:
    """The key's type prefix and last four characters, for telling keys apart.

    Only a known prefix is kept: a key chosen by hand may have any text before its first "_".
    """
    prefix = key.split("_", 1)[0]
    if "_" in key and prefix in KNOWN_KEY_PREFIXES:
        return f"{prefix}_...{key[-4:]}"
    return f"...{key[-4:]}"


class APIKey(Base):
    """API key for authenticating users and services.

    Supports multiple keys per user with different types and expiration. Only a
    hash of the key is stored; the plaintext is readable as ``key`` on the
    instance that minted it, and nowhere afterwards.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    key_preview: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    key_type: Mapped[str] = mapped_column(
        String, nullable=False, default=APIKeyType.INTERNAL
    )
    # Scopes override - if set, these scopes are used instead of the user's scopes.
    # If None, authentication falls back to the user's default scopes.
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agent_max_run_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    agent_max_daily_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )

    # Relationship to user
    user: Mapped[User] = relationship("User", back_populates="api_keys")

    @property
    def key(self) -> str:
        if (plaintext := vars(self).get("minted_key")) is None:
            raise AttributeError("API keys are stored hashed; the plaintext exists only at creation")
        return plaintext

    @key.setter
    def key(self, plaintext: str) -> None:
        vars(self)["minted_key"] = plaintext
        self.key_hash = hash_api_key(plaintext)
        self.key_preview = api_key_preview(plaintext)

    @classmethod
    def generate_key(cls, prefix: str = "key") -> str:
        """Generate a new API key with the given prefix."""
        return f"{prefix}_{secrets.token_hex(32)}"

    @classmethod
    def create(
        cls,
        user_id: int,
        key_type: str = APIKeyType.INTERNAL,
        name: str | None = None,
        scopes: list[str] | None = None,
        expires_at: datetime | None = None,
        prefix: str | None = None,
    ) -> "APIKey":
        """Create a new API key for a user."""
        if prefix is None:
            prefix = key_type
        return cls(
            user_id=user_id,
            key=cls.generate_key(prefix),
            name=name,
            key_type=key_type,
            scopes=scopes,
            expires_at=expires_at,
        )

    def is_valid(self) -> bool:
        """Check if the key is valid (a known type, not revoked, not expired)."""
        # A row of a removed type (e.g. a one-time key restored from a backup) must not authenticate.
        if self.revoked or self.key_type not in APIKeyType.ALL_TYPES:
            return False
        if self.expires_at:
            now = datetime.now(timezone.utc)
            # Handle both tz-aware and tz-naive datetimes
            expires = self.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < now:
                return False
        return True

    def serialize(self) -> dict[str, Any]:
        """Serialize the API key for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "key_type": self.key_type,
            "scopes": self.scopes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
            "revoked": self.revoked,
            "key_preview": self.key_preview,
            "agent_max_run_cost_usd": money(self.agent_max_run_cost_usd),
            "agent_max_daily_cost_usd": money(self.agent_max_daily_cost_usd),
        }


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    oauth_state_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("oauth_states.id"), nullable=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationship to user
    user: Mapped[User] = relationship("User", back_populates="sessions")
    oauth_state: Mapped[OAuthState | None] = relationship(
        "OAuthState", back_populates="session"
    )


class OAuthClientInformation(Base):
    __tablename__ = "oauth_client"

    client_id: Mapped[str] = mapped_column(String, primary_key=True)
    client_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    client_id_issued_at: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    client_secret_expires_at: Mapped[Decimal | None] = mapped_column(
        Numeric, nullable=True
    )

    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    token_endpoint_auth_method: Mapped[str] = mapped_column(String, nullable=False)
    grant_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    response_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    client_name: Mapped[str] = mapped_column(String, nullable=False)
    client_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    logo_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    contacts: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    tos_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    jwks_uri: Mapped[str | None] = mapped_column(String, nullable=True)

    sessions: Mapped[list[OAuthState]] = relationship(
        "OAuthState", back_populates="client", cascade="all, delete-orphan"
    )

    def serialize(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "client_id_issued_at": self.client_id_issued_at,
            "client_secret_expires_at": self.client_secret_expires_at,
            "redirect_uris": self.redirect_uris,
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "grant_types": self.grant_types,
            "response_types": self.response_types,
            "scope": self.scope,
            "client_name": self.client_name,
            "client_uri": self.client_uri,
            "logo_uri": self.logo_uri,
            "contacts": self.contacts,
            "tos_uri": self.tos_uri,
            "policy_uri": self.policy_uri,
            "jwks_uri": self.jwks_uri,
        }


class OAuthToken:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String, ForeignKey("oauth_client.client_id"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def serialize(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "scopes": self.scopes,
            "expires_at": self.expires_at.timestamp(),
        }


class OAuthState(Base, OAuthToken):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    redirect_uri: Mapped[str] = mapped_column(String, nullable=False)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    code_challenge: Mapped[str | None] = mapped_column(String, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def serialize(self, code: bool = False) -> dict[str, Any]:
        data = {
            "redirect_uri": self.redirect_uri,
            "redirect_uri_provided_explicitly": self.redirect_uri_provided_explicitly,
            "code_challenge": self.code_challenge,
        } | super().serialize()
        if code:
            data |= {
                "code": self.code,
                "expires_at": self.expires_at.timestamp(),
            }
        return data

    client: Mapped[OAuthClientInformation] = relationship(
        "OAuthClientInformation", back_populates="sessions"
    )
    session: Mapped[UserSession | None] = relationship(
        "UserSession", back_populates="oauth_state", uselist=False
    )
    user: Mapped[User | None] = relationship("User", back_populates="oauth_states")


class OAuthRefreshToken(Base, OAuthToken):
    __tablename__ = "oauth_refresh_tokens"

    token: Mapped[str] = mapped_column(
        String, nullable=False, default=lambda: f"rt_{secrets.token_hex(32)}"
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Optional: link to the access token session that was created with this refresh token
    access_token_session_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("user_sessions.id"), nullable=True
    )

    # Relationships
    client: Mapped[OAuthClientInformation] = relationship("OAuthClientInformation")
    user: Mapped[User | None] = relationship("User", back_populates="oauth_refresh_tokens")
    access_token_session: Mapped[UserSession | None] = relationship("UserSession")

    def serialize(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "expires_at": self.expires_at.timestamp(),
            "revoked": self.revoked,
        } | super().serialize()


class OAuthClientState(Base):
    """Temporary storage for OAuth client flow state tokens (CSRF protection).

    Used when Memory acts as an OAuth client connecting to external services
    (Slack, Google, etc.). This is distinct from OAuthState which is for
    Memory acting as an OAuth provider.

    States are created when initiating OAuth flow and consumed (deleted)
    upon callback. They typically expire after 10 minutes if not used.
    """

    __tablename__ = "oauth_client_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)  # "slack", "google", etc.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
