"""Interactive SSH key management. Private material is write-only."""

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.api.auth import get_interactive_user
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.ssh_keys import SSHKey
from memory.common.ssh_keys import can_use_key, encode_key, import_key

router = APIRouter(prefix="/ssh-keys", tags=["ssh-keys"])


class KeyUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    private_key: str | None = Field(default=None, max_length=65536)
    passphrase: str | None = Field(default=None, max_length=4096)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provide a key name")
        return value.strip()


class KeyInput(KeyUpdate):
    generate: bool = False
    instance: bool = False


class DefaultInput(BaseModel):
    key_id: int | None = None


def key_view(key: SSHKey, user: User) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "user_id": key.user_id,
        "public_key": key.public_key,
        "fingerprint": key.fingerprint,
        "is_default": user.default_ssh_key_id == key.id,
        "created_at": key.created_at.isoformat(),
        "updated_at": key.updated_at.isoformat(),
    }


def accessible_key(db: Session, key_id: int, user: User) -> SSHKey:
    key = db.get(SSHKey, key_id)
    if not key or not can_use_key(key, user):
        raise HTTPException(404, "SSH key not found")
    return key


def key_material(
    private_key: str | None, passphrase: str | None, generate: bool = False
):
    if not settings.SECRETS_ENCRYPTION_KEY:
        raise HTTPException(409, "SECRETS_ENCRYPTION_KEY must be configured")
    if generate and (private_key or passphrase):
        raise HTTPException(422, "Choose either import or generation")
    if not generate and not private_key:
        raise HTTPException(422, "Provide a private key")
    try:
        return (
            encode_key(Ed25519PrivateKey.generate())
            if generate
            else import_key(private_key or "", passphrase)
        )
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise HTTPException(
            422, "Invalid or unsupported SSH private key or incorrect passphrase"
        ) from exc


@router.get("")
def list_keys(
    user: User = Depends(get_interactive_user), db: Session = Depends(get_session)
) -> list[dict]:
    visible = SSHKey.user_id == user.id
    if has_admin_scope(user):
        visible = or_(visible, SSHKey.user_id.is_(None))
    return [
        key_view(key, user)
        for key in db.query(SSHKey).filter(visible).order_by(SSHKey.name, SSHKey.id)
    ]


@router.post("")
def create_key(
    body: KeyInput,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> dict:
    if body.instance and not has_admin_scope(user):
        raise HTTPException(403, "Only admins can create instance keys")
    encrypted, public, fingerprint = key_material(
        body.private_key, body.passphrase, body.generate
    )
    key = SSHKey(
        user_id=None if body.instance else user.id,
        name=body.name.strip(),
        private_key_encrypted=encrypted,
        public_key=public,
        fingerprint=fingerprint,
    )
    db.add(key)
    db.commit()
    return key_view(key, user)


@router.put("/default")
def set_default(
    body: DefaultInput,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> dict:
    if body.key_id is not None:
        accessible_key(db, body.key_id, user)
    user.default_ssh_key_id = body.key_id
    db.commit()
    return {"key_id": user.default_ssh_key_id}


@router.put("/{key_id}")
def update_key(
    key_id: int,
    body: KeyUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> dict:
    key = accessible_key(db, key_id, user)
    if body.private_key:
        key.private_key_encrypted, key.public_key, key.fingerprint = key_material(
            body.private_key, body.passphrase
        )
    elif body.passphrase:
        raise HTTPException(422, "Provide the private key with its passphrase")
    key.name = body.name.strip()
    db.commit()
    return key_view(key, user)


@router.delete("/{key_id}")
def delete_key(
    key_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> dict:
    key = accessible_key(db, key_id, user)
    db.delete(key)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "This key is selected for Notes. Change or clear that selection first."
        ) from exc
    return {"deleted": True}
