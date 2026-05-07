"""API endpoints for Transcript Account management (Fireflies, etc.)."""

import logging
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.api.auth import (
    assert_project_membership,
    get_current_user,
    get_user_account,
    resolve_user_filter,
)
from memory.common.celery_app import (
    RESCAN_TRANSCRIPT_ACCOUNT,
    SYNC_TRANSCRIPT_ACCOUNT,
    app as celery_app,
)
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import TranscriptAccount
from memory.workers.tasks.transcripts import PROVIDERS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcript-accounts", tags=["transcript-accounts"])


SUPPORTED_PROVIDERS = sorted(PROVIDERS.keys())


class TranscriptAccountCreate(BaseModel):
    name: str
    provider: str
    api_key: str
    webhook_secret: str | None = None
    tags: list[str] = []
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] = "basic"


class TranscriptAccountUpdate(BaseModel):
    name: str | None = None
    # Leave None to keep existing key; pass a string to rotate.
    api_key: str | None = None
    # Pass "" to clear; None to keep; non-empty string to set/rotate.
    webhook_secret: str | None = None
    tags: list[str] | None = None
    active: bool | None = None
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None


class TranscriptAccountResponse(BaseModel):
    id: int
    name: str
    provider: str
    has_api_key: bool
    has_webhook_secret: bool
    tags: list[str]
    last_sync_at: str | None
    sync_error: str | None
    active: bool
    created_at: str
    updated_at: str
    project_id: int | None
    sensitivity: str


def account_to_response(account: TranscriptAccount) -> TranscriptAccountResponse:
    return TranscriptAccountResponse(
        id=cast(int, account.id),
        name=cast(str, account.name),
        provider=cast(str, account.provider),
        has_api_key=bool(account.api_key_encrypted),
        has_webhook_secret=account.webhook_secret_encrypted is not None,
        tags=list(account.tags or []),
        last_sync_at=account.last_sync_at.isoformat() if account.last_sync_at else None,
        sync_error=account.sync_error,
        active=cast(bool, account.active),
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else "",
        project_id=account.project_id,
        sensitivity=cast(str, account.sensitivity) or "basic",
    )


@router.get("/providers")
def list_providers() -> list[str]:
    """List supported transcript providers."""
    return SUPPORTED_PROVIDERS


@router.get("")
def list_accounts(
    user_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[TranscriptAccountResponse]:
    """List transcript accounts. Admins can view any user's accounts or all accounts."""
    resolved_user_id = resolve_user_filter(user_id, user, db)
    query = db.query(TranscriptAccount)
    if resolved_user_id is not None:
        query = query.filter(TranscriptAccount.user_id == resolved_user_id)
    accounts = query.all()
    return [account_to_response(account) for account in accounts]


@router.post("")
def create_account(
    data: TranscriptAccountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TranscriptAccountResponse:
    """Create a new transcript account.

    Admins create on their own user_id (no impersonation here); to create
    an account on behalf of another user, use tools/add_transcript_account.py.
    """
    if data.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider. Supported: {', '.join(SUPPORTED_PROVIDERS)}",
        )

    # Same justification as in update_account: api_key is the only auth path,
    # an empty key would silently break every sync.
    if not data.api_key:
        raise HTTPException(status_code=400, detail="api_key cannot be empty")

    existing = (
        db.query(TranscriptAccount)
        .filter(
            TranscriptAccount.user_id == user.id,
            TranscriptAccount.provider == data.provider,
            TranscriptAccount.name == data.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="A transcript account with this name and provider already exists",
        )

    # Block non-admins from tagging accounts into projects they aren't in.
    assert_project_membership(db, user, data.project_id)

    account = TranscriptAccount(
        user_id=user.id,
        name=data.name,
        provider=data.provider,
        tags=data.tags,
        project_id=data.project_id,
        sensitivity=data.sensitivity,
    )
    account.api_key = data.api_key
    if data.webhook_secret:
        account.webhook_secret = data.webhook_secret

    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against the pre-check or another concurrent create.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="A transcript account with this name and provider already exists",
        )
    db.refresh(account)

    return account_to_response(account)


@router.get("/{account_id}")
def get_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TranscriptAccountResponse:
    """Get a single transcript account."""
    account = get_user_account(db, TranscriptAccount, account_id, user)
    return account_to_response(account)


@router.patch("/{account_id}")
def update_account(
    account_id: int,
    updates: TranscriptAccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TranscriptAccountResponse:
    """Update a transcript account."""
    account = get_user_account(db, TranscriptAccount, account_id, user)

    if updates.name is not None:
        account.name = updates.name
    if updates.api_key is not None:
        # Reject empty string explicitly: api_key is the *only* auth path for
        # transcript providers (no IMAP/CalDAV-style alternative knobs), so
        # an empty key would silently break every subsequent sync.
        if not updates.api_key:
            raise HTTPException(status_code=400, detail="api_key cannot be empty")
        account.api_key = updates.api_key
    if updates.webhook_secret is not None:
        # Empty string clears the secret; non-empty rotates it.
        account.webhook_secret = updates.webhook_secret or None
    if updates.tags is not None:
        account.tags = updates.tags
    if updates.active is not None:
        account.active = updates.active
    # Known limitation (matches email_accounts/calendar_accounts pattern):
    # project_id cannot be unset once set — None means "keep existing", and
    # the request body has no separate sentinel for "clear it". Fix belongs
    # in a project-wide PR introducing an explicit sentinel.
    if updates.project_id is not None:
        assert_project_membership(db, user, updates.project_id)
        account.project_id = updates.project_id
    if updates.sensitivity is not None:
        account.sensitivity = updates.sensitivity

    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete a transcript account."""
    account = get_user_account(db, TranscriptAccount, account_id, user)
    db.delete(account)
    db.commit()
    return {"status": "deleted"}


@router.post("/{account_id}/sync")
def trigger_sync(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a quick sync for a transcript account."""
    get_user_account(db, TranscriptAccount, account_id, user)  # Verify ownership; raises 404 otherwise.
    task = celery_app.send_task(SYNC_TRANSCRIPT_ACCOUNT, args=[account_id])
    return {"task_id": task.id, "status": "scheduled"}


@router.post("/{account_id}/rescan")
def trigger_rescan(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a full rescan for a transcript account."""
    get_user_account(db, TranscriptAccount, account_id, user)  # Verify ownership; raises 404 otherwise.
    task = celery_app.send_task(RESCAN_TRANSCRIPT_ACCOUNT, args=[account_id])
    return {"task_id": task.id, "status": "scheduled"}
