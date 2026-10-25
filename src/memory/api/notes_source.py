"""The Notes tab: how many notes there are, the vault's last git sync, and its admin-only git setup."""

import logging
from datetime import datetime, timedelta, timezone

import redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user, require_scope
from memory.api.field_errors import field_error
from memory.common import instance_settings, settings
from memory.common.access_control import (
    AccessFilter,
    apply_access_filter_to_query,
    build_access_filter,
    get_user_project_roles,
    has_admin_scope,
)
from memory.common.celery_app import SETUP_GIT_NOTES, TRACK_GIT_CHANGES
from memory.common.db.connection import get_session
from memory.common.db.models import InstanceSetting, JobStatus, Note, PendingJob, User
from memory.common.jobs import dispatch_job
from memory.common.notes_git import (
    VaultGit,
    check_remote_url,
    check_token,
    last_sync,
    read_vault_git,
)
from memory.common.scopes import SCOPE_ADMIN

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notes-source", tags=["notes"])

TOKEN = instance_settings.NOTES_GIT_TOKEN
# Beat's syncs are tracked under the task's name, so this matches them too.
SYNC_JOB_TYPE = "track_git_changes"
SYNC_IN_FLIGHT_WINDOW = timedelta(minutes=15)


class SyncStatus(BaseModel):
    ok: bool
    failed_step: str | None = None
    hint: str | None = None
    at: str


class TokenView(BaseModel):
    set: bool
    # Never the token: only its last four characters.
    hint: str | None = None
    # Set by the operator in the env, so read-only here.
    locked: bool


class GitSetup(BaseModel):
    has_repo: bool
    branch: str | None
    remote_url: str | None
    # A vault that already has a remote keeps it: repointing could push its history somewhere new.
    remote_locked: bool
    author_name: str | None
    author_email: str | None
    token: TokenView
    # What to add as a deploy key for an ssh remote.
    ssh_public_key: str | None


class NotesSource(BaseModel):
    count: int
    last_sync: SyncStatus | None
    # Admins only.
    git: GitSetup | None


class GitSetupIn(BaseModel):
    remote_url: str | None = None
    author_name: str
    author_email: str
    token: str | None = None


class Queued(NotesSource):
    job_id: int


def visible_notes(db: Session, access: AccessFilter | None) -> int:
    query = db.query(func.count(Note.id)).select_from(Note)
    return apply_access_filter_to_query(query, access).scalar() or 0


def readable_last_sync() -> dict | None:
    """The last sync's status, or None while Redis can't be read: it mustn't fail the page."""
    try:
        return last_sync()
    except redis.RedisError:
        logger.warning("Could not read the notes sync status")
        return None


def sync_failing(user: User) -> bool:
    if not has_admin_scope(user):
        return False
    status = readable_last_sync()
    return status is not None and not status["ok"]


def sync_in_flight(db: Session) -> bool:
    # A job lost with its worker would block this for good, so old ones don't count.
    since = datetime.now(timezone.utc) - SYNC_IN_FLIGHT_WINDOW
    return db.query(PendingJob).filter(
        PendingJob.job_type == SYNC_JOB_TYPE,
        PendingJob.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value]),
        PendingJob.created_at >= since,
    ).first() is not None


def ssh_public_key() -> str | None:
    path = settings.SSH_PUBLIC_KEY_FILE
    return (path.read_text().strip() or None) if path.is_file() else None


def token_view(db: Session) -> TokenView:
    if env := TOKEN.env_value():
        return TokenView(set=True, hint=env[-4:], locked=True)
    row = db.get(InstanceSetting, TOKEN.key)
    return TokenView(set=row is not None, hint=row.key_hint if row else None, locked=False)


def git_setup(db: Session) -> GitSetup:
    vault = read_vault_git(settings.NOTES_STORAGE_DIR)
    return GitSetup(
        has_repo=vault.has_repo,
        branch=vault.branch,
        remote_url=vault.remote_url,
        remote_locked=bool(vault.remotes) or not vault.config_readable,
        author_name=vault.author_name,
        author_email=vault.author_email,
        token=token_view(db),
        ssh_public_key=ssh_public_key(),
    )


def notes_source(db: Session, user: User) -> NotesSource:
    status = readable_last_sync()
    return NotesSource(
        count=visible_notes(db, build_access_filter(user, get_user_project_roles(db, user))),
        last_sync=SyncStatus(**status) if status else None,
        git=git_setup(db) if has_admin_scope(user) else None,
    )


def one_line(field: str, value: str) -> str:
    value = value.strip()
    if not value or any(c in value for c in "\r\n\0"):
        raise field_error(("body", field), "Enter a single line")
    return value


def checked_origin(body: GitSetupIn, vault: VaultGit) -> str | None:
    """The remote to add, or None to keep the vault's own."""
    if vault.remotes or not vault.config_readable:
        if body.remote_url and body.remote_url.strip() != vault.remote_url:
            raise field_error(
                ("body", "remote_url"),
                "The vault already has a remote; Memory won't repoint it",
                status_code=409,
            )
        return None
    try:
        return check_remote_url(body.remote_url or "")
    except ValueError as e:
        raise field_error(("body", "remote_url"), str(e))


@router.get("")
def get_notes_source(
    user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> NotesSource:
    return notes_source(db, user)


@router.put("/git")
def set_up_git(
    body: GitSetupIn, user: User = require_scope(SCOPE_ADMIN), db: Session = Depends(get_session)
) -> Queued:
    """Set the vault's remote (unless it has one), commit author and token, then sync it."""
    origin = checked_origin(body, read_vault_git(settings.NOTES_STORAGE_DIR))
    name = one_line("author_name", body.author_name)
    email = one_line("author_email", body.author_email)
    if "@" not in email:
        raise field_error(("body", "author_email"), "Enter an email address")
    if body.token and body.token.strip():
        try:
            token = check_token(body.token)
        except ValueError as e:
            raise field_error(("body", "token"), str(e))
        if TOKEN.env_value():
            raise field_error(
                ("body", "token"),
                f"Set by the operator in the environment ({TOKEN.env_var})",
                status_code=409,
            )
        if not settings.SECRETS_ENCRYPTION_KEY:
            raise field_error(
                ("body", "token"), "SECRETS_ENCRYPTION_KEY must be set to store a token", status_code=409
            )
        instance_settings.save(db, TOKEN, token, user.id)
        db.commit()
        instance_settings.invalidate_cache()

    logger.info("User %s set up the notes git sync", user.id)
    job = dispatch_job(
        db,
        job_type="notes_git_setup",
        task_name=SETUP_GIT_NOTES,
        task_kwargs={"origin": origin, "email": email, "name": name},
        user_id=user.id,
    ).job
    return Queued(**notes_source(db, user).model_dump(), job_id=job.id)


@router.delete("/git/token")
def clear_git_token(
    user: User = require_scope(SCOPE_ADMIN), db: Session = Depends(get_session)
) -> NotesSource:
    instance_settings.clear(db, TOKEN)
    db.commit()
    instance_settings.invalidate_cache()
    logger.info("User %s cleared the notes git token", user.id)
    return notes_source(db, user)


@router.post("/sync")
def sync_now(user: User = require_scope(SCOPE_ADMIN), db: Session = Depends(get_session)) -> dict:
    if not read_vault_git(settings.NOTES_STORAGE_DIR).has_repo:
        raise HTTPException(status_code=409, detail="The notes vault isn't a git repository yet")
    # Each queued sync can hold a notes worker for minutes waiting on the vault's lock.
    if sync_in_flight(db):
        raise HTTPException(status_code=409, detail="A sync of the notes vault is already queued")
    job = dispatch_job(
        db, job_type=SYNC_JOB_TYPE, task_name=TRACK_GIT_CHANGES, task_kwargs={}, user_id=user.id
    ).job
    return {"job_id": job.id, "status": "scheduled"}
