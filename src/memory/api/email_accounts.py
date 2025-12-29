"""API endpoints for Email Account management."""

from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import EmailAccount
from memory.api.auth import get_current_user

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])


class EmailAccountCreate(BaseModel):
    name: str
    email_address: EmailStr
    imap_server: str
    imap_port: int = 993
    username: str
    password: str
    use_ssl: bool = True
    folders: list[str] = []
    tags: list[str] = []


class EmailAccountUpdate(BaseModel):
    name: str | None = None
    imap_server: str | None = None
    imap_port: int | None = None
    username: str | None = None
    password: str | None = None
    use_ssl: bool | None = None
    folders: list[str] | None = None
    tags: list[str] | None = None
    active: bool | None = None


class EmailAccountResponse(BaseModel):
    id: int
    name: str
    email_address: str
    imap_server: str
    imap_port: int
    username: str
    use_ssl: bool
    folders: list[str]
    tags: list[str]
    last_sync_at: str | None
    active: bool
    created_at: str
    updated_at: str


def account_to_response(account: EmailAccount) -> EmailAccountResponse:
    """Convert an EmailAccount model to a response model."""
    return EmailAccountResponse(
        id=cast(int, account.id),
        name=cast(str, account.name),
        email_address=cast(str, account.email_address),
        imap_server=cast(str, account.imap_server),
        imap_port=cast(int, account.imap_port),
        username=cast(str, account.username),
        use_ssl=cast(bool, account.use_ssl),
        folders=list(account.folders or []),
        tags=list(account.tags or []),
        last_sync_at=account.last_sync_at.isoformat() if account.last_sync_at else None,
        active=cast(bool, account.active),
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else "",
    )


@router.get("")
def list_accounts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[EmailAccountResponse]:
    """List all email accounts."""
    accounts = db.query(EmailAccount).all()
    return [account_to_response(account) for account in accounts]


@router.post("")
def create_account(
    data: EmailAccountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EmailAccountResponse:
    """Create a new email account."""
    # Check for duplicate email address
    existing = (
        db.query(EmailAccount)
        .filter(EmailAccount.email_address == data.email_address)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email account already exists")

    account = EmailAccount(
        name=data.name,
        email_address=data.email_address,
        imap_server=data.imap_server,
        imap_port=data.imap_port,
        username=data.username,
        password=data.password,
        use_ssl=data.use_ssl,
        folders=data.folders,
        tags=data.tags,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.get("/{account_id}")
def get_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EmailAccountResponse:
    """Get a single email account."""
    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account_to_response(account)


@router.patch("/{account_id}")
def update_account(
    account_id: int,
    updates: EmailAccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EmailAccountResponse:
    """Update an email account."""
    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if updates.name is not None:
        account.name = updates.name
    if updates.imap_server is not None:
        account.imap_server = updates.imap_server
    if updates.imap_port is not None:
        account.imap_port = updates.imap_port
    if updates.username is not None:
        account.username = updates.username
    if updates.password is not None:
        account.password = updates.password
    if updates.use_ssl is not None:
        account.use_ssl = updates.use_ssl
    if updates.folders is not None:
        account.folders = updates.folders
    if updates.tags is not None:
        account.tags = updates.tags
    if updates.active is not None:
        account.active = updates.active

    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete an email account."""
    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    db.delete(account)
    db.commit()

    return {"status": "deleted"}


@router.post("/{account_id}/sync")
def trigger_sync(
    account_id: int,
    since_date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a sync for an email account."""
    from memory.workers.tasks.email import sync_account

    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    task = sync_account.delay(account_id, since_date=since_date)

    return {"task_id": task.id, "status": "scheduled"}


@router.post("/{account_id}/test")
def test_connection(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Test IMAP connection for an email account."""
    from memory.workers.email import imap_connection

    account = db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    try:
        with imap_connection(account) as conn:
            # List folders to verify connection works
            status, folders = conn.list()
            if status != "OK":
                return {"status": "error", "message": "Failed to list folders"}

            folder_count = len(folders) if folders else 0
            return {
                "status": "success",
                "message": f"Connected successfully. Found {folder_count} folders.",
                "folders": folder_count,
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}
