"""API endpoints for Email Account management."""

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, model_validator
from sqlalchemy.orm import Session

from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import EmailAccount, GoogleAccount
from memory.api.auth import get_current_user, get_user_account

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])


class EmailAccountCreate(BaseModel):
    name: str
    email_address: EmailStr
    account_type: Literal["imap", "gmail"] = "imap"
    # IMAP fields (required for IMAP, not used for Gmail)
    imap_server: str | None = None
    imap_port: int = 993
    username: str | None = None
    password: str | None = None
    use_ssl: bool = True
    # SMTP fields (optional - inferred from IMAP if not set)
    smtp_server: str | None = None
    smtp_port: int | None = None
    # Gmail fields
    google_account_id: int | None = None
    # Common fields
    folders: list[str] = []
    tags: list[str] = []
    send_enabled: bool = True
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] = "basic"

    @model_validator(mode="after")
    def validate_account_type_fields(self):
        if self.account_type == "imap":
            if not self.imap_server:
                raise ValueError("imap_server is required for IMAP accounts")
            if not self.username:
                raise ValueError("username is required for IMAP accounts")
            if not self.password:
                raise ValueError("password is required for IMAP accounts")
        elif self.account_type == "gmail":
            if not self.google_account_id:
                raise ValueError("google_account_id is required for Gmail accounts")
        return self


class EmailAccountUpdate(BaseModel):
    name: str | None = None
    # IMAP fields
    imap_server: str | None = None
    imap_port: int | None = None
    username: str | None = None
    password: str | None = None
    use_ssl: bool | None = None
    # SMTP fields (optional - inferred from IMAP if not set)
    smtp_server: str | None = None
    smtp_port: int | None = None
    # Gmail fields
    google_account_id: int | None = None
    # Common fields
    folders: list[str] | None = None
    tags: list[str] | None = None
    active: bool | None = None  # sync enabled
    send_enabled: bool | None = None
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None


class GoogleAccountInfo(BaseModel):
    id: int
    name: str
    email: str


class EmailAccountResponse(BaseModel):
    id: int
    name: str
    email_address: str
    account_type: str
    # IMAP fields (nullable for Gmail accounts)
    imap_server: str | None
    imap_port: int | None
    username: str | None
    use_ssl: bool | None
    # SMTP fields (optional - inferred from IMAP if not set)
    smtp_server: str | None
    smtp_port: int | None
    # Gmail fields
    google_account_id: int | None
    google_account: GoogleAccountInfo | None
    # Common fields
    folders: list[str]
    tags: list[str]
    last_sync_at: str | None
    sync_error: str | None
    active: bool  # sync enabled
    send_enabled: bool
    created_at: str
    updated_at: str
    # Access control
    project_id: int | None
    sensitivity: str


def account_to_response(account: EmailAccount, db: Session | None = None) -> EmailAccountResponse:
    """Convert an EmailAccount model to a response model."""
    google_account_info = None
    if account.google_account_id and db:
        ga = db.get(GoogleAccount, account.google_account_id)
        if ga:
            google_account_info = GoogleAccountInfo(
                id=cast(int, ga.id),
                name=cast(str, ga.name),
                email=cast(str, ga.email),
            )

    return EmailAccountResponse(
        id=cast(int, account.id),
        name=cast(str, account.name),
        email_address=cast(str, account.email_address),
        account_type=cast(str, account.account_type) or "imap",
        imap_server=account.imap_server,
        imap_port=account.imap_port,
        username=account.username,
        use_ssl=account.use_ssl,
        smtp_server=account.smtp_server,
        smtp_port=account.smtp_port,
        google_account_id=account.google_account_id,
        google_account=google_account_info,
        folders=list(account.folders or []),
        tags=list(account.tags or []),
        last_sync_at=account.last_sync_at.isoformat() if account.last_sync_at else None,
        sync_error=account.sync_error,
        active=cast(bool, account.active),
        send_enabled=cast(bool, account.send_enabled) if account.send_enabled is not None else True,
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else "",
        project_id=account.project_id,
        sensitivity=cast(str, account.sensitivity) or "basic",
    )


@router.get("")
def list_accounts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[EmailAccountResponse]:
    """List email accounts for the current user."""
    accounts = db.query(EmailAccount).filter(EmailAccount.user_id == user.id).all()
    return [account_to_response(account, db) for account in accounts]


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

    # For Gmail accounts, verify the Google account exists
    if data.account_type == "gmail" and data.google_account_id:
        google_account = db.get(GoogleAccount, data.google_account_id)
        if not google_account:
            raise HTTPException(status_code=400, detail="Google account not found")

    account = EmailAccount(
        user_id=user.id,
        name=data.name,
        email_address=data.email_address,
        account_type=data.account_type,
        imap_server=data.imap_server,
        imap_port=data.imap_port if data.account_type == "imap" else None,
        username=data.username,
        password=data.password,
        use_ssl=data.use_ssl if data.account_type == "imap" else None,
        smtp_server=data.smtp_server if data.account_type == "imap" else None,
        smtp_port=data.smtp_port if data.account_type == "imap" else None,
        google_account_id=data.google_account_id,
        folders=data.folders,
        tags=data.tags,
        send_enabled=data.send_enabled,
        project_id=data.project_id,
        sensitivity=data.sensitivity,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return account_to_response(account, db)


@router.get("/{account_id}")
def get_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EmailAccountResponse:
    """Get a single email account."""
    account = get_user_account(db, EmailAccount, account_id, user)
    return account_to_response(account, db)


@router.patch("/{account_id}")
def update_account(
    account_id: int,
    updates: EmailAccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EmailAccountResponse:
    """Update an email account."""
    account = get_user_account(db, EmailAccount, account_id, user)

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
    if updates.smtp_server is not None:
        account.smtp_server = updates.smtp_server
    if updates.smtp_port is not None:
        account.smtp_port = updates.smtp_port
    if updates.google_account_id is not None:
        account.google_account_id = updates.google_account_id
    if updates.folders is not None:
        account.folders = updates.folders
    if updates.tags is not None:
        account.tags = updates.tags
    if updates.active is not None:
        account.active = updates.active
    if updates.send_enabled is not None:
        account.send_enabled = updates.send_enabled
    if updates.project_id is not None:
        account.project_id = updates.project_id
    if updates.sensitivity is not None:
        account.sensitivity = updates.sensitivity

    db.commit()
    db.refresh(account)

    return account_to_response(account, db)


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete an email account."""
    account = get_user_account(db, EmailAccount, account_id, user)

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
    from memory.common.celery_app import app, SYNC_ACCOUNT

    get_user_account(db, EmailAccount, account_id, user)  # Verify ownership

    task = app.send_task(
        SYNC_ACCOUNT,
        args=[account_id],
        kwargs={"since_date": since_date},
    )

    return {"task_id": task.id, "status": "scheduled"}


@router.post("/{account_id}/test")
def test_connection(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Test IMAP connection for an email account."""
    from memory.workers.email import imap_connection

    account = get_user_account(db, EmailAccount, account_id, user)

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
