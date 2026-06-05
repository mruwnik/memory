"""API endpoints for Email Account management."""

import logging
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.orm import Session

from memory.api.auth import (
    assert_project_membership,
    get_current_user,
    get_user_account,
    resolve_user_filter,
)
from memory.common.celery_app import SYNC_ACCOUNT
from memory.common.celery_app import app as celery_app
from memory.common.data_source_access import (
    enqueue_access_control_propagation,
    mark_access_control_changed_if_needed,
)
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import EmailAccount, GoogleAccount
from memory.common.ssrf import UnsafeURLError, validate_public_hostname
from memory.common.scopes import StorableSensitivityLiteral
from memory.workers.email import imap_connection, list_imap_folders

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])

# Standard IMAP / SMTP service ports. We refuse to open connections to
# arbitrary ports because the test endpoint surfaces connect-vs-refuse
# differential timing/error shapes that double as a TCP-probe primitive
# on the API container's network. Restricting to the known ports
# eliminates the recon surface — a legitimate IMAP host always serves
# 143 or 993 — while still letting operators self-host on either.
_ALLOWED_IMAP_PORTS: frozenset[int] = frozenset({143, 993})
_ALLOWED_SMTP_PORTS: frozenset[int] = frozenset({25, 465, 587, 2525})


def _validate_imap_settings(server: str | None, port: int | None) -> None:
    """Refuse private/loopback/link-local IMAP servers and non-standard ports.

    Wraps ``validate_public_hostname`` (rejects internal addresses,
    DNS-resolves) and adds a port allowlist. Raises HTTPException(400)
    on violation so the validation error reaches the caller as a 400
    rather than crashing the request handler.
    """
    if server is not None:
        try:
            validate_public_hostname(server)
        except UnsafeURLError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid imap_server: {exc}"
            ) from exc
    if port is not None and port not in _ALLOWED_IMAP_PORTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"imap_port must be one of {sorted(_ALLOWED_IMAP_PORTS)} "
                "(standard IMAP ports)"
            ),
        )


def _validate_smtp_settings(server: str | None, port: int | None) -> None:
    """Same hostname/port allowlist treatment for the SMTP companion fields."""
    if server is not None:
        try:
            validate_public_hostname(server)
        except UnsafeURLError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid smtp_server: {exc}"
            ) from exc
    if port is not None and port not in _ALLOWED_SMTP_PORTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"smtp_port must be one of {sorted(_ALLOWED_SMTP_PORTS)} "
                "(standard SMTP ports)"
            ),
        )


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
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    # Gmail fields
    google_account_id: int | None = None
    # Common fields
    folders: list[str] = []
    tags: list[str] = []
    send_enabled: bool = True
    # Access control
    project_id: int | None = None
    # StorableSensitivityLiteral includes the "hidden" tombstone, which excludes
    # the account's mail from search/visibility entirely (even for admins). This
    # API is the one surface allowed to set it — "hidden" is deliberately kept
    # out of the role-granted ladder (SensitivityLevelLiteral) so generic
    # content-create paths can't.
    sensitivity: StorableSensitivityLiteral = "basic"

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
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    # Gmail fields
    google_account_id: int | None = None
    # Common fields
    folders: list[str] | None = None
    tags: list[str] | None = None
    active: bool | None = None  # sync enabled
    send_enabled: bool | None = None
    # Access control
    project_id: int | None = None
    # See EmailAccountCreate.sensitivity — "hidden" tombstones the account's
    # mail. Reversible: messages re-inherit a normal level when changed back.
    sensitivity: StorableSensitivityLiteral | None = None


class EmailAccountTest(BaseModel):
    """Credentials to test an IMAP login.

    ``id`` (if given) names a stored account, ownership-checked, whose
    values form the base. Any other field overrides the stored value;
    an empty/omitted ``password`` keeps the stored one.
    """

    id: int | None = None
    imap_server: str | None = None
    imap_port: int | None = None
    username: str | None = None
    password: str | None = None
    use_ssl: bool | None = None


class ImapFolderInfo(BaseModel):
    """A mailbox surfaced by a connection test, for the folder picker."""

    name: str
    flags: list[str]
    selectable: bool


class EmailAccountTestResult(BaseModel):
    status: Literal["success", "error"]
    message: str
    folders: list[ImapFolderInfo] = []


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


def account_to_response(account: EmailAccount, db: Session) -> EmailAccountResponse:
    """Convert an EmailAccount model to a response model."""
    google_account_info = None
    if account.google_account_id:
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
        send_enabled=cast(bool, account.send_enabled)
        if account.send_enabled is not None
        else True,
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else "",
        project_id=account.project_id,
        sensitivity=cast(str, account.sensitivity) or "basic",
    )


@router.get("")
def list_accounts(
    user_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[EmailAccountResponse]:
    """List email accounts. Admins can view any user's accounts or all accounts."""
    resolved_user_id = resolve_user_filter(user_id, user, db)
    query = db.query(EmailAccount)
    if resolved_user_id is not None:
        query = query.filter(EmailAccount.user_id == resolved_user_id)
    accounts = query.all()
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

    # For Gmail accounts, verify the Google account exists AND belongs to this user
    if data.account_type == "gmail" and data.google_account_id:
        google_account = db.get(GoogleAccount, data.google_account_id)
        if not google_account or google_account.user_id != user.id:
            raise HTTPException(status_code=400, detail="Google account not found")

    # SSRF / port-probe guard on user-supplied IMAP/SMTP host:port. The
    # test_connection endpoint below opens a TCP connection from inside
    # the API container's network position, so an unvalidated server
    # name is a generic recon primitive against postgres/redis/qdrant/
    # AWS metadata (CWE-918, CWE-200). Validation is skipped for Gmail
    # accounts (which use Google OAuth, not user-supplied IMAP servers).
    if data.account_type == "imap":
        _validate_imap_settings(data.imap_server, data.imap_port)
        _validate_smtp_settings(data.smtp_server, data.smtp_port)

    # Block non-admins from tagging accounts into projects they aren't in.
    assert_project_membership(db, user, data.project_id)

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

    snap_pid, snap_sens = account.project_id, account.sensitivity
    if updates.name is not None:
        account.name = updates.name
    # Same SSRF / port-allowlist guard as create_account, applied
    # whenever the user changes server/port. We validate against the
    # newly-supplied value, not the row's current state.
    if updates.imap_server is not None or updates.imap_port is not None:
        _validate_imap_settings(updates.imap_server, updates.imap_port)
    if updates.smtp_server is not None or updates.smtp_port is not None:
        _validate_smtp_settings(updates.smtp_server, updates.smtp_port)
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
        # Verify Google account belongs to this user
        if updates.google_account_id:
            google_account = db.get(GoogleAccount, updates.google_account_id)
            if not google_account or google_account.user_id != user.id:
                raise HTTPException(status_code=400, detail="Google account not found")
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
        assert_project_membership(db, user, updates.project_id)
        account.project_id = updates.project_id
    if updates.sensitivity is not None:
        account.sensitivity = updates.sensitivity

    access_changed = mark_access_control_changed_if_needed(
        account,
        snapshot_project_id=snap_pid,
        snapshot_sensitivity=snap_sens,
    )

    db.commit()
    db.refresh(account)

    if access_changed:
        enqueue_access_control_propagation("email_account", account)

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
    get_user_account(db, EmailAccount, account_id, user)  # Verify ownership

    task = celery_app.send_task(
        SYNC_ACCOUNT,
        args=[account_id],
        kwargs={"since_date": since_date},
    )

    return {"task_id": task.id, "status": "scheduled"}


@router.post("/test")
def test_connection(
    data: EmailAccountTest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EmailAccountTestResult:
    """Test an IMAP login against typed and/or stored credentials.

    When ``data.id`` is set the stored account supplies base values
    (loaded via ``get_user_account``, so ownership is enforced); each
    provided body field overrides it, except a blank ``password`` which
    falls back to the stored one. The effective ``imap_server`` /
    ``imap_port`` are re-validated against the SSRF/port allowlist
    (``validate_public_hostname`` re-resolves DNS, defeating rebinding),
    then a transient account drives ``imap_connection`` -> ``conn.list()``.
    On success the parsed folder list is returned so the client can offer a
    folder picker. Any connection failure returns a deliberately-generic
    message so the connect/refuse/auth-failed differential can't enumerate
    internal hosts/ports.
    """
    base = (
        get_user_account(db, EmailAccount, data.id, user)
        if data.id is not None
        else None
    )

    if base is not None and base.account_type != "imap":
        return EmailAccountTestResult(
            status="error",
            message="Testing is only supported for IMAP accounts",
        )

    imap_server = data.imap_server or (base.imap_server if base else None)
    imap_port = data.imap_port or (base.imap_port if base else None) or 993
    username = data.username or (base.username if base else None)
    password = data.password or (base.password if base else None)
    if data.use_ssl is not None:
        use_ssl = data.use_ssl
    else:
        use_ssl = base.use_ssl if base else True

    if not imap_server or not username or not password:
        raise HTTPException(
            status_code=400,
            detail="imap_server, username and password are required",
        )

    # Same SSRF / port-allowlist guard as create_account, applied to the
    # effective merged values. Raises HTTPException(400) on a private/
    # loopback host or non-standard port.
    _validate_imap_settings(imap_server, imap_port)

    probe = EmailAccount(
        imap_server=imap_server,
        imap_port=imap_port,
        username=username,
        password=password,
        use_ssl=use_ssl,
    )

    try:
        with imap_connection(probe) as conn:
            folders = list_imap_folders(conn)
    except Exception as e:
        logger.warning(
            "IMAP connection test failed (account id=%s): %s: %s",
            data.id,
            type(e).__name__,
            e,
        )
        return EmailAccountTestResult(status="error", message="Connection failed")

    return EmailAccountTestResult(
        status="success",
        message=f"Connected successfully. Found {len(folders)} folders.",
        folders=[
            ImapFolderInfo(name=f.name, flags=f.flags, selectable=f.selectable)
            for f in folders
        ],
    )
