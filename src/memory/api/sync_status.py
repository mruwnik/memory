from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user, resolve_user_filter
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.slack import SlackUserCredentials, SlackWorkspace
from memory.common.db.models.sources import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    GithubAccount,
    GithubRepo,
    GoogleAccount,
    GoogleFolder,
    TranscriptAccount,
)
from memory.common.db.models.sync_status import SyncStatusMixin, redact_secrets

router = APIRouter(prefix="/sync-failures", tags=["sync-status"])


class SyncStatusResponse(BaseModel):
    sync_error: str | None
    sync_failing_since: str | None


def sync_status_fields(source: SyncStatusMixin) -> dict[str, str | None]:
    # Redacted again on read: rows stored before redaction existed, or by writes
    # that bypass record_sync, still reach users.
    since = source.sync_failing_since
    return {
        "sync_error": source.sync_error and redact_secrets(source.sync_error),
        "sync_failing_since": since.isoformat() if since else None,
    }


class SyncFailureCounts(BaseModel):
    """Failing sources per Sources tab, keyed by the tab's value."""

    accounts: int
    email: int
    feeds: int
    github: int
    drive: int
    calendar: int
    transcripts: int
    slack: int


def count_failing(db: Session, model: type[SyncStatusMixin], *filters) -> int:
    return (
        db.query(func.count())
        .select_from(model)
        .filter(model.sync_error.isnot(None), *filters)
        .scalar()
    )


@router.get("")
def sync_failure_counts(
    user_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SyncFailureCounts:
    """Counts of sources whose latest sync failed, scoped like the Sources tabs' own lists."""
    uid = resolve_user_filter(user_id, user, db)

    def owned(column) -> list:
        return [] if uid is None else [column == uid]

    def of_accounts(model, account_model) -> list:
        if uid is None:
            return []
        accounts = db.query(account_model.id).filter(account_model.user_id == uid)
        return [model.account_id.in_(accounts)]

    # The feeds list ignores user_id for admins, so the tab count must too.
    feed_owner = [] if has_admin_scope(user) else [ArticleFeed.user_id == user.id]

    if uid is None:
        slack_filter = []
    else:
        connected = db.query(SlackUserCredentials.workspace_id).filter(
            SlackUserCredentials.user_id == uid
        )
        slack_filter = [SlackWorkspace.id.in_(connected)]

    return SyncFailureCounts(
        accounts=count_failing(db, GoogleAccount, *owned(GoogleAccount.user_id)),
        email=count_failing(db, EmailAccount, *owned(EmailAccount.user_id)),
        feeds=count_failing(db, ArticleFeed, *feed_owner),
        github=count_failing(db, GithubRepo, *of_accounts(GithubRepo, GithubAccount)),
        drive=count_failing(db, GoogleFolder, *of_accounts(GoogleFolder, GoogleAccount)),
        calendar=count_failing(db, CalendarAccount, *owned(CalendarAccount.user_id)),
        transcripts=count_failing(db, TranscriptAccount, *owned(TranscriptAccount.user_id)),
        slack=count_failing(db, SlackWorkspace, *slack_filter),
    )
