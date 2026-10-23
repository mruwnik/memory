"""Resolve a date range to the source items it covers.

Each content type has its own notion of *when* it happened (an email's send
time, a calendar event's start, a forum post's publication), which is usually
not when it was ingested: backfills insert months of history at once. The
range is matched against that per-type event time and resolved to source ids,
which both the Qdrant and BM25 arms already filter on (the Qdrant payload has
no indexed timestamp to range over).
"""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Float, cast, func, select, union
from sqlalchemy.orm import Session

from memory.common.access_control import AccessFilter, apply_access_filter_to_query
from memory.common.db.models import (
    BlogPost,
    CalendarEvent,
    ChatMessage,
    Comic,
    DiscordMessage,
    ForumPost,
    GitCommit,
    GithubItem,
    GoogleDoc,
    MailMessage,
    Meeting,
    Photo,
    SessionSegment,
    SlackMessage,
    SourceItem,
    Task,
)

# Item timestamps are UTC but the user's day is not, so whole-day bounds are
# widened enough to cover any timezone.
DAY_SLACK = timedelta(hours=12)

EVENT_TIMES = {
    MailMessage: MailMessage.__table__.c.sent_at,
    ChatMessage: ChatMessage.__table__.c.sent_at,
    DiscordMessage: DiscordMessage.__table__.c.sent_at,
    SlackMessage: func.to_timestamp(cast(SlackMessage.__table__.c.message_ts, Float)),
    GitCommit: GitCommit.__table__.c.author_date,
    GithubItem: GithubItem.__table__.c.created_at,
    Photo: Photo.__table__.c.exif_taken_at,
    Comic: Comic.__table__.c.published,
    BlogPost: BlogPost.__table__.c.published,
    ForumPost: ForumPost.__table__.c.published_at,
    GoogleDoc: GoogleDoc.__table__.c.google_modified_at,
    CalendarEvent: CalendarEvent.__table__.c.start_time,
    Meeting: Meeting.__table__.c.meeting_date,
    SessionSegment: SessionSegment.__table__.c.start_time,
    Task: Task.__table__.c.due_date,
}
# Types without an event time match on inserted_at. Tasks match on either:
# "tasks from this week" means created or due then.
INSERTED_AT_TYPES_EXCLUDED = [
    model.__mapper__.polymorphic_identity for model in EVENT_TIMES if model is not Task
]


def date_bounds(
    start: date | None, end: date | None
) -> tuple[datetime | None, datetime | None]:
    """Inclusive whole-day bounds for a date range, widened by DAY_SLACK."""
    lower = datetime.combine(start, time.min, timezone.utc) - DAY_SLACK if start else None
    upper = (
        datetime.combine(end + timedelta(days=1), time.min, timezone.utc) + DAY_SLACK
        if end
        else None
    )
    return lower, upper


def in_range(column, start: datetime | None, end: datetime | None) -> list:
    return [
        *([column >= start] if start else []),
        *([column <= end] if end else []),
    ]


def source_ids_in_range(
    db: Session,
    start: datetime | None,
    end: datetime | None,
    access_filter: AccessFilter | None,
) -> list[int]:
    """Ids of source items the caller can see whose event time is within [start, end].

    Filtering by access here keeps the "nothing in range" fallback from
    depending on other users' content.
    """
    if start is None and end is None:
        raise ValueError("source_ids_in_range needs at least one bound")

    by_event_time = [
        select(model.__table__.c.id).where(*in_range(column, start, end))
        for model, column in EVENT_TIMES.items()
    ]
    by_inserted_at = select(SourceItem.id).where(
        SourceItem.type.notin_(INSERTED_AT_TYPES_EXCLUDED),
        *in_range(SourceItem.inserted_at, start, end),
    )
    in_window = union(*by_event_time, by_inserted_at).subquery()
    query = db.query(SourceItem.id).filter(SourceItem.id.in_(select(in_window.c.id)))
    return [source_id for (source_id,) in apply_access_filter_to_query(query, access_filter)]
