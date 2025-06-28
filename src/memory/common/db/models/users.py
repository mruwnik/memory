import hashlib
import secrets
from typing import cast
import uuid
from memory.common.db.models.base import Base
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Boolean,
    ARRAY,
    Numeric,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship


def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with salt"""
    salt = secrets.token_hex(16)
    return f"{salt}:{hashlib.sha256((salt + password).encode()).hexdigest()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash"""
    try:
        salt, hash_value = password_hash.split(":", 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == hash_value
    except ValueError:
        return False


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)

    # Relationship to sessions
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )
    oauth_states = relationship(
        "OAuthState", back_populates="user", cascade="all, delete-orphan"
    )

    def serialize(self) -> dict:
        return {
            "user_id": self.id,
            "name": self.name,
            "email": self.email,
        }

    def is_valid_password(self, password: str) -> bool:
        """Check if the provided password is valid for this user"""
        return verify_password(password, cast(str, self.password_hash))

    @classmethod
    def create_with_password(cls, email: str, name: str, password: str) -> "User":
        """Create a new user with a hashed password"""
        return cls(email=email, name=name, password_hash=hash_password(password))


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
