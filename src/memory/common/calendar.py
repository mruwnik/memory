"""
Common calendar utilities for event expansion and querying.
"""

import logging
import signal
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Iterator, NamedTuple, TypedDict

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
# can mean millions of steps. This cap still covers a daily series 130 years old.
MAX_OCCURRENCES_VISITED = 50_000
# A visit costs microseconds when the rule's filters match most periods, but
# dateutil steps through every period between matches. Rules whose day filters
# skip most days under DAILY or HOURLY are rewritten (see coarsened), and this
# bounds what remains: past it, an event keeps the occurrences found so far.
EXPANSION_BUDGET_SECONDS = 0.25
# Shared by every recurring event one query expands; events left when it is
# spent are skipped.
QUERY_EXPANSION_BUDGET_SECONDS = 2.0
# Day filters that can leave months or years between matching days.
SPARSE_DAY_PARTS = ("BYMONTHDAY", "BYYEARDAY", "BYWEEKNO")
# Under these frequencies a rule that matches once a day costs dateutil tens
# of thousands of periods per occurrence. Calendars don't send them (Google
# refuses them), so rules using them are refused.
REFUSED_FREQUENCIES = frozenset({"SECONDLY", "MINUTELY"})
# The rule parts RFC 5545 defines. dateutil also reads extensions such as
# BYEASTER, which no calendar sends; rules naming anything else are refused.
RULE_PARTS = frozenset({
    "FREQ", "UNTIL", "COUNT", "INTERVAL", "BYSECOND", "BYMINUTE", "BYHOUR", "BYDAY",
    "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYMONTH", "BYSETPOS", "WKST",
})
FREQUENCIES = frozenset({"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"})
# The calendar sync times each rule's expansion from its start to this far past
# now and stores the cost; a rule over the per-event budget is stored as not
# expandable, so queries only expand rules proven cheap, cheapest first.
RECURRENCE_PROOF_HORIZON = timedelta(days=366)
RECURRENCE_PROOF_BUDGET_SECONDS = EXPANSION_BUDGET_SECONDS
# A hard limit on the whole sync-time check: dateutil can step through periods
# for minutes without yielding, where no budget check between yields runs.
RECURRENCE_CHECK_TIME_LIMIT_SECONDS = 1.0
# Under these a BYSETPOS past the period's set size matches nothing, and
# dateutil steps hour by hour or day by day to year 9999 (minutes).
SETPOS_REFUSED_FREQUENCIES = frozenset({"HOURLY", "DAILY"})


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
    EXRULE or RDATE that it would honour. So is a part RFC 5545 doesn't define,
    a repeated part, and a missing or unknown FREQ.
    """
    rule = rule.strip()
    if ":" in rule or any(c.isspace() for c in rule):
        return None
    pairs = [part.partition("=") for part in rule.split(";") if part]
    if not pairs or any(not sep for _, sep, _ in pairs):
        return None
    parts = {key.upper(): value for key, _, value in pairs}
    if len(parts) != len(pairs) or not parts.keys() <= RULE_PARTS:
        return None
    if parts.get("FREQ", "").upper() not in FREQUENCIES:
        return None
    return parts


def is_bounded_rule(parts: dict[str, str]) -> bool:
    """Whether dateutil can expand this rule in bounded time.

    INTERVAL=0 makes dateutil loop forever without yielding, which the visit
    cap cannot stop. A sub-minute or minute frequency is refused, see
    REFUSED_FREQUENCIES, and so is BYSETPOS under HOURLY or DAILY, see
    SETPOS_REFUSED_FREQUENCIES.
    """
    interval = parts.get("INTERVAL", "1")
    freq = parts.get("FREQ", "").upper()
    if freq in REFUSED_FREQUENCIES:
        return False
    if "BYSETPOS" in parts and freq in SETPOS_REFUSED_FREQUENCIES:
        return False
    return interval.isdigit() and int(interval) >= 1


def coarsened(parts: dict[str, str]) -> dict[str, str] | None:
    """The rule under the coarsest FREQ that yields the same occurrences, or
    None if it can't be rewritten and is too costly as given.

    HOURLY, DAILY or WEEKLY with BYMONTHDAY, BYYEARDAY or BYWEEKNO makes
    dateutil step through every hour or day between matching days, which can be
    years, or to year 9999 when no day matches. With INTERVAL=1 every matching
    day is an occurrence, so the same set comes from YEARLY with the same
    filters (and, for HOURLY, every hour of the day unless BYHOUR names them),
    which steps a year at a time. INTERVAL above 1, BYSETPOS, an ordinal BYDAY
    (e.g. 1MO), or WEEKLY without BYDAY (which means DTSTART's weekday) mean
    something else under YEARLY, so those rules are refused.
    """
    freq = parts.get("FREQ", "").upper()
    if freq not in ("HOURLY", "DAILY", "WEEKLY"):
        return parts
    if not any(p in parts for p in SPARSE_DAY_PARTS):
        return parts
    interval = parts.get("INTERVAL", "1")
    weekdays = [day.strip() for day in parts.get("BYDAY", "").split(",") if day.strip()]
    if not interval.isdigit() or int(interval) != 1 or "BYSETPOS" in parts:
        return None
    if any(not day.isalpha() for day in weekdays) or (freq == "WEEKLY" and not weekdays):
        return None
    every_hour = {"BYHOUR": ",".join(map(str, range(24)))}
    hours = every_hour if freq == "HOURLY" and "BYHOUR" not in parts else {}
    return parts | {"FREQ": "YEARLY"} | hours


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
    rule: rrule | rruleset,
    start_range: datetime,
    end_range: datetime,
    event_id: int,
    deadline: float | None = None,
) -> list[datetime]:
    """Occurrences of the rule within the range, visiting at most
    MAX_OCCURRENCES_VISITED from its start and stopping once
    EXPANSION_BUDGET_SECONDS have passed or at ``deadline`` (time.monotonic)."""
    starts: list[datetime] = []
    own_deadline = time.monotonic() + EXPANSION_BUDGET_SECONDS
    deadline = own_deadline if deadline is None else min(deadline, own_deadline)
    # One past the cap, to tell a series that ends there from one cut short.
    for visited, occurrence in enumerate(islice(rule, MAX_OCCURRENCES_VISITED + 1), start=1):
        if occurrence > end_range:
            return starts
        if visited > MAX_OCCURRENCES_VISITED:
            logger.warning("Recurrence of calendar event %s hit the expansion cap", event_id)
            return starts
        if time.monotonic() > deadline:
            logger.warning("Recurrence of calendar event %s ran out of time", event_id)
            return starts
        if occurrence >= start_range:
            starts.append(occurrence)
    return starts


def build_rule(recurrence_rule: str, start_time: datetime) -> rrule | rruleset | None:
    """The parsed rule starting at ``start_time``, or None if it is malformed
    or can't be expanded in bounded time."""
    parts = rule_parts(strip_rrule_prefix(recurrence_rule))
    if parts is None or not is_bounded_rule(parts):
        return None
    parts = coarsened(parts)
    if parts is None:
        return None
    rule_text = ";".join(f"{k}={v}" for k, v in with_utc_until(parts).items())
    try:
        return rrulestr(f"RRULE:{rule_text}", dtstart=start_time)
    except RRULE_ERRORS:
        return None


def expansion_seconds(rule: rrule | rruleset, start: datetime) -> float:
    """How long walking the rule from its start to RECURRENCE_PROOF_HORIZON past
    now takes, visiting at most MAX_OCCURRENCES_VISITED occurrences and stopping
    once past RECURRENCE_PROOF_BUDGET_SECONDS. Time spent between occurrences
    counts."""
    horizon = max(datetime.now(timezone.utc), start) + RECURRENCE_PROOF_HORIZON
    began = time.monotonic()
    deadline = began + RECURRENCE_PROOF_BUDGET_SECONDS
    for occurrence in islice(rule, MAX_OCCURRENCES_VISITED):
        if occurrence > horizon or time.monotonic() > deadline:
            break
    return time.monotonic() - began


class CheckTimedOut(Exception):
    """Raised by interrupt_after when its time is up."""


@contextmanager
def interrupt_after(seconds: float) -> Iterator[None]:
    """Raise CheckTimedOut in the block once ``seconds`` have passed.

    Uses SIGALRM, so it interrupts only on the main thread (as in a Celery
    prefork worker); elsewhere the block runs unbounded. A timer already set
    stops the block too if it is due sooner, and is restored afterwards with
    its remaining time.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def expire(signum, frame):
        raise CheckTimedOut

    began = time.monotonic()
    outer_delay, outer_interval = signal.getitimer(signal.ITIMER_REAL)
    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, outer_delay or seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if outer_delay:
            remaining = max(outer_delay - (time.monotonic() - began), 1e-6)
            signal.setitimer(signal.ITIMER_REAL, remaining, outer_interval)


class RecurrenceCheck(NamedTuple):
    """What the calendar sync stores about an event's rule; Nones without a rule."""

    expandable: bool | None
    cost_ms: float | None


def check_recurrence(recurrence_rule: str | None, start_time: datetime) -> RecurrenceCheck:
    """Whether queries may expand the rule, and what expanding it costs.

    The rule must yield an occurrence at all and expand within
    RECURRENCE_PROOF_BUDGET_SECONDS (see expansion_seconds). A rule no date
    satisfies (e.g. 30 February) makes dateutil walk to year 9999 before giving
    up, which takes seconds. The calendar sync pays this once and stores the
    answer, so queries skip such events and expand the cheapest first. The
    whole check stops after RECURRENCE_CHECK_TIME_LIMIT_SECONDS (see
    interrupt_after), and a rule that runs out of time or fails in any way is
    not expandable.
    """
    if not recurrence_rule:
        return RecurrenceCheck(None, None)
    start = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    try:
        with interrupt_after(RECURRENCE_CHECK_TIME_LIMIT_SECONDS):
            return measure_recurrence(recurrence_rule, start)
    except CheckTimedOut:
        logger.warning("Recurrence rule check ran out of time")
    except RRULE_ERRORS:
        pass
    except Exception:
        logger.exception("Recurrence rule check failed")
    return RecurrenceCheck(False, None)


def measure_recurrence(recurrence_rule: str, start: datetime) -> RecurrenceCheck:
    """check_recurrence without its time limit; ``start`` must be aware."""
    rule = build_rule(recurrence_rule, start)
    if rule is None or rule.after(start, inc=True) is None:
        return RecurrenceCheck(False, None)
    seconds = expansion_seconds(rule, start)
    return RecurrenceCheck(seconds <= RECURRENCE_PROOF_BUDGET_SECONDS, seconds * 1000)


def recurrence_expandable(recurrence_rule: str | None, start_time: datetime) -> bool | None:
    """check_recurrence's verdict alone."""
    return check_recurrence(recurrence_rule, start_time).expandable


def occurrence_starts(
    event: CalendarEvent,
    start_range: datetime,
    end_range: datetime,
    deadline: float | None = None,
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
        return occurrences_within(rule, start_range, end_range, event.id, deadline)
    except RRULE_ERRORS:
        logger.warning("Skipping malformed recurrence rule on calendar event %s", event.id)
        return []


def expand_recurring_event(
    event: CalendarEvent,
    start_range: datetime,
    end_range: datetime,
    deadline: float | None = None,
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
        for occ_start in occurrence_starts(event, start_range, end_range, deadline)
    ]


def expand_or_skip(
    event: CalendarEvent,
    start_range: datetime,
    end_range: datetime,
    deadline: float | None = None,
) -> list[tuple[datetime, datetime | None]]:
    """expand_recurring_event, where an unexpected error loses only this event.

    The rule is external input, so dateutil may fail in ways RRULE_ERRORS
    does not list.
    """
    try:
        return expand_recurring_event(event, start_range, end_range, deadline)
    except Exception:
        logger.exception("Unable to expand recurring calendar event %s", event.id)
        return []


Occurrence = tuple[CalendarEvent, datetime, datetime | None]


def by_account(events: list[CalendarEvent]) -> list[list[CalendarEvent]]:
    """The events the sync didn't refuse, grouped by calendar account, each
    group ordered by the expansion cost the sync measured, cheapest first, with
    events not checked yet last."""
    groups: dict[int | None, list[CalendarEvent]] = {}
    for event in events:
        if event.recurrence_expandable is not False:
            groups.setdefault(event.calendar_account_id, []).append(event)
    return [
        sorted(group, key=lambda event: (event.recurrence_cost_ms is None, event.recurrence_cost_ms or 0.0))
        for _, group in sorted(groups.items(), key=lambda item: item[0] or 0)
    ]


def expand_until(
    events: list[CalendarEvent], start_range: datetime, end_range: datetime, deadline: float
) -> tuple[list[Occurrence], list[int]]:
    """Occurrences of ``events`` in order until ``deadline``, and the ids of the
    events left unexpanded."""
    occurrences: list[Occurrence] = []
    for index, event in enumerate(events):
        if time.monotonic() > deadline:
            return occurrences, [e.id for e in events[index:]]
        occurrences.extend(
            (event, start, end)
            for start, end in expand_or_skip(event, start_range, end_range, deadline)
        )
    return occurrences, []


def expand_within_budget(
    events: list[CalendarEvent], start_range: datetime, end_range: datetime
) -> list[Occurrence]:
    """(event, start, end) for each occurrence of the recurring events in the range,
    within QUERY_EXPANSION_BUDGET_SECONDS.

    Each calendar account gets an equal share of what is left when its turn
    comes, so one account's events can't use up another's time. Events left
    once their account's share is spent are skipped, with a warning naming
    their ids.
    """
    deadline = time.monotonic() + QUERY_EXPANSION_BUDGET_SECONDS
    groups = by_account(events)
    occurrences: list[Occurrence] = []
    skipped: list[int] = []
    for index, group in enumerate(groups):
        now = time.monotonic()
        share = now + (deadline - now) / (len(groups) - index)
        found, left = expand_until(group, start_range, end_range, share)
        occurrences += found
        skipped += left
    if skipped:
        logger.warning(
            "Calendar query ran out of time; skipped %d recurring events, first ids %s",
            len(skipped),
            skipped[:20],
        )
    return occurrences


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
    for e, occ_start, occ_end in expand_within_budget(recurring, start_date, end_date):
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
