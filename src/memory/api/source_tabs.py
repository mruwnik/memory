from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from memory.api.auth import require_rest_access, resolve_user_filter
from memory.api.notes_source import sync_failing, visible_notes
from memory.common.access_control import (
    AccessFilter,
    apply_access_filter_to_query,
    build_access_filter,
    get_user_project_roles,
    has_admin_scope,
)
from memory.common.db.connection import get_session
from memory.common.db.counts import count_rows
from memory.common.db.models import BookSection, User
from memory.common.db.models.discord import DiscordBot
from memory.common.db.models.secrets import Secret
from memory.common.db.models.slack import SlackUserCredentials, SlackWorkspace
from memory.common.db.models.source_items import ForumPost, Photo
from memory.common.db.models.sources import (
    ArticleFeed,
    LESSWRONG,
    CalendarAccount,
    EmailAccount,
    ForumSyncConfig,
    GithubAccount,
    GithubRepo,
    GoogleAccount,
    GoogleFolder,
    TranscriptAccount,
)
from memory.common.db.models.sync_status import SyncStatusMixin

router = APIRouter(prefix="/source-tabs", tags=["sources"])


class TabCounts(BaseModel):
    count: int
    failing: int = 0


class SourceTabs(BaseModel):
    """What each Sources tab lists, keyed by the tab's value: keys must match the frontend's TABS."""

    accounts: TabCounts
    email: TabCounts
    feeds: TabCounts
    github: TabCounts
    drive: TabCounts
    calendar: TabCounts
    transcripts: TabCounts
    books: TabCounts
    photos: TabCounts
    forums: TabCounts
    notes: TabCounts
    discord: TabCounts
    slack: TabCounts
    secrets: TabCounts


def with_failures(db: Session, model: type[SyncStatusMixin], *criteria) -> TabCounts:
    total, failing = (
        db.query(func.count(), func.count().filter(model.sync_error.isnot(None)))
        .select_from(model)
        .filter(*criteria)
        .one()
    )
    return TabCounts(count=total, failing=failing)


def visible_photos(db: Session, access: AccessFilter | None) -> int:
    # The true accessible total, which can exceed the 100 photos the tab's list returns.
    query = db.query(func.count(Photo.id)).select_from(Photo)
    return apply_access_filter_to_query(query, access).scalar() or 0


def visible_forum_posts(db: Session, access: AccessFilter | None) -> int:
    query = db.query(func.count(ForumPost.id)).select_from(ForumPost)
    return apply_access_filter_to_query(query, access).scalar() or 0


def forum_sync_failing(db: Session, user: User) -> bool:
    # Only admins can change the forum sync settings, so only they are shown its failures.
    config = db.get(ForumSyncConfig, LESSWRONG)
    return has_admin_scope(user) and bool(config and config.sync_error)


def visible_books(db: Session, access: AccessFilter | None) -> int:
    # Like the books list: a Book has no access columns, so it counts when any of its sections is readable.
    query = db.query(func.count(distinct(BookSection.book_id))).select_from(BookSection)
    return apply_access_filter_to_query(query, access).scalar() or 0


@router.get("")
def source_tabs(
    user_id: int | None = None,
    user: User = Depends(require_rest_access),
    db: Session = Depends(get_session),
) -> SourceTabs:
    """Per-tab counts of what the Sources tabs list, and how many of those are failing to sync.

    Each count is scoped like its tab's own list.
    """
    uid = resolve_user_filter(user_id, user, db)
    access = build_access_filter(user, get_user_project_roles(db, user))

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
        discord_bots = count_rows(db, DiscordBot)
    else:
        connected = db.query(SlackUserCredentials.workspace_id).filter(
            SlackUserCredentials.user_id == uid
        )
        slack_filter = [SlackWorkspace.id.in_(connected)]
        discord_bots = count_rows(db, DiscordBot, DiscordBot.created_by_user_id == uid)

    google = with_failures(db, GoogleAccount, *owned(GoogleAccount.user_id))
    folders = with_failures(db, GoogleFolder, *of_accounts(GoogleFolder, GoogleAccount))

    return SourceTabs(
        accounts=TabCounts(
            count=count_rows(db, GithubAccount, *owned(GithubAccount.user_id)) + google.count,
            failing=google.failing,
        ),
        email=with_failures(db, EmailAccount, *owned(EmailAccount.user_id)),
        feeds=with_failures(db, ArticleFeed, *feed_owner),
        github=with_failures(db, GithubRepo, *of_accounts(GithubRepo, GithubAccount)),
        # Deliberately sums both: the Drive tab shows each folder under its Google account,
        # and a failing account (e.g. a lapsed token) stops all of its folders syncing.
        drive=TabCounts(count=folders.count, failing=folders.failing + google.failing),
        calendar=with_failures(db, CalendarAccount, *owned(CalendarAccount.user_id)),
        transcripts=with_failures(db, TranscriptAccount, *owned(TranscriptAccount.user_id)),
        books=TabCounts(count=visible_books(db, access)),
        photos=TabCounts(count=visible_photos(db, access)),
        forums=TabCounts(
            count=visible_forum_posts(db, access), failing=int(forum_sync_failing(db, user))
        ),
        notes=TabCounts(count=visible_notes(db, access), failing=int(sync_failing(user))),
        discord=TabCounts(count=discord_bots),
        slack=with_failures(db, SlackWorkspace, *slack_filter),
        secrets=TabCounts(count=count_rows(db, Secret, Secret.user_id == user.id)),
    )
