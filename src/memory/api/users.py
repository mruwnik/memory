"""API endpoints for User management."""

from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from memory.common.db.connection import get_session
from memory.common.db.models import APIKey, APIKeyType, BotUser, HumanUser, User
from memory.common.db.models.users import hash_password, verify_password
from memory.common.scopes import (
    ADMIN_SCOPE,
    VALID_SCOPES,
    WILDCARD_SCOPE,
    validate_scopes,
)
from memory.api.auth import get_current_user, require_scope

# Valid API key types for validation (derived from APIKeyType constants)
VALID_KEY_TYPES = frozenset(APIKeyType.ALL_TYPES)

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str | None = None
    user_type: Literal["human", "bot"] = "human"
    scopes: list[str] = ["read"]


class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    scopes: list[str] | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordReset(BaseModel):
    new_password: str


MIN_PASSWORD_LENGTH = 8


def validate_password_strength(password: str) -> None:
    """Validate password meets minimum requirements."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    user_type: str
    scopes: list[str]
    api_key_count: int = 0
    created_at: str | None = None

    model_config = {"from_attributes": True}


def user_to_response(user: User) -> UserResponse:
    """Convert a User model to a response model."""
    key_count = sum(1 for k in (user.api_keys or []) if not k.revoked)

    return UserResponse(
        id=cast(int, user.id),
        name=cast(str, user.name),
        email=cast(str, user.email),
        user_type=cast(str, user.user_type),
        scopes=list(user.scopes or []),
        api_key_count=key_count,
    )


def has_admin_scope(user: User) -> bool:
    """Check if user has admin scope."""
    user_scopes = user.scopes or []
    return "*" in user_scopes or ADMIN_SCOPE in user_scopes


@router.get("")
def list_users(
    user: User = require_scope(ADMIN_SCOPE),
    db: Session = Depends(get_session),
) -> list[UserResponse]:
    """List all users. Requires admin:users scope."""
    users = db.query(User).all()
    return [user_to_response(u) for u in users]


@router.post("")
def create_user(
    data: UserCreate,
    user: User = require_scope(ADMIN_SCOPE),
    db: Session = Depends(get_session),
) -> UserResponse:
    """Create a new user. Requires admin:users scope."""
    # Check for duplicate email
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    # Validate that all scopes are known
    invalid_scopes = validate_scopes(data.scopes)
    if invalid_scopes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scopes: {', '.join(invalid_scopes)}",
        )

    # Prevent scope escalation: only users with * scope can grant * scope
    user_scopes = user.scopes or []
    if WILDCARD_SCOPE in data.scopes and WILDCARD_SCOPE not in user_scopes:
        raise HTTPException(
            status_code=403,
            detail="Only users with full admin (*) scope can grant full admin scope",
        )

    if data.user_type == "human":
        if not data.password:
            raise HTTPException(status_code=400, detail="Password is required for human users")
        validate_password_strength(data.password)
        new_user = HumanUser.create_with_password(
            email=data.email,
            name=data.name,
            password=data.password,
        )
        new_user.scopes = data.scopes
    else:
        new_user = BotUser.create_with_api_key(
            email=data.email,
            name=data.name,
        )
        new_user.scopes = data.scopes

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return user_to_response(new_user)


@router.get("/me")
def get_current_user_details(
    user: User = Depends(get_current_user),
) -> UserResponse:
    """Get current user's details."""
    return user_to_response(user)


class ScopeResponse(BaseModel):
    """Response model for scope information."""

    value: str
    label: str
    description: str
    category: str


@router.get("/scopes")
def list_available_scopes(
    user: User = require_scope(ADMIN_SCOPE),
) -> list[ScopeResponse]:
    """List all available scopes. Requires admin:users scope."""
    return [ScopeResponse(**scope) for scope in VALID_SCOPES]


@router.get("/{user_id}")
def get_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserResponse:
    """Get a user by ID. Admins can get any user, others can only get themselves."""
    if user_id != user.id and not has_admin_scope(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    return user_to_response(target_user)


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    updates: UserUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserResponse:
    """Update a user. Admins can update any user, others can only update their own name/email."""
    is_admin = has_admin_scope(user)
    is_self = user_id == user.id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if updates.name is not None:
        target_user.name = updates.name

    if updates.email is not None:
        # Check for duplicate email
        existing = db.query(User).filter(User.email == updates.email, User.id != user_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        target_user.email = updates.email

    # Only admins can update scopes
    if updates.scopes is not None:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only admins can modify scopes")

        # Validate that all scopes are known
        invalid_scopes = validate_scopes(updates.scopes)
        if invalid_scopes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scopes: {', '.join(invalid_scopes)}",
            )

        # Prevent scope escalation: only users with * scope can grant * scope
        user_scopes = user.scopes or []
        if WILDCARD_SCOPE in updates.scopes and WILDCARD_SCOPE not in user_scopes:
            raise HTTPException(
                status_code=403,
                detail="Only users with full admin (*) scope can grant full admin scope",
            )

        target_user.scopes = updates.scopes

    db.commit()
    db.refresh(target_user)

    return user_to_response(target_user)


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    user: User = require_scope(ADMIN_SCOPE),
    db: Session = Depends(get_session),
):
    """Delete a user. Requires admin:users scope."""
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(target_user)
    db.commit()

    return {"status": "deleted"}


@router.post("/me/change-password")
def change_password(
    data: PasswordChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Change current user's password. Requires current password verification."""
    if user.user_type != "human":
        raise HTTPException(status_code=400, detail="Only human users can change passwords")

    human_user = db.get(HumanUser, user.id)
    if not human_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify current password
    if not verify_password(data.current_password, cast(str, human_user.password_hash)):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Validate and update password
    validate_password_strength(data.new_password)
    human_user.password_hash = hash_password(data.new_password)
    db.commit()

    return {"status": "password_changed"}


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    data: PasswordReset,
    user: User = require_scope(ADMIN_SCOPE),
    db: Session = Depends(get_session),
):
    """Reset a user's password (admin only). Does not require current password."""
    # Must fetch as HumanUser to access password_hash
    human_user = db.get(HumanUser, user_id)
    if not human_user:
        raise HTTPException(status_code=404, detail="User not found")

    validate_password_strength(data.new_password)
    human_user.password_hash = hash_password(data.new_password)
    db.commit()

    return {"status": "password_reset"}


# --- API Key Management ---


class APIKeyCreate(BaseModel):
    """Request model for creating a new API key.

    For one-time use keys, set key_type="one_time".
    """

    name: str | None = None
    key_type: str = APIKeyType.INTERNAL
    scopes: list[str] | None = None
    expires_in_days: int | None = None

    @field_validator("key_type")
    @classmethod
    def validate_key_type(cls, v: str) -> str:
        if v not in VALID_KEY_TYPES:
            raise ValueError(f"Invalid key_type. Must be one of: {', '.join(sorted(VALID_KEY_TYPES))}")
        return v


class APIKeyResponse(BaseModel):
    """Response model for API key details (excludes the actual key)."""

    id: int
    name: str | None
    key_type: str
    scopes: list[str] | None
    is_one_time: bool
    created_at: str | None
    expires_at: str | None
    last_used_at: str | None
    revoked: bool
    key_preview: str | None

    model_config = {"from_attributes": True}


class APIKeyCreateResponse(APIKeyResponse):
    """Response model for newly created API key (includes the key once)."""

    key: str


def api_key_to_response(key: APIKey) -> APIKeyResponse:
    """Convert an APIKey model to a response model."""
    data = key.serialize()
    return APIKeyResponse(**data)


# API Key Management Endpoints
# All key management is done via /{user_id}/api-keys endpoints.
# Users can access their own keys, admins can access any user's keys.


@router.get("/{user_id}/api-keys")
def list_user_api_keys(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[APIKeyResponse]:
    """List all API keys for a user. Admins can list any user's keys."""
    if user_id != user.id and not has_admin_scope(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    keys = db.query(APIKey).filter(APIKey.user_id == user_id).all()
    return [api_key_to_response(k) for k in keys]


@router.post("/{user_id}/api-keys")
def create_user_api_key(
    user_id: int,
    data: APIKeyCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> APIKeyCreateResponse:
    """Create an API key for a user. Admins can create keys for any user."""
    if user_id != user.id and not has_admin_scope(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    expires_at = None
    if data.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=data.expires_in_days)

    api_key = APIKey.create(
        user_id=user_id,
        key_type=data.key_type,
        name=data.name,
        scopes=data.scopes,
        expires_at=expires_at,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    response_data = api_key.serialize()
    response_data["key"] = api_key.key
    return APIKeyCreateResponse(**response_data)


@router.delete("/{user_id}/api-keys/{key_id}")
def revoke_user_api_key(
    user_id: int,
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Revoke (soft-delete) an API key. Users can revoke their own, admins can revoke any."""
    if user_id != user.id and not has_admin_scope(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    api_key = db.get(APIKey, key_id)
    if not api_key or api_key.user_id != user_id:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.revoked = True
    db.commit()

    return {"status": "revoked"}


@router.delete("/{user_id}/api-keys/{key_id}/permanent")
def delete_user_api_key(
    user_id: int,
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Permanently delete an API key. Users can delete their own, admins can delete any."""
    if user_id != user.id and not has_admin_scope(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    api_key = db.get(APIKey, key_id)
    if not api_key or api_key.user_id != user_id:
        raise HTTPException(status_code=404, detail="API key not found")

    db.delete(api_key)
    db.commit()

    return {"status": "deleted"}
