import secrets
import uuid
from typing import cast

import bcrypt
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Session, relationship
from sqlalchemy.sql import func

from memory.common.db.models.base import Base


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with salt.

    Returns a hash in the format: bcrypt2:$2b$12$...
    The prefix allows us to identify the hashing algorithm.
    """
    # Generate bcrypt hash (automatically includes salt)
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return f"{hashed.decode('utf-8')}"


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
    __table_args__ = (
        CheckConstraint(
            "password_hash IS NOT NULL OR api_key IS NOT NULL",
            name="user_has_auth_method",
        ),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    user_type = Column(String, nullable=False)  # Discriminator column

    # Make these nullable since subclasses will use them selectively
    password_hash = Column(String, nullable=True)
    api_key = Column(String, nullable=True, unique=True)

    # MCP tool scopes - controls which tools this user can access
    # Example: ["read", "observe", "github"] or ["*"] for full access
    scopes = Column(ARRAY(String), nullable=False, default=["read"])

    # Relationship to sessions
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )
    oauth_states = relationship(
        "OAuthState", back_populates="user", cascade="all, delete-orphan"
    )
    discord_users = relationship("DiscordUser", back_populates="system_user")

    __mapper_args__ = {
        "polymorphic_on": user_type,
        "polymorphic_identity": "user",
    }

    def serialize(self) -> dict:
        return {
            "user_id": self.id,
            "name": self.name,
            "email": self.email,
            "user_type": self.user_type,
            "scopes": self.scopes or ["read"],
            "discord_users": {
                discord_user.id: discord_user.username
                for discord_user in self.discord_users
            },
        }


class HumanUser(User):
    """Human user with password authentication"""

    __mapper_args__ = {
        "polymorphic_identity": "human",
    }

    def is_valid_password(self, password: str) -> bool:
        """Check if the provided password is valid for this user.

        Automatically upgrades legacy SHA-256 hashes to bcrypt on successful login.
        """
        return verify_password(password, cast(str, self.password_hash))

    @classmethod
    def create_with_password(cls, email: str, name: str, password: str) -> "HumanUser":
        """Create a new human user with a hashed password"""
        return cls(
            email=email,
            name=name,
            password_hash=hash_password(password),
            user_type="human",
        )


class BotUser(User):
    """Bot user with API key authentication"""

    __mapper_args__ = {
        "polymorphic_identity": "bot",
    }

    @classmethod
    def create_with_api_key(
        cls, name: str, email: str, api_key: str | None = None
    ) -> "BotUser":
        """Create a new bot user with an API key"""
        if api_key is None:
            api_key = f"bot_{secrets.token_hex(32)}"
        return cls(
            name=name,
            email=email,
            api_key=api_key,
            user_type=cls.__mapper_args__["polymorphic_identity"],
        )


class DiscordBotUser(BotUser):
    """Bot user with API key authentication"""

    __mapper_args__ = {
        "polymorphic_identity": "discord_bot",
    }

    @classmethod
    def create_with_api_key(
        cls,
        discord_users: list,
        name: str,
        email: str,
        api_key: str | None = None,
    ) -> "DiscordBotUser":
        if not discord_users:
            raise ValueError("discord_users must be provided")
        bot = super().create_with_api_key(name, email, api_key)
        bot.discord_users = discord_users
        return bot

    @property
    def discord_id(self) -> int | None:
        if not self.discord_users:
            return None
        return self.discord_users[0].id


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    oauth_state_id = Column(Integer, ForeignKey("oauth_states.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    # Relationship to user
    user = relationship("User", back_populates="sessions")
    oauth_state = relationship("OAuthState", back_populates="session")


class OAuthClientInformation(Base):
    __tablename__ = "oauth_client"

    client_id = Column(String, primary_key=True)
    client_secret = Column(String, nullable=True)
    client_id_issued_at = Column(Numeric, nullable=False)
    client_secret_expires_at = Column(Numeric, nullable=True)

    redirect_uris = Column(ARRAY(String), nullable=False)
    token_endpoint_auth_method = Column(String, nullable=False)
    grant_types = Column(ARRAY(String), nullable=False)
    response_types = Column(ARRAY(String), nullable=False)
    scope = Column(String, nullable=False)
    client_name = Column(String, nullable=False)
    client_uri = Column(String, nullable=True)
    logo_uri = Column(String, nullable=True)
    contacts = Column(ARRAY(String), nullable=True)
    tos_uri = Column(String, nullable=True)
    policy_uri = Column(String, nullable=True)
    jwks_uri = Column(String, nullable=True)

    sessions = relationship(
        "OAuthState", back_populates="client", cascade="all, delete-orphan"
    )

    def serialize(self) -> dict:
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
    id = Column(Integer, primary_key=True)
    client_id = Column(String, ForeignKey("oauth_client.client_id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    scopes = Column(ARRAY(String), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    def serialize(self) -> dict:
        return {
            "client_id": self.client_id,
            "scopes": self.scopes,
            "expires_at": self.expires_at.timestamp(),
        }


class OAuthState(Base, OAuthToken):
    __tablename__ = "oauth_states"

    state = Column(String, nullable=False)
    code = Column(String, nullable=True)
    redirect_uri = Column(String, nullable=False)
    redirect_uri_provided_explicitly = Column(Boolean, nullable=False)
    code_challenge = Column(String, nullable=True)
    stale = Column(Boolean, nullable=False, default=False)

    def serialize(self, code: bool = False) -> dict:
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

    client = relationship("OAuthClientInformation", back_populates="sessions")
    session = relationship("UserSession", back_populates="oauth_state", uselist=False)
    user = relationship("User", back_populates="oauth_states")


class OAuthRefreshToken(Base, OAuthToken):
    __tablename__ = "oauth_refresh_tokens"

    token = Column(
        String, nullable=False, default=lambda: f"rt_{secrets.token_hex(32)}"
    )
    revoked = Column(Boolean, nullable=False, default=False)

    # Optional: link to the access token session that was created with this refresh token
    access_token_session_id = Column(
        String, ForeignKey("user_sessions.id"), nullable=True
    )

    # Relationships
    client = relationship("OAuthClientInformation")
    user = relationship("User")
    access_token_session = relationship("UserSession")

    def serialize(self) -> dict:
        return {
            "token": self.token,
            "expires_at": self.expires_at.timestamp(),
            "revoked": self.revoked,
        } | super().serialize()


def purge_oauth(session: Session):
    for token in session.query(OAuthRefreshToken).all():
        session.delete(token)
    for user_session in session.query(UserSession).all():
        session.delete(user_session)

    for oauth_state in session.query(OAuthState).all():
        session.delete(oauth_state)
    for oauth_client in session.query(OAuthClientInformation).all():
        session.delete(oauth_client)
