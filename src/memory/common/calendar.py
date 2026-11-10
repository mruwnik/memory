"""
Common calendar utilities for event expansion and querying.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from dateutil.rrule import rrulestr

from memory.common.access_control import AccessFilter, apply_access_filter_to_query
from memory.common.dates import parse_iso_datetime_utc
from memory.common.db.connection import DBSession
from memory.common.db.models import CalendarEvent
from memory.common.db.models.sources import CalendarAccount

logger = logging.getLogger(__name__)


class EventDict(TypedDict):
    id: int
    event_title: str
    start_time: str
    end_time: str | None
    all_day: bool
    location: str | None
    calendar_name: str | None
    recurrence_rule: str | None
    calendar_account_id: int | None
    attendees: list[str] | None
    meeting_link: str | None


# Recurrence rules come from external calendars, so a malformed one must only
# lose its own event. These are what dateutil raises on bad rule text.
RRULE_ERRORS = (ValueError, TypeError, OverflowError, KeyError)

# dateutil walks every occurrence from DTSTART, so sub-hourly rules on an old
# series take minutes. No calendar app creates them.
UNBOUNDED_FREQS = frozenset({"SECONDLY", "MINUTELY"})


def rule_parts(rule: str) -> dict[str, str] | None:
    """Split an RRULE value into upper-cased KEY -> value, or None if malformed.

    Multi-line text is refused: it could smuggle in a DTSTART or other
    properties that rrulestr would honour.
    """
    if "\n" in rule or "\r" in rule:
        return None
    pairs = [part.partition("=") for part in rule.split(";") if part]
    if not pairs or any(not sep for _, sep, _ in pairs):
        return None
    return {key.strip().upper(): value.strip() for key, _, value in pairs}


def is_bounded_rule(parts: dict[str, str]) -> bool:
    """Whether dateutil can expand this rule in bounded time.

    INTERVAL=0 makes dateutil loop forever.
    """
    if parts.get("FREQ", "").upper() in UNBOUNDED_FREQS:
        return False
    interval = parts.get("INTERVAL", "1")
    return interval.isdigit() and int(interval) >= 1


def occurrence_starts(
    event: CalendarEvent, start_range: datetime, end_range: datetime
) -> list[datetime]:
    """Start times of the event's occurrences within the range.

    A rule that is malformed or too costly to expand yields nothing, with a
    warning naming only the event id.
    """
    rule_text = event.recurrence_rule or ""
    parts = rule_parts(rule_text)
    if parts is None or not is_bounded_rule(parts):
        logger.warning("Skipping unsupported recurrence rule on calendar event %s", event.id)
        return []
    try:
        rule = rrulestr(f"RRULE:{rule_text}", dtstart=event.start_time)
        return list(rule.between(start_range, end_range, inc=True))
    except RRULE_ERRORS:
        logger.warning("Skipping malformed recurrence rule on calendar event %s", event.id)
        return []


def expand_recurring_event(
    event: CalendarEvent,
    start_range: datetime,
    end_range: datetime,
) -> list[tuple[datetime, datetime | None]]:
    """Expand a recurring event into occurrences within the given range.

    Returns list of (start_time, end_time) tuples for each occurrence.
    """
    if not event.recurrence_rule or not event.start_time:
        return []

    duration = event.end_time - event.start_time if event.end_time else None
    return [
        (occ_start, occ_start + duration if duration else None)
        for occ_start in occurrence_starts(event, start_range, end_range)
    ]


def event_to_dict(
    event: CalendarEvent,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> EventDict:
    """Convert a CalendarEvent to a dictionary.

    If start_time/end_time are provided, they override the event's times
    (used for recurring event occurrences).
    """
    st = start_time or event.start_time
    et = end_time or event.end_time

    # Extract attendees and meeting_link from event_metadata
    metadata = event.event_metadata or {}
    attendees = metadata.get("attendees")
    meeting_link = metadata.get("meeting_link")

    return EventDict(
        id=event.id,  # type: ignore
        event_title=event.event_title or "",  # type: ignore
        start_time=st.isoformat() if st else "",
        end_time=et.isoformat() if et else None,
        all_day=event.all_day or False,  # type: ignore
        location=event.location,  # type: ignore
        calendar_name=event.calendar_name,  # type: ignore
        recurrence_rule=event.recurrence_rule,  # type: ignore
        calendar_account_id=event.calendar_account_id,  # type: ignore
        attendees=attendees,
        meeting_link=meeting_link,
    )


def get_events_in_range(
    session: DBSession,
    start_date: datetime,
    end_date: datetime,
    limit: int = 200,
    user_ids: list[int] | None = None,
    *,
    access_filter: AccessFilter | None,
) -> list[EventDict]:
    """Get all calendar events (including expanded recurring) in a date range.

    Args:
        session: Database session
        start_date: Start of the date range (inclusive)
        end_date: End of the date range (inclusive)
        limit: Maximum number of events to return
        user_ids: If provided, only return events from calendars owned by these users
        access_filter: The caller's access filter; None means no filtering
            (admins only)

    Returns:
        List of event dictionaries, sorted by start_time

    Note (operator-visible breaking change):
        Pre-migration ``20260507_calendar_account_user_id``, this filter
        returned every CalDAV event to every user. The new filter scopes by
        ``CalendarAccount.user_id``; legacy CalDAV rows with NULL user_id
        are excluded from non-admin views ("my CalDAV events disappeared"
        on upgrade). Admins can reassign each row via
        ``PATCH /calendar-accounts/{id}`` to restore visibility.
    """
    # Build base query with access and optional user filtering
    def apply_user_filter(query):  # type: ignore[no-untyped-def]
        query = apply_access_filter_to_query(query, access_filter)
        if user_ids is None:
            return query
        # Filter by CalendarAccount.user_id (set on creation; backfilled for
        # legacy Gmail-linked rows). Legacy CalDAV rows with NULL user_id are
        # excluded — admins can reassign ownership to make them visible.
        return (
            query
            .join(CalendarAccount, CalendarEvent.calendar_account_id == CalendarAccount.id)
            .filter(CalendarAccount.user_id.in_(user_ids))
        )

    # Get non-recurring events in range
    non_recurring_query = session.query(CalendarEvent).filter(
        CalendarEvent.start_time >= start_date,
        CalendarEvent.start_time <= end_date,
        CalendarEvent.recurrence_rule.is_(None),
    )
    non_recurring = apply_user_filter(non_recurring_query).all()

    # Get all recurring events (they might have occurrences in range)
    recurring_query = session.query(CalendarEvent).filter(
        CalendarEvent.recurrence_rule.isnot(None)
    )
    recurring = apply_user_filter(recurring_query).all()

    results: list[tuple[datetime, EventDict]] = []

    # Add non-recurring events
    for e in non_recurring:
        if e.start_time:
            results.append((e.start_time, event_to_dict(e)))

    # Expand recurring events
    for e in recurring:
        for occ_start, occ_end in expand_recurring_event(e, start_date, end_date):
            results.append((occ_start, event_to_dict(e, occ_start, occ_end)))

    # Sort by start time and apply limit
    results.sort(key=lambda x: x[0])
    return [r[1] for r in results[:limit]]


def parse_date_range(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 7,
) -> tuple[datetime, datetime]:
    """Parse date range from string inputs.

    Args:
        start_date: ISO format start date (defaults to now); naive values are UTC
        end_date: ISO format end date (defaults to start + days); naive values are UTC
        days: Number of days if end_date not specified

    Returns:
        Tuple of (start_datetime, end_datetime)

    Raises:
        ValueError: If date format is invalid
    """
    if start_date:
        range_start = parse_iso_datetime_utc(start_date)
        if range_start is None:
            raise ValueError(f"Invalid start_date format: {start_date}")
    else:
        range_start = datetime.now(timezone.utc)

    if end_date:
        range_end = parse_iso_datetime_utc(end_date)
        if range_end is None:
            raise ValueError(f"Invalid end_date format: {end_date}")
    else:
        range_end = range_start + timedelta(days=days)

    return range_start, range_end
