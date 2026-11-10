"""
Common calendar utilities for event expansion and querying.
"""

import logging
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import TypedDict

from dateutil.rrule import rrule, rruleset, rrulestr

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

# dateutil walks every occurrence from DTSTART, so a dense rule or an old series
# can mean millions of steps. Each visit costs a few microseconds; this cap keeps
# an event under a quarter second and still covers a daily series 130 years old.
MAX_OCCURRENCES_VISITED = 50_000


def strip_rrule_prefix(rule: str) -> str:
    """The RRULE value without a leading ``RRULE:`` property name.

    Google Calendar lists recurrence as full ``RRULE:...`` lines; CalDAV
    gives the bare value.
    """
    head, sep, rest = rule.partition(":")
    return rest if sep and head.strip().upper() == "RRULE" else rule


def rule_parts(rule: str) -> dict[str, str] | None:
    """Split an RRULE value into upper-cased KEY -> value, or None if malformed.

    Whitespace and ``:`` are refused: rrulestr splits its input on whitespace
    and reads ``NAME:`` as a property, so either could smuggle in a DTSTART,
    EXRULE or RDATE that it would honour.
    """
    rule = rule.strip()
    if ":" in rule or any(c.isspace() for c in rule):
        return None
    pairs = [part.partition("=") for part in rule.split(";") if part]
    if not pairs or any(not sep for _, sep, _ in pairs):
        return None
    return {key.strip().upper(): value.strip() for key, _, value in pairs}


def is_bounded_rule(parts: dict[str, str]) -> bool:
    """Whether dateutil can expand this rule in bounded time.

    INTERVAL=0 makes dateutil loop forever without yielding, which the visit
    cap cannot stop.
    """
    interval = parts.get("INTERVAL", "1")
    return interval.isdigit() and int(interval) >= 1


def with_utc_until(parts: dict[str, str]) -> dict[str, str]:
    """Rewrite a DATE or floating UNTIL as UTC, which dateutil requires with an
    aware DTSTART.

    A DATE UNTIL (all-day series) includes that whole day, so it becomes the
    day's last second; a floating date-time is read as UTC.
    """
    until = parts.get("UNTIL", "")
    if until.isdigit() and len(until) == 8:
        return parts | {"UNTIL": f"{until}T235959Z"}
    if len(until) == 15 and until[8] == "T":
        return parts | {"UNTIL": f"{until}Z"}
    return parts


def occurrences_within(
    rule: rrule | rruleset, start_range: datetime, end_range: datetime, event_id: int
) -> list[datetime]:
    """Occurrences of the rule within the range, visiting at most
    MAX_OCCURRENCES_VISITED from its start."""
    starts: list[datetime] = []
    visited = 0
    for occurrence in islice(rule, MAX_OCCURRENCES_VISITED):
        visited += 1
        if occurrence > end_range:
            return starts
        if occurrence >= start_range:
            starts.append(occurrence)
    if visited == MAX_OCCURRENCES_VISITED:
        logger.warning("Recurrence of calendar event %s hit the expansion cap", event_id)
    return starts


def build_rule(recurrence_rule: str, start_time: datetime) -> rrule | rruleset | None:
    """The parsed rule starting at ``start_time``, or None if it is malformed
    or can't be expanded in bounded time."""
    parts = rule_parts(strip_rrule_prefix(recurrence_rule))
    if parts is None or not is_bounded_rule(parts):
        return None
    rule_text = ";".join(f"{k}={v}" for k, v in with_utc_until(parts).items())
    try:
        return rrulestr(f"RRULE:{rule_text}", dtstart=start_time)
    except RRULE_ERRORS:
        return None


def recurrence_expandable(recurrence_rule: str | None, start_time: datetime) -> bool | None:
    """Whether the rule yields any occurrence at all, or None without a rule.

    A rule no date satisfies (e.g. 30 February) makes dateutil walk to year
    9999 before giving up, which takes seconds. The calendar sync pays that
    once and stores the answer, so queries can skip such events.
    """
    if not recurrence_rule:
        return None
    start = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    rule = build_rule(recurrence_rule, start)
    if rule is None:
        return False
    try:
        return rule.after(start, inc=True) is not None
    except RRULE_ERRORS:
        return False


def occurrence_starts(
    event: CalendarEvent, start_range: datetime, end_range: datetime
) -> list[datetime]:
    """Start times of the event's occurrences within the range.

    A rule that is malformed or too costly to expand yields nothing, with a
    warning naming only the event id.
    """
    rule = build_rule(event.recurrence_rule or "", event.start_time)
    if rule is None:
        logger.warning("Skipping unsupported recurrence rule on calendar event %s", event.id)
        return []
    try:
        return occurrences_within(rule, start_range, end_range, event.id)
    except RRULE_ERRORS:
        logger.warning("Skipping malformed recurrence rule on calendar event %s", event.id)
        return []


def expand_recurring_event(
    event: CalendarEvent,
    start_range: datetime,
    end_range: datetime,
) -> list[tuple[datetime, datetime | None]]:
    """Expand a recurring event into occurrences within the given range.

    Returns list of (start_time, end_time) tuples for each occurrence. An
    event the sync found to have no occurrences at all is skipped unexpanded.
    """
    if not event.recurrence_rule or not event.start_time:
        return []
    if event.recurrence_expandable is False:
        return []

    duration = event.end_time - event.start_time if event.end_time else None
    return [
        (occ_start, occ_start + duration if duration else None)
        for occ_start in occurrence_starts(event, start_range, end_range)
    ]


def expand_or_skip(
    event: CalendarEvent, start_range: datetime, end_range: datetime
) -> list[tuple[datetime, datetime | None]]:
    """expand_recurring_event, where an unexpected error loses only this event.

    The rule is external input, so dateutil may fail in ways RRULE_ERRORS
    does not list.
    """
    try:
        return expand_recurring_event(event, start_range, end_range)
    except Exception:
        logger.exception("Unable to expand recurring calendar event %s", event.id)
        return []


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
        for occ_start, occ_end in expand_or_skip(e, start_date, end_date):
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
