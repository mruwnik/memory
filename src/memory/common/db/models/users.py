import hashlib
import secrets
from typing import cast
import uuid
from memory.common.db.models.base import Base
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
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
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    # Relationship to user
    user = relationship("User", back_populates="sessions")
