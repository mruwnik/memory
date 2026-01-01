"""Tests for common calendar utilities."""

import pytest
from datetime import datetime, timedelta, timezone

from memory.common.calendar import (
    expand_recurring_event,
    event_to_dict,
    get_events_in_range,
    parse_date_range,
    EventDict,
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

    events = get_events_in_range(db_session, start, end)

    assert len(events) == 1
    assert events[0]["event_title"] == "Team Meeting"


def test_get_events_in_range_excludes_out_of_range(db_session, simple_event):
    """Test that events outside range are excluded."""
    start = datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 2, 28, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end)

    assert len(events) == 0


def test_get_events_in_range_expands_recurring(db_session, recurring_event):
    """Test that recurring events are expanded."""
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end)

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

    events = get_events_in_range(db_session, start, end)

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

    events = get_events_in_range(db_session, start, end)

    # Verify sorted order
    times = [e["start_time"] for e in events]
    assert times == sorted(times)


def test_get_events_in_range_respects_limit(db_session, recurring_event):
    """Test that limit parameter is respected."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end, limit=3)

    assert len(events) == 3


def test_get_events_in_range_empty_database(db_session, calendar_account):
    """Test with no events in database."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    events = get_events_in_range(db_session, start, end)

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

    events = get_events_in_range(db_session, start, end)

    # Should have occurrences on Mondays: 1st, 8th, 15th = 3
    assert len(events) >= 2
    for event in events:
        assert event["end_time"] is None
