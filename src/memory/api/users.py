"""API endpoints for User management."""

import secrets
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from memory.common.db.connection import get_session
from memory.common.db.models import ApiKey, ApiKeyType, BotUser, HumanUser, User
from memory.common.db.models.users import hash_password, verify_password
from memory.api.auth import get_current_user, require_scope

router = APIRouter(prefix="/users", tags=["users"])

ADMIN_SCOPE = "admin:users"


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
    has_api_key: bool
    created_at: str | None = None

    model_config = {"from_attributes": True}


class ApiKeyResponse(BaseModel):
    api_key: str


class ApiKeyCreate(BaseModel):
    """Request body for creating a new API key."""

    name: str | None = None
    key_type: str = "internal"  # internal, mcp, discord, google, github, external
    scopes: list[str] | None = None  # None means inherit user scopes
    expires_in_days: int | None = None  # None means no expiration
    is_one_time: bool = False


class ApiKeyInfo(BaseModel):
    """API key metadata (never includes the actual key)."""

    id: int
    user_id: int
    key_prefix: str
    name: str | None
    key_type: str
    scopes: list[str] | None
    expires_at: str | None
    is_one_time: bool
    is_active: bool
    created_at: str | None
    last_used_at: str | None
    use_count: int

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(BaseModel):
    """Response when creating an API key (includes the plaintext key once)."""

    api_key: str  # The plaintext key - only shown once!
    info: ApiKeyInfo


def user_to_response(user: User) -> UserResponse:
    """Convert a User model to a response model."""
    return UserResponse(
        id=cast(int, user.id),
        name=cast(str, user.name),
        email=cast(str, user.email),
        user_type=cast(str, user.user_type),
        scopes=list(user.scopes or ["read"]),
        has_api_key=user.api_key is not None,
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

    # Prevent scope escalation: only users with * scope can grant * scope
    user_scopes = user.scopes or []
    if "*" in data.scopes and "*" not in user_scopes:
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

        # Prevent scope escalation: only users with * scope can grant * scope
        user_scopes = user.scopes or []
        if "*" in updates.scopes and "*" not in user_scopes:
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


@router.post("/{user_id}/regenerate-api-key")
def regenerate_api_key(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ApiKeyResponse:
    """Regenerate API key for a user. Admins can regenerate any user's key, others only their own."""
    is_admin = has_admin_scope(user)
    is_self = user_id == user.id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Generate new API key with appropriate prefix
    prefix = "bot_" if target_user.user_type == "bot" else "user_"
    new_key = f"{prefix}{secrets.token_hex(32)}"
    target_user.api_key = new_key

    db.commit()

    return ApiKeyResponse(api_key=new_key)


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


# --- API Key Management Endpoints ---


def str_to_key_type(key_type_str: str) -> ApiKeyType:
    """Convert string to ApiKeyType enum."""
    type_map = {
        "internal": ApiKeyType.INTERNAL,
        "mcp": ApiKeyType.MCP,
        "discord": ApiKeyType.DISCORD,
        "google": ApiKeyType.GOOGLE,
        "github": ApiKeyType.GITHUB,
        "external": ApiKeyType.EXTERNAL,
    }
    return type_map.get(key_type_str.lower(), ApiKeyType.INTERNAL)


@router.get("/{user_id}/api-keys")
def list_api_keys(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ApiKeyInfo]:
    """List all API keys for a user. Users can list their own keys, admins can list any user's keys."""
    is_admin = has_admin_scope(user)
    is_self = user_id == user.id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    api_keys = db.query(ApiKey).filter(ApiKey.user_id == user_id).all()
    return [
        ApiKeyInfo(
            id=cast(int, k.id),
            user_id=cast(int, k.user_id),
            key_prefix=cast(str, k.key_prefix),
            name=k.name,
            key_type=k.key_type.value if k.key_type else "internal",
            scopes=list(k.scopes) if k.scopes else None,
            expires_at=k.expires_at.isoformat() if k.expires_at else None,
            is_one_time=cast(bool, k.is_one_time),
            is_active=cast(bool, k.is_active),
            created_at=k.created_at.isoformat() if k.created_at else None,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            use_count=cast(int, k.use_count or 0),
        )
        for k in api_keys
    ]


@router.post("/{user_id}/api-keys")
def create_api_key(
    user_id: int,
    data: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ApiKeyCreateResponse:
    """Create a new API key for a user. Users can create keys for themselves, admins can create for any user."""
    is_admin = has_admin_scope(user)
    is_self = user_id == user.id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify target user exists
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Calculate expiration
    from datetime import datetime, timedelta, timezone

    expires_at = None
    if data.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=data.expires_in_days)

    # Create the key
    api_key, plaintext_key = ApiKey.create(
        user_id=user_id,
        key_type=str_to_key_type(data.key_type),
        name=data.name,
        scopes=data.scopes,
        expires_at=expires_at,
        is_one_time=data.is_one_time,
        prefix="key",
    )

    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return ApiKeyCreateResponse(
        api_key=plaintext_key,
        info=ApiKeyInfo(
            id=cast(int, api_key.id),
            user_id=cast(int, api_key.user_id),
            key_prefix=cast(str, api_key.key_prefix),
            name=api_key.name,
            key_type=api_key.key_type.value if api_key.key_type else "internal",
            scopes=list(api_key.scopes) if api_key.scopes else None,
            expires_at=api_key.expires_at.isoformat() if api_key.expires_at else None,
            is_one_time=cast(bool, api_key.is_one_time),
            is_active=cast(bool, api_key.is_active),
            created_at=api_key.created_at.isoformat() if api_key.created_at else None,
            last_used_at=api_key.last_used_at.isoformat() if api_key.last_used_at else None,
            use_count=cast(int, api_key.use_count or 0),
        ),
    )


@router.delete("/{user_id}/api-keys/{key_id}")
def delete_api_key(
    user_id: int,
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete an API key. Users can delete their own keys, admins can delete any key."""
    is_admin = has_admin_scope(user)
    is_self = user_id == user.id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    api_key = db.get(ApiKey, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key.user_id != user_id:
        raise HTTPException(status_code=404, detail="API key not found")

    db.delete(api_key)
    db.commit()

    return {"status": "deleted"}


@router.post("/{user_id}/api-keys/{key_id}/revoke")
def revoke_api_key(
    user_id: int,
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Revoke (deactivate) an API key without deleting it. Useful for audit trails."""
    is_admin = has_admin_scope(user)
    is_self = user_id == user.id

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    api_key = db.get(ApiKey, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key.user_id != user_id:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.is_active = False
    db.commit()

    return {"status": "revoked"}
