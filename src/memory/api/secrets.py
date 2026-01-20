"""API endpoints for encrypted secrets management."""

import logging
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.secrets import (
    Secret,
    create_secret,
    find_secret,
    validate_symbol_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/secrets", tags=["secrets"])


class SecretCreate(BaseModel):
    name: str
    value: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not validate_symbol_name(v):
            raise ValueError(
                "Name must be a valid symbol: start with letter or special char "
                "(*+!-_'?<>=), followed by letters, digits, or special chars"
            )
        return v


class SecretUpdate(BaseModel):
    value: str | None = None
    description: str | None = None


class SecretResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: str
    updated_at: str
    # Note: value is intentionally not included for security


class SecretValueResponse(BaseModel):
    id: int
    name: str
    value: str
    description: str | None


def secret_to_response(secret: Secret) -> SecretResponse:
    """Convert a Secret model to a response model (without value)."""
    return SecretResponse(
        id=cast(int, secret.id),
        name=cast(str, secret.name),
        description=cast(str | None, secret.description),
        created_at=secret.created_at.isoformat() if secret.created_at else "",
        updated_at=secret.updated_at.isoformat() if secret.updated_at else "",
    )


def get_user_secret(db: Session, user: User, secret_id: int) -> Secret:
    """Get a secret ensuring it belongs to the current user."""
    secret = db.get(Secret, secret_id)
    if not secret or secret.user_id != user.id:
        raise HTTPException(status_code=404, detail="Secret not found")
    return secret


@router.get("")
def list_secrets_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SecretResponse]:
    """List all secrets for the current user (metadata only, values are not returned)."""
    secrets = (
        db.query(Secret)
        .filter(Secret.user_id == user.id)
        .order_by(Secret.name)
        .all()
    )
    return [secret_to_response(s) for s in secrets]


@router.post("")
def create_secret_endpoint(
    data: SecretCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SecretResponse:
    """Create a new secret for the current user."""
    # Check for duplicate name for this user
    existing = find_secret(db, cast(int, user.id), data.name)
    if existing:
        raise HTTPException(status_code=400, detail="Secret with this name already exists")

    secret = create_secret(db, cast(int, user.id), data.name, data.value, data.description)
    db.commit()
    db.refresh(secret)

    return secret_to_response(secret)


@router.get("/{secret_id}")
def get_secret_endpoint(
    secret_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SecretResponse:
    """Get a secret's metadata (value is not returned)."""
    secret = get_user_secret(db, user, secret_id)
    return secret_to_response(secret)


@router.get("/{secret_id}/value")
def get_secret_value_endpoint(
    secret_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SecretValueResponse:
    """Get a secret including its decrypted value.

    This endpoint requires explicit access as it returns sensitive data.
    """
    secret = get_user_secret(db, user, secret_id)

    # Audit log for security
    logger.info(f"Secret '{secret.name}' (id={secret_id}) accessed by user {user.id}")

    return SecretValueResponse(
        id=cast(int, secret.id),
        name=cast(str, secret.name),
        value=secret.value,  # Decrypts the value
        description=cast(str | None, secret.description),
    )


@router.patch("/{secret_id}")
def update_secret_endpoint(
    secret_id: int,
    updates: SecretUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SecretResponse:
    """Update a secret's value or description."""
    secret = get_user_secret(db, user, secret_id)

    if updates.value is not None:
        secret.value = updates.value  # Encrypts the value
    if updates.description is not None:
        secret.description = updates.description

    db.commit()
    db.refresh(secret)

    return secret_to_response(secret)


@router.delete("/{secret_id}")
def delete_secret_endpoint(
    secret_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    """Delete a secret."""
    secret = get_user_secret(db, user, secret_id)

    db.delete(secret)
    db.commit()

    return {"status": "deleted"}


@router.get("/by-name/{name}")
def get_secret_by_name_endpoint(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SecretResponse:
    """Get a secret by its symbolic name."""
    secret = find_secret(db, cast(int, user.id), name)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    return secret_to_response(secret)
