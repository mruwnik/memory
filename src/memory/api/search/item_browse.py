"""SQL clauses for browsing stored items without semantic search.

Two dimensions on top of the generic item filters: a case-insensitive text
match on each item type's title, subject and author or sender name (never the
body text, which would scan every row's content), and the connection an item
came from (the email account, channel, chat, feed, repo, folder or account
whose foreign key the item's table carries).
"""

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Column, ColumnElement, SQLColumnExpression, func, or_, select
from sqlalchemy.orm import Session

from memory.api.search.filters import escape_like
from memory.common.db.models import (
    AgentObservation,
    ArticleFeed,
    BeeperChat,
    BeeperMessage,
    BlogPost,
    Book,
    BookSection,
    CalendarAccount,
    CalendarEvent,
    ChatMessage,
    Comic,
    DiscordChannel,
    DiscordMessage,
    EmailAccount,
    ForumComment,
    ForumPost,
    GitCommit,
    GithubItem,
    GithubRepo,
    GoogleDoc,
    GoogleFolder,
    MailMessage,
    Meeting,
    Note,
    Report,
    SlackChannel,
    SlackMessage,
    SourceItem,
    Task,
    TranscriptAccount,
)

EMBED_STATUSES = frozenset({"RAW", "QUEUED", "STORED", "FAILED", "SKIPPED"})
# Done with the pipeline: embedded, or deliberately not (too short to be worth it).
# RAW and QUEUED items are still being processed, and FAILED ones are retried.
PROCESSED_STATUSES = frozenset({"STORED", "SKIPPED"})


def table_columns(model: Any, *names: str) -> tuple[Column, ...]:
    """Columns of the model's own table, so a match needs no inheritance join."""
    return tuple(cast(Column, model.__table__.c[name]) for name in names)


def table_column(model: Any, name: str) -> Column:
    return table_columns(model, name)[0]


GITHUB_SEARCH = table_columns(GithubItem, "title", "author", "repo_path")

# Per item type: the columns holding its title, subject, or author/sender name.
# Every type also matches on SourceItem.filename.
SEARCH_COLUMNS: dict[str, tuple[Column, ...]] = {
    "mail_message": table_columns(MailMessage, "subject", "sender"),
    "slack_message": table_columns(SlackMessage, "author_name"),
    "beeper_message": table_columns(BeeperMessage, "sender_name"),
    "chat_message": table_columns(ChatMessage, "author"),
    "git_commit": table_columns(GitCommit, "author_name", "repo_path"),
    "comic": table_columns(Comic, "title", "author"),
    "book_section": table_columns(BookSection, "section_title"),
    "blog_post": table_columns(BlogPost, "title", "author"),
    "forum_post": table_columns(ForumPost, "title"),
    "forum_comment": table_columns(ForumComment, "author"),
    "github_item": GITHUB_SEARCH,
    "github_pr": GITHUB_SEARCH,
    "github_issue": GITHUB_SEARCH,
    "note": table_columns(Note, "subject"),
    "report": table_columns(Report, "report_title"),
    "agent_observation": table_columns(AgentObservation, "subject"),
    "google_doc": table_columns(GoogleDoc, "title", "folder_path"),
    "task": table_columns(Task, "task_title"),
    "calendar_event": table_columns(CalendarEvent, "event_title"),
    "meeting": table_columns(Meeting, "title"),
}


@dataclass(frozen=True)
class Connection:
    """Where items of a type came from: the foreign key on the item's table,
    the model it points at, and that model's display name."""

    kind: str
    column: Column
    model: Any
    label: SQLColumnExpression[Any]


GITHUB_CONNECTION = Connection(
    "github_repo",
    table_column(GithubItem, "repo_id"),
    GithubRepo,
    func.concat(GithubRepo.owner, "/", GithubRepo.name),
)

CONNECTIONS: dict[str, Connection] = {
    "mail_message": Connection(
        "email_account",
        table_column(MailMessage, "email_account_id"),
        EmailAccount,
        EmailAccount.email_address,
    ),
    "discord_message": Connection(
        "discord_channel",
        table_column(DiscordMessage, "channel_id"),
        DiscordChannel,
        DiscordChannel.name,
    ),
    "slack_message": Connection(
        "slack_channel",
        table_column(SlackMessage, "channel_id"),
        SlackChannel,
        SlackChannel.name,
    ),
    "beeper_message": Connection(
        "beeper_chat",
        table_column(BeeperMessage, "chat_id"),
        BeeperChat,
        BeeperChat.title,
    ),
    "book_section": Connection(
        "book", table_column(BookSection, "book_id"), Book, Book.title
    ),
    "blog_post": Connection(
        "feed",
        table_column(BlogPost, "feed_id"),
        ArticleFeed,
        func.coalesce(ArticleFeed.title, ArticleFeed.url),
    ),
    "github_item": GITHUB_CONNECTION,
    "github_pr": GITHUB_CONNECTION,
    "github_issue": GITHUB_CONNECTION,
    "google_doc": Connection(
        "google_folder",
        table_column(GoogleDoc, "folder_id"),
        GoogleFolder,
        func.coalesce(GoogleFolder.folder_path, GoogleFolder.folder_name),
    ),
    "calendar_event": Connection(
        "calendar_account",
        table_column(CalendarEvent, "calendar_account_id"),
        CalendarAccount,
        CalendarAccount.name,
    ),
    "meeting": Connection(
        "transcript_account",
        table_column(Meeting, "transcript_account_id"),
        TranscriptAccount,
        TranscriptAccount.name,
    ),
}


def check_embed_statuses(statuses: set[str]) -> None:
    if unknown := set(statuses) - EMBED_STATUSES:
        raise ValueError(
            f"Unknown embed_statuses {sorted(unknown)}; use {sorted(EMBED_STATUSES)}"
        )


def text_match_clause(text: str, item_types: set[str]) -> ColumnElement[bool]:
    """Items whose filename, or title/subject/author column of their own type,
    contains ``text`` (case-insensitive). Only the tables of ``item_types`` are
    searched when given, else every table in :data:`SEARCH_COLUMNS`."""
    pattern = f"%{escape_like(text)}%"
    types = item_types or set(SEARCH_COLUMNS)
    by_table: dict[Any, list[Column]] = {}
    for item_type in sorted(types):
        for column in SEARCH_COLUMNS.get(item_type, ()):
            columns = by_table.setdefault(column.table, [])
            if column not in columns:
                columns.append(column)
    subclass_matches = [
        SourceItem.id.in_(
            select(table.c.id).where(
                or_(*(column.ilike(pattern, escape="\\") for column in columns))
            )
        )
        for table, columns in by_table.items()
    ]
    return or_(SourceItem.filename.ilike(pattern, escape="\\"), *subclass_matches)


def connection_for(item_types: set[str]) -> Connection | None:
    """The connection shared by every type in ``item_types``, if there is one."""
    found = {CONNECTIONS.get(item_type) for item_type in item_types}
    if len(found) != 1:
        return None
    return found.pop()


def connection_value(connection: Connection, connection_id: str) -> Any:
    if connection.column.type.python_type is not int:
        return connection_id
    try:
        return int(connection_id)
    except ValueError:
        raise ValueError(
            f"connection_id must be an integer for {connection.kind}"
        ) from None


def connection_clause(item_types: set[str], connection_id: str) -> ColumnElement[bool]:
    """Items that came from the connection ``connection_id``.

    Raises ValueError unless every type in ``item_types`` shares one connection.
    """
    connection = connection_for(item_types)
    if connection is None:
        raise ValueError(
            "connection_id needs item_types whose items all come from the same kind "
            f"of connection: one of {sorted(CONNECTIONS)}"
        )
    column = connection.column
    return SourceItem.id.in_(
        select(column.table.c.id).where(
            column == connection_value(connection, connection_id)
        )
    )


def connection_counts(
    session: Session, connection: Connection, item_ids: Any
) -> list[dict]:
    """Item counts per connection among ``item_ids`` (a subquery of SourceItem
    ids), largest first. Items whose connection was removed are left out.

    Ids are strings: Discord and Slack ids do not fit a JavaScript number.
    """
    column = connection.column
    counts = (
        session.query(column, func.count())
        .filter(column.table.c.id.in_(item_ids), column.is_not(None))
        .group_by(column)
        .all()
    )
    names = dict(
        session.query(connection.model.id, connection.label)
        .filter(connection.model.id.in_([key for key, _ in counts]))
        .all()
    )
    rows = [
        {"id": str(key), "name": names.get(key), "count": count}
        for key, count in counts
    ]
    return sorted(rows, key=lambda row: (-row["count"], row["name"] or ""))
