"""Cross-type scoping for item enumeration: a content date range, channel, author, repo.

Each concept lives in a different column per item type (a mail's sent_at, a
GitHub issue's created_at, a Discord message's channel), so each filter is an
OR of per-type id subqueries. The subqueries never correlate with the outer
query, so they compose with any joins the caller already made.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import Float, Text, and_, cast, func, or_, select

from memory.api.search.filters import escape_like
from memory.common.db.models import (
    BlogPost,
    CalendarEvent,
    ChatMessage,
    Comic,
    DiscordChannel,
    DiscordMessage,
    DiscordUser,
    EmailAttachment,
    ForumComment,
    ForumPost,
    GitCommit,
    GithubItem,
    MailMessage,
    SlackChannel,
    SlackMessage,
    SourceItem,
)

# When each type's content happened; types not listed fall back to inserted_at.
CONTENT_DATES = [
    (MailMessage, MailMessage.sent_at),
    (DiscordMessage, DiscordMessage.sent_at),
    (ChatMessage, ChatMessage.sent_at),
    (SlackMessage, func.to_timestamp(cast(SlackMessage.message_ts, Float))),
    (GitCommit, GitCommit.author_date),
    (GithubItem, GithubItem.created_at),
    (BlogPost, BlogPost.published),
    (ForumPost, ForumPost.published_at),
    (ForumComment, ForumComment.posted_at),
    (Comic, Comic.published),
    (EmailAttachment, EmailAttachment.created_at),
    (CalendarEvent, CalendarEvent.start_time),
]


def ids_of(model, *conditions):
    return SourceItem.id.in_(select(model.id).where(*conditions).correlate(None))


def parse_bound(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"{name} must be an ISO date or datetime, got {value!r}") from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def content_date_clause(compare: Callable):
    dated = [
        ids_of(model, compare(func.coalesce(column, model.inserted_at)))
        for model, column in CONTENT_DATES
    ]
    identities = [model.__mapper__.polymorphic_identity for model, _ in CONTENT_DATES]
    undated = and_(
        or_(SourceItem.type.is_(None), SourceItem.type.notin_(identities)),
        compare(SourceItem.inserted_at),
    )
    return or_(*dated, undated)


def channel_clause(channel: str):
    name = channel.lstrip("#").lower()
    return or_(
        ids_of(
            DiscordMessage,
            or_(
                cast(DiscordMessage.channel_id, Text) == channel,
                DiscordMessage.channel_id.in_(
                    select(DiscordChannel.id).where(func.lower(DiscordChannel.name) == name)
                ),
            ),
        ),
        ids_of(
            SlackMessage,
            or_(
                SlackMessage.channel_id == channel,
                SlackMessage.channel_id.in_(
                    select(SlackChannel.id).where(func.lower(SlackChannel.name) == name)
                ),
            ),
        ),
        ids_of(ChatMessage, ChatMessage.channel_id == channel),
    )


def author_clause(author: str):
    lowered = author.lower()
    forum_author = func.unnest(ForumPost.authors).column_valued("name")
    return or_(
        ids_of(
            DiscordMessage,
            or_(
                cast(DiscordMessage.author_id, Text) == author,
                DiscordMessage.author_id.in_(
                    select(DiscordUser.id).where(
                        or_(
                            func.lower(DiscordUser.username) == lowered,
                            func.lower(DiscordUser.display_name) == lowered,
                        )
                    )
                ),
            ),
        ),
        ids_of(
            SlackMessage,
            or_(SlackMessage.author_id == author, func.lower(SlackMessage.author_name) == lowered),
        ),
        ids_of(ChatMessage, func.lower(ChatMessage.author) == lowered),
        ids_of(GithubItem, func.lower(GithubItem.author) == lowered),
        ids_of(BlogPost, func.lower(BlogPost.author) == lowered),
        ids_of(Comic, func.lower(Comic.author) == lowered),
        ids_of(
            ForumPost,
            select(forum_author)
            .where(func.lower(forum_author) == lowered)
            .exists(),
        ),
        ids_of(
            GitCommit,
            or_(
                func.lower(GitCommit.author_name) == lowered,
                func.lower(GitCommit.author_email) == lowered,
            ),
        ),
        # Sender headers mix display names and addresses, so match a substring.
        ids_of(MailMessage, MailMessage.sender.ilike(f"%{escape_like(author)}%", escape="\\")),
    )


def repo_clause(repo: str):
    lowered = repo.lower()
    return or_(
        ids_of(GithubItem, func.lower(GithubItem.repo_path) == lowered),
        ids_of(GitCommit, func.lower(GitCommit.repo_path) == lowered),
    )


def apply_scope_filters(
    query,
    *,
    since: str | None = None,
    until: str | None = None,
    channel: str | None = None,
    author: str | None = None,
    repo: str | None = None,
):
    if since:
        start = parse_bound("since", since)
        query = query.filter(content_date_clause(lambda column: column >= start))
    if until:
        end = parse_bound("until", until)
        # A bare date means through the end of that day.
        if len(until) == len("YYYY-MM-DD"):
            end += timedelta(days=1) - timedelta(microseconds=1)
        query = query.filter(content_date_clause(lambda column: column <= end))
    if channel:
        query = query.filter(channel_clause(channel))
    if author:
        query = query.filter(author_clause(author))
    if repo:
        query = query.filter(repo_clause(repo))
    return query
