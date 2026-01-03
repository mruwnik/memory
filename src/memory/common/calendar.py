"""
Common calendar utilities for event expansion and querying.
"""

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from dateutil.rrule import rrulestr
from sqlalchemy.orm import Session

from memory.common.db.models import CalendarEvent


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

    try:
        rule = rrulestr(
            f"RRULE:{event.recurrence_rule}",
            dtstart=event.start_time,
        )

        duration = None
        if event.end_time and event.start_time:
            duration = event.end_time - event.start_time

        occurrences = []
        for occ_start in rule.between(start_range, end_range, inc=True):
            occ_end = occ_start + duration if duration else None
            occurrences.append((occ_start, occ_end))

        return occurrences
    except Exception:
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
    session: Session,
    start_date: datetime,
    end_date: datetime,
    limit: int = 200,
) -> list[EventDict]:
    """Get all calendar events (including expanded recurring) in a date range.

    Args:
        session: Database session
        start_date: Start of the date range (inclusive)
        end_date: End of the date range (inclusive)
        limit: Maximum number of events to return

    Returns:
        List of event dictionaries, sorted by start_time
    """
    # Get non-recurring events in range
    non_recurring = (
        session.query(CalendarEvent)
        .filter(
            CalendarEvent.start_time >= start_date,
            CalendarEvent.start_time <= end_date,
            CalendarEvent.recurrence_rule.is_(None),
        )
        .all()
    )

    # Get all recurring events (they might have occurrences in range)
    recurring = (
        session.query(CalendarEvent)
        .filter(CalendarEvent.recurrence_rule.isnot(None))
        .all()
    )

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
        start_date: ISO format start date (defaults to now)
        end_date: ISO format end date (defaults to start + days)
        days: Number of days if end_date not specified

    Returns:
        Tuple of (start_datetime, end_datetime)

    Raises:
        ValueError: If date format is invalid
    """
    if start_date:
        try:
            range_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid start_date format: {start_date}")
    else:
        range_start = datetime.now(timezone.utc)

    if end_date:
        try:
            range_end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid end_date format: {end_date}")
    else:
        range_end = range_start + timedelta(days=days)

    return range_start, range_end
