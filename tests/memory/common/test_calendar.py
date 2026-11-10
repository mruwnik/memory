"""Tests for common calendar utilities."""

import signal

import pytest
from datetime import datetime, timedelta, timezone

from memory.common import calendar
from memory.common.calendar import (
    MAX_OCCURRENCES_VISITED,
    expand_recurring_event,
    event_to_dict,
    get_events_in_range,
    parse_date_range,
)
from memory.common.db.models import CalendarEvent
from memory.common.db.models.sources import CalendarAccount


@pytest.fixture
def calendar_account(db_session) -> CalendarAccount:
    """Create a calendar account for testing."""
    account = CalendarAccount(
        name="Test Calendar",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="testuser",
        caldav_password="testpass",
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def simple_event(db_session, calendar_account) -> CalendarEvent:
    """Create a simple non-recurring event."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"0" * 32,
        event_title="Team Meeting",
        start_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
        all_day=False,
        location="Conference Room A",
        calendar_name="Work",
        calendar_account_id=calendar_account.id,
        recurrence_rule=None,
    )
    db_session.add(event)
    db_session.commit()
    return event


@pytest.fixture
def all_day_event(db_session, calendar_account) -> CalendarEvent:
    """Create an all-day event."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"1" * 32,
        event_title="Holiday",
        start_time=datetime(2024, 1, 20, 0, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 21, 0, 0, 0, tzinfo=timezone.utc),
        all_day=True,
        location=None,
        calendar_name="Holidays",
        calendar_account_id=calendar_account.id,
        recurrence_rule=None,
    )
    db_session.add(event)
    db_session.commit()
    return event


@pytest.fixture
def recurring_event(db_session, calendar_account) -> CalendarEvent:
    """Create a recurring event (daily on weekdays)."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"2" * 32,
        event_title="Daily Standup",
        start_time=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 1, 9, 15, 0, tzinfo=timezone.utc),
        all_day=False,
        location="Zoom",
        calendar_name="Work",
        calendar_account_id=calendar_account.id,
        recurrence_rule="FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
    )
    db_session.add(event)
    db_session.commit()
    return event


@pytest.fixture
def weekly_recurring_event(db_session, calendar_account) -> CalendarEvent:
    """Create a weekly recurring event."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"3" * 32,
        event_title="Weekly Review",
        start_time=datetime(2024, 1, 5, 14, 0, 0, tzinfo=timezone.utc),  # Friday
        end_time=datetime(2024, 1, 5, 15, 0, 0, tzinfo=timezone.utc),
        all_day=False,
        location=None,
        calendar_name="Work",
        calendar_account_id=calendar_account.id,
        recurrence_rule="FREQ=WEEKLY;BYDAY=FR",
    )
    db_session.add(event)
    db_session.commit()
    return event


# =============================================================================
# Tests for parse_date_range
# =============================================================================


def test_parse_date_range_with_both_dates():
    """Test parsing with both start and end date provided."""
    start, end = parse_date_range("2024-01-15", "2024-01-20")

    assert start.year == 2024
    assert start.month == 1
    assert start.day == 15
    assert end.day == 20


def test_parse_date_range_with_iso_format():
    """Test parsing with full ISO format."""
    start, end = parse_date_range(
        "2024-01-15T10:00:00Z",
        "2024-01-20T18:00:00Z"
    )

    assert start.hour == 10
    assert end.hour == 18


def test_parse_date_range_with_timezone():
    """Test parsing with timezone offset."""
    start, end = parse_date_range(
        "2024-01-15T10:00:00+00:00",
        "2024-01-20T18:00:00+00:00"
    )

    assert start.tzinfo is not None
    assert end.tzinfo is not None


def test_parse_date_range_defaults_to_now():
    """Test that start defaults to now when not provided."""
    before = datetime.now(timezone.utc)
    start, end = parse_date_range(None, None, days=7)
    after = datetime.now(timezone.utc)

    assert before <= start <= after
    assert end > start


def test_parse_date_range_uses_days():
    """Test that days parameter is used for end date."""
    start, end = parse_date_range("2024-01-15", None, days=10)

    assert start.day == 15
    expected_end = start + timedelta(days=10)
    assert end.day == expected_end.day


@pytest.mark.parametrize(
    "start_date, end_date",
    [
        ("2024-01-15", "2024-01-20"),
        ("2024-01-15T09:00:00", None),
        ("2024-01-15", "2024-01-20T18:00:00Z"),
    ],
)
def test_parse_date_range_naive_input_is_utc(start_date, end_date):
    start, end = parse_date_range(start_date, end_date)

    assert (start.tzinfo, end.tzinfo) == (timezone.utc, timezone.utc)


def test_get_events_in_range_date_only_range_expands_recurring(
    db_session, recurring_event
):
    start, end = parse_date_range("2024-01-15", "2024-01-17")

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert [e["start_time"] for e in events] == [
        "2024-01-15T09:00:00+00:00",
        "2024-01-16T09:00:00+00:00",
    ]


def test_parse_date_range_invalid_start_date():
    """Test error on invalid start date."""
    with pytest.raises(ValueError, match="Invalid start_date"):
        parse_date_range("not-a-date", None)


def test_parse_date_range_invalid_end_date():
    """Test error on invalid end date."""
    with pytest.raises(ValueError, match="Invalid end_date"):
        parse_date_range("2024-01-15", "not-a-date")


# =============================================================================
# Tests for expand_recurring_event
# =============================================================================


def test_expand_recurring_event_daily(recurring_event):
    """Test expanding a daily recurring event."""
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 23, 59, 59, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(recurring_event, start, end)

    # Mon-Fri should give us 5 occurrences
    assert len(occurrences) == 5

    # Check first occurrence
    first_start, first_end = occurrences[0]
    assert first_start.day == 15
    assert first_start.hour == 9
    assert first_end is not None
    assert first_end.hour == 9
    assert first_end.minute == 15


def test_expand_recurring_event_weekly(weekly_recurring_event):
    """Test expanding a weekly recurring event."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(weekly_recurring_event, start, end)

    # January has 4-5 Fridays: 5th, 12th, 19th, 26th = 4 occurrences
    assert len(occurrences) >= 4

    # All should be Fridays
    for occ_start, _ in occurrences:
        assert occ_start.weekday() == 4  # Friday


def test_expand_recurring_event_preserves_duration(recurring_event):
    """Test that expansion preserves event duration."""
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 16, 23, 59, 59, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(recurring_event, start, end)

    for occ_start, occ_end in occurrences:
        assert occ_end is not None
        duration = occ_end - occ_start
        assert duration == timedelta(minutes=15)


def test_expand_recurring_event_non_recurring_returns_empty(simple_event):
    """Test that non-recurring events return empty list."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(simple_event, start, end)

    assert occurrences == []


def test_expand_recurring_event_no_start_time():
    """Test handling event without start time."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"x" * 32,
        event_title="No Start",
        start_time=None,
        recurrence_rule="FREQ=DAILY",
    )

    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(event, start, end)

    assert occurrences == []


def test_expand_recurring_event_invalid_rule():
    """Test handling invalid recurrence rule."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"y" * 32,
        event_title="Bad Rule",
        start_time=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        recurrence_rule="INVALID_RULE",
    )

    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    # Should return empty list, not raise
    occurrences = expand_recurring_event(event, start, end)
    assert occurrences == []


class DeadlineExceeded(BaseException):
    """A BaseException, so no ``except Exception`` in the code under test hides it."""


def run_with_deadline(seconds: int):
    """Fail the test, rather than hang, if it runs past ``seconds``."""

    def expire(signum, frame):
        raise DeadlineExceeded("recurrence expansion did not finish")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(seconds)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def deadline():
    yield from run_with_deadline(5)


@pytest.fixture
def short_deadline():
    """One second: far above a skipped event's cost, below one expanded to year 9999."""
    yield from run_with_deadline(1)


NEVER_MATCHING_RULES = [
    "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30",
    "FREQ=MINUTELY;BYMONTH=4;BYMONTHDAY=31",
]


@pytest.mark.parametrize("rule", NEVER_MATCHING_RULES)
def test_get_events_in_range_skips_unexpandable_event_quickly(
    db_session, calendar_account, recurring_event, rule, short_deadline
):
    for n in range(3):
        db_session.add(
            CalendarEvent(
                modality="calendar",
                sha256=bytes([n]) * 32,
                event_title="Never",
                start_time=datetime(2020, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
                calendar_account_id=calendar_account.id,
                recurrence_rule=rule,
                recurrence_expandable=False,
            )
        )
    db_session.commit()
    start = datetime(2024, 1, 15, tzinfo=timezone.utc)
    end = datetime(2024, 1, 17, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert [e["event_title"] for e in events] == ["Daily Standup", "Daily Standup"]


@pytest.mark.parametrize(
    "rule, expandable",
    [
        (None, None),
        ("FREQ=DAILY;BYDAY=MO", True),
        ("FREQ=DAILY;UNTIL=20240101", True),
        *((rule, False) for rule in NEVER_MATCHING_RULES),
        ("FREQ=DAILY;INTERVAL=0", False),
        ("FREQ=BOGUS", False),
    ],
)
def test_recurrence_expandable(rule, expandable):
    start = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert calendar.recurrence_expandable(rule, start) is expandable


def test_recurrence_expandable_reads_naive_start_as_utc():
    assert calendar.recurrence_expandable(
        "FREQ=DAILY;UNTIL=20240105T000000Z", datetime(2024, 1, 1, 9, 0, 0)
    ) is True


MALFORMED_RULES = [
    "FREQ=DAILY;BYDAY=XX",
    "FREQ=DAILY\nDTSTART:20200101T000000",
    "FREQ=DAILY\r\nDTSTART:20200101T000000",
    "FREQ=BOGUS",
    "FREQ",
    ";",
    "FREQ=DAILY;BYHOUR=99",
    "FREQ=DAILY;UNTIL=99999999T999999Z",
    "FREQ=DAILY;BYSETPOS=0",
    "FREQ=DAILY;INTERVAL=0",
    "FREQ=DAILY;INTERVAL=-1",
    "FREQ=DAILY;INTERVAL=x",
    "FREQ=DAILY DTSTART:20200101T000000",
    "FREQ=DAILY\tDTSTART:20200101T000000",
    "FREQ=DAILY DTSTART:20240102T090000Z",
    "FREQ=DAILY\tDTSTART:20240102T090000Z",
    "FREQ=DAILY;BYDAY=MO TU",
    "FREQ=DAILY;X:DTSTART=20200101T000000",
    "FREQ=DAILY;EXRULE:FREQ=WEEKLY",
    "FREQ=DAILY;RDATE:20240102T090000Z",
]


@pytest.mark.parametrize("rule", MALFORMED_RULES)
def test_expand_recurring_event_malformed_rule_yields_nothing(rule, deadline):
    event = CalendarEvent(
        modality="calendar",
        sha256=b"m" * 32,
        event_title="Bad rule",
        start_time=datetime(2020, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        recurrence_rule=rule,
    )
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)

    assert expand_recurring_event(event, start, end) == []


@pytest.mark.parametrize("rule", MALFORMED_RULES)
def test_get_events_in_range_malformed_rule_spares_siblings(
    db_session, calendar_account, recurring_event, rule, deadline
):
    db_session.add(
        CalendarEvent(
            modality="calendar",
            sha256=b"m" * 32,
            event_title="Bad rule",
            start_time=datetime(2020, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
            calendar_account_id=calendar_account.id,
            recurrence_rule=rule,
        )
    )
    db_session.commit()
    start = datetime(2024, 1, 15, tzinfo=timezone.utc)
    end = datetime(2024, 1, 17, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert [e["event_title"] for e in events] == ["Daily Standup", "Daily Standup"]


@pytest.mark.parametrize("rule", ["FREQ=BOGUS", "FREQ=DAILY;INTERVAL=0"])
def test_expand_recurring_event_logs_only_event_id(
    db_session, calendar_account, rule, caplog, deadline
):
    event = CalendarEvent(
        modality="calendar",
        sha256=b"l" * 32,
        event_title="Secret title",
        start_time=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        calendar_account_id=calendar_account.id,
        recurrence_rule=rule,
    )
    db_session.add(event)
    db_session.commit()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)

    expand_recurring_event(event, start, end)

    messages = [r.getMessage() for r in caplog.records]
    assert [(r.levelname, str(event.id) in r.getMessage()) for r in caplog.records] == [
        ("WARNING", True)
    ]
    assert not any(rule in m or "Secret" in m for m in messages)


@pytest.mark.parametrize(
    "rule", ["RRULE:FREQ=DAILY", "rrule:FREQ=DAILY", " RRULE :FREQ=DAILY"]
)
def test_expand_recurring_event_accepts_rrule_prefix(rule):
    event = CalendarEvent(
        modality="calendar",
        sha256=b"g" * 32,
        event_title="Google series",
        start_time=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        recurrence_rule=rule,
    )
    start = datetime(2024, 1, 10, tzinfo=timezone.utc)
    end = datetime(2024, 1, 12, tzinfo=timezone.utc)

    starts = [s for s, _ in expand_recurring_event(event, start, end)]

    assert starts == [
        datetime(2024, 1, 10, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 11, 9, 0, 0, tzinfo=timezone.utc),
    ]


@pytest.mark.parametrize(
    "until, expected_days",
    [
        ("20240117", [15, 16, 17]),
        ("20240117T000000", [15, 16, 17]),
        ("20240116T235959Z", [15, 16]),
        ("20240116T235959", [15, 16]),
    ],
)
def test_expand_recurring_event_until_without_utc(until, expected_days):
    event = CalendarEvent(
        modality="calendar",
        sha256=b"u" * 32,
        event_title="All-day series",
        start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 16, tzinfo=timezone.utc),
        all_day=True,
        recurrence_rule=f"FREQ=DAILY;UNTIL={until}",
    )
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)

    starts = [s for s, _ in expand_recurring_event(event, start, end)]

    assert starts == [datetime(2024, 1, d, tzinfo=timezone.utc) for d in expected_days]


EVERY = ",".join(str(n) for n in range(60))


@pytest.mark.parametrize(
    "rule, series_start, expected",
    [
        # Millions of occurrences inside the range
        (
            f"FREQ=HOURLY;BYMINUTE={EVERY};BYSECOND={EVERY}",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            MAX_OCCURRENCES_VISITED,
        ),
        # Millions of occurrences before the range
        ("FREQ=SECONDLY", datetime(2000, 1, 1, tzinfo=timezone.utc), 0),
        (
            f"FREQ=DAILY;BYHOUR=0,6,12,18;BYMINUTE={EVERY}",
            datetime(1990, 1, 1, tzinfo=timezone.utc),
            0,
        ),
    ],
)
def test_expand_recurring_event_caps_occurrences_visited(
    rule, series_start, expected, deadline, caplog
):
    event = CalendarEvent(
        modality="calendar",
        sha256=b"d" * 32,
        event_title="Dense series",
        start_time=series_start,
        recurrence_rule=rule,
    )
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 2, 1, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(event, start, end)

    assert len(occurrences) == expected
    assert [r.levelname for r in caplog.records] == ["WARNING"]


@pytest.mark.parametrize(
    "count, expected, warnings",
    [
        # A series that ends exactly at the cap was expanded in full
        (5, 5, []),
        (6, 5, ["WARNING"]),
    ],
)
def test_expansion_cap_warns_only_when_occurrences_remain(
    monkeypatch, caplog, count, expected, warnings
):
    monkeypatch.setattr(calendar, "MAX_OCCURRENCES_VISITED", 5)
    event = CalendarEvent(
        modality="calendar",
        sha256=b"c" * 32,
        event_title="Counted series",
        start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        recurrence_rule=f"FREQ=DAILY;COUNT={count}",
    )
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 2, 1, tzinfo=timezone.utc)

    occurrences = expand_recurring_event(event, start, end)

    assert len(occurrences) == expected
    assert [r.levelname for r in caplog.records] == warnings


def test_get_events_in_range_unexpected_error_spares_siblings(
    db_session, calendar_account, recurring_event, monkeypatch, caplog
):
    broken = CalendarEvent(
        modality="calendar",
        sha256=b"b" * 32,
        event_title="Broken",
        start_time=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        calendar_account_id=calendar_account.id,
        recurrence_rule="FREQ=WEEKLY",
    )
    db_session.add(broken)
    db_session.commit()
    real_expand = calendar.expand_recurring_event

    def explode(*args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        calendar,
        "expand_recurring_event",
        lambda event, *rng: {broken.id: explode}.get(event.id, real_expand)(
            event, *rng
        ),
    )
    start = datetime(2024, 1, 15, tzinfo=timezone.utc)
    end = datetime(2024, 1, 17, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert [e["event_title"] for e in events] == ["Daily Standup", "Daily Standup"]
    assert [r.getMessage() for r in caplog.records] == [
        f"Unable to expand recurring calendar event {broken.id}"
    ]


def test_expand_recurring_event_huge_count_is_valid(deadline):
    event = CalendarEvent(
        modality="calendar",
        sha256=b"c" * 32,
        event_title="Long series",
        start_time=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        recurrence_rule="FREQ=DAILY;COUNT=99999999999999999999999",
    )
    start = datetime(2024, 1, 10, tzinfo=timezone.utc)
    end = datetime(2024, 1, 12, tzinfo=timezone.utc)

    starts = [s for s, _ in expand_recurring_event(event, start, end)]

    assert starts == [
        datetime(2024, 1, 10, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 11, 9, 0, 0, tzinfo=timezone.utc),
    ]


# =============================================================================
# Tests for event_to_dict
# =============================================================================


def test_event_to_dict_basic(simple_event):
    """Test converting event to dict."""
    result = event_to_dict(simple_event)

    assert result["id"] == simple_event.id
    assert result["event_title"] == "Team Meeting"
    assert result["location"] == "Conference Room A"
    assert result["calendar_name"] == "Work"
    assert result["all_day"] is False
    assert result["recurrence_rule"] is None
    assert result["start_time"] is not None
    assert result["end_time"] is not None
    assert "2024-01-15" in result["start_time"]
    assert "2024-01-15" in result["end_time"]


def test_event_to_dict_all_day(all_day_event):
    """Test converting all-day event."""
    result = event_to_dict(all_day_event)

    assert result["all_day"] is True
    assert result["event_title"] == "Holiday"


def test_event_to_dict_with_override_times(simple_event):
    """Test overriding times for recurring occurrences."""
    override_start = datetime(2024, 2, 15, 10, 0, 0, tzinfo=timezone.utc)
    override_end = datetime(2024, 2, 15, 11, 0, 0, tzinfo=timezone.utc)

    result = event_to_dict(simple_event, override_start, override_end)

    assert result["start_time"] is not None
    assert result["end_time"] is not None
    assert "2024-02-15" in result["start_time"]
    assert "2024-02-15" in result["end_time"]


def test_event_to_dict_no_end_time():
    """Test event without end time."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"z" * 32,
        event_title="Open-ended",
        start_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        end_time=None,
        all_day=False,
    )

    result = event_to_dict(event)

    assert result["end_time"] is None


# =============================================================================
# Tests for get_events_in_range
# =============================================================================


def test_get_events_in_range_simple(db_session, simple_event):
    """Test fetching non-recurring events in range."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert len(events) == 1
    assert events[0]["event_title"] == "Team Meeting"


def test_get_events_in_range_excludes_out_of_range(db_session, simple_event):
    """Test that events outside range are excluded."""
    start = datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 2, 28, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert len(events) == 0


def test_get_events_in_range_expands_recurring(db_session, recurring_event):
    """Test that recurring events are expanded."""
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    # 5 weekdays
    assert len(events) == 5
    for event in events:
        assert event["event_title"] == "Daily Standup"


def test_get_events_in_range_mixed_events(
    db_session, simple_event, all_day_event, recurring_event
):
    """Test fetching mix of recurring and non-recurring."""
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 21, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    titles = [e["event_title"] for e in events]
    assert "Team Meeting" in titles
    assert "Holiday" in titles
    assert "Daily Standup" in titles


def test_get_events_in_range_sorted_by_start_time(
    db_session, simple_event, recurring_event
):
    """Test that events are sorted by start time."""
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 16, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    # Verify sorted order
    times = [e["start_time"] for e in events]
    assert times == sorted(times)


def test_get_events_in_range_respects_limit(db_session, recurring_event):
    """Test that limit parameter is respected."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, limit=3, access_filter=None)

    assert len(events) == 3


def test_get_events_in_range_empty_database(db_session, calendar_account):
    """Test with no events in database."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    assert events == []


def test_get_events_in_range_recurring_no_end_time(db_session, calendar_account):
    """Test recurring event without end time."""
    event = CalendarEvent(
        modality="calendar",
        sha256=b"4" * 32,
        event_title="All Day Recurring",
        start_time=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        end_time=None,
        all_day=True,
        calendar_account_id=calendar_account.id,
        recurrence_rule="FREQ=WEEKLY;BYDAY=MO",
    )
    db_session.add(event)
    db_session.commit()

    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 15, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, access_filter=None)

    # Should have occurrences on Mondays: 1st, 8th, 15th = 3
    assert len(events) >= 2
    for event in events:
        assert event["end_time"] is None
