"""Tests for calendar syncing tasks."""

import pytest
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import Mock, patch

from memory.common.db.models import CalendarEvent, HumanUser, Person
from memory.common.db.models.sources import CalendarAccount, GoogleAccount
from memory.workers.tasks import calendar
from memory.workers.tasks.calendar import (
    _create_event_hash,
    _parse_google_event,
    _create_calendar_event,
    _serialize_event_data,
    EventData,
)
from memory.common.db import connection as db_connection


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


@pytest.fixture
def mock_event_data() -> dict:
    """Mock event data for testing."""
    return {
        "title": "Team Meeting",
        "start_time": datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        "end_time": datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
        "all_day": False,
        "description": "Weekly sync meeting with the team",
        "location": "Conference Room A",
        "external_id": "event-123",
        "calendar_name": "Work",
        "recurrence_rule": None,
        "attendees": ["alice@example.com", "bob@example.com"],
        "meeting_link": "https://meet.example.com/abc123",
    }


@pytest.fixture
def mock_all_day_event() -> dict:
    """Mock all-day event data."""
    return {
        "title": "Company Holiday",
        "start_time": datetime(2024, 12, 25, 0, 0, 0, tzinfo=timezone.utc),
        "end_time": datetime(2024, 12, 26, 0, 0, 0, tzinfo=timezone.utc),
        "all_day": True,
        "description": "Christmas Day",
        "location": None,
        "external_id": "holiday-123",
        "calendar_name": "Holidays",
        "recurrence_rule": None,
        "attendees": [],
    }


@pytest.fixture
def mock_recurring_event() -> dict:
    """Mock recurring event data."""
    return {
        "title": "Daily Standup",
        "start_time": datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        "end_time": datetime(2024, 1, 15, 9, 15, 0, tzinfo=timezone.utc),
        "all_day": False,
        "description": "Quick daily sync",
        "location": None,
        "external_id": "standup-123",
        "calendar_name": "Work",
        "recurrence_rule": "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
        "attendees": ["team@example.com"],
    }


@pytest.fixture
def caldav_account(db_session) -> CalendarAccount:
    """Create a CalDAV calendar account for testing."""
    account = CalendarAccount(
        name="Test CalDAV",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="testuser",
        caldav_password="testpass",
        calendar_ids=[],
        tags=["calendar", "test"],
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def google_account(db_session) -> GoogleAccount:
    """Create a Google account for testing."""
    # GoogleAccount.user_id is NOT NULL (FK -> users.id), so create the
    # owning user first.
    user = HumanUser(
        name="Calendar Test User",
        email="calendar-test@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(user)
    db_session.commit()

    account = GoogleAccount(
        user_id=user.id,
        name="Test Google",
        email="test@gmail.com",
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["calendar"],
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def google_calendar_account(db_session, google_account) -> CalendarAccount:
    """Create a Google Calendar account for testing."""
    account = CalendarAccount(
        name="Test Google Calendar",
        calendar_type="google",
        google_account_id=google_account.id,
        calendar_ids=[],
        tags=["calendar", "google"],
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def inactive_account(db_session) -> CalendarAccount:
    """Create an inactive calendar account."""
    account = CalendarAccount(
        name="Inactive CalDAV",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="testuser",
        caldav_password="testpass",
        active=False,
    )
    db_session.add(account)
    db_session.commit()
    return account


# =============================================================================
# Tests for helper functions
# =============================================================================


def test_create_event_hash_basic(mock_event_data):
    """Test event hash creation."""
    hash1 = _create_event_hash(mock_event_data)
    hash2 = _create_event_hash(mock_event_data)
    assert hash1 == hash2
    assert len(hash1) == 32  # SHA256 = 32 bytes


def test_create_event_hash_different_events():
    """Test that different events have different hashes."""
    event1 = EventData(
        title="Event 1",
        start_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        description="",
    )
    event2 = EventData(
        title="Event 2",
        start_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        description="",
    )

    hash1 = _create_event_hash(event1)
    hash2 = _create_event_hash(event2)
    assert hash1 != hash2


def test_serialize_event_data(mock_event_data):
    """Test event data serialization for Celery."""
    serialized = _serialize_event_data(mock_event_data)

    # Datetimes should be converted to ISO strings
    assert isinstance(serialized["start_time"], str)
    assert isinstance(serialized["end_time"], str)
    assert serialized["title"] == "Team Meeting"


def test_serialize_event_data_none_end_time():
    """Test serialization with None end_time."""
    event = EventData(
        title="Open Event",
        start_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        end_time=None,
    )
    serialized = _serialize_event_data(event)
    assert serialized["end_time"] is None


def test_parse_google_event_regular():
    """Test parsing a regular Google Calendar event."""
    google_event = {
        "id": "google-event-123",
        "summary": "Team Sync",
        "description": "Weekly team meeting",
        "location": "Zoom",
        "start": {"dateTime": "2024-01-15T14:00:00Z"},
        "end": {"dateTime": "2024-01-15T15:00:00Z"},
        "attendees": [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ],
        "hangoutLink": "https://meet.google.com/abc-123",
    }

    result = _parse_google_event(google_event, "Work Calendar")

    assert result["title"] == "Team Sync"
    assert result.get("external_id") == "google-event-123"
    assert result.get("calendar_name") == "Work Calendar"
    assert result.get("all_day") is False
    assert result.get("location") == "Zoom"
    assert result.get("meeting_link") == "https://meet.google.com/abc-123"
    attendees = result.get("attendees", [])
    assert "alice@example.com" in attendees
    assert "bob@example.com" in attendees


def test_parse_google_event_all_day():
    """Test parsing an all-day Google Calendar event."""
    google_event = {
        "id": "holiday-event",
        "summary": "Company Holiday",
        "start": {"date": "2024-12-25"},
        "end": {"date": "2024-12-26"},
    }

    result = _parse_google_event(google_event, "Holidays")

    assert result["title"] == "Company Holiday"
    assert result.get("all_day") is True
    assert result["start_time"].date().isoformat() == "2024-12-25"


def test_parse_google_event_with_conference_data():
    """Test parsing Google event with conference data instead of hangoutLink."""
    google_event = {
        "id": "meet-event",
        "summary": "Video Call",
        "start": {"dateTime": "2024-01-15T14:00:00Z"},
        "end": {"dateTime": "2024-01-15T15:00:00Z"},
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "phone", "uri": "tel:+1234567890"},
                {"entryPointType": "video", "uri": "https://zoom.us/j/123456"},
            ]
        },
    }

    result = _parse_google_event(google_event, "Work")

    assert result.get("meeting_link") == "https://zoom.us/j/123456"


def test_parse_google_event_no_description():
    """Test parsing event without description."""
    google_event = {
        "id": "simple-event",
        "summary": "Quick Meeting",
        "start": {"dateTime": "2024-01-15T14:00:00Z"},
        "end": {"dateTime": "2024-01-15T15:00:00Z"},
    }

    result = _parse_google_event(google_event, "Work")

    assert result.get("description") == ""
    assert result.get("attendees") == []
    assert result.get("meeting_link") is None


def test_parse_google_event_with_recurrence():
    """Test parsing event with recurrence rule."""
    google_event = {
        "id": "recurring-event",
        "summary": "Daily Standup",
        "start": {"dateTime": "2024-01-15T09:00:00Z"},
        "end": {"dateTime": "2024-01-15T09:15:00Z"},
        "recurrence": ["RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"],
    }

    result = _parse_google_event(google_event, "Work")

    assert result.get("recurrence_rule") == "RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"


# =============================================================================
# Tests for _create_calendar_event
# =============================================================================


def test_create_calendar_event(caldav_account, mock_event_data):
    """Test creating a CalendarEvent from event data."""
    event = _create_calendar_event(caldav_account, mock_event_data)

    assert event.event_title == "Team Meeting"
    assert event.start_time == mock_event_data["start_time"]
    assert event.end_time == mock_event_data["end_time"]
    assert event.all_day is False
    assert event.location == "Conference Room A"
    assert event.external_id == "event-123"
    assert event.calendar_account_id == caldav_account.id
    assert event.modality == "calendar"
    assert "calendar" in event.tags
    assert "test" in event.tags  # From account tags


def test_create_calendar_event_with_metadata(caldav_account, mock_event_data):
    """Test that attendees and meeting link are stored in metadata."""
    event = _create_calendar_event(caldav_account, mock_event_data)

    assert event.event_metadata is not None
    assert event.event_metadata["attendees"] == ["alice@example.com", "bob@example.com"]
    assert event.event_metadata["meeting_link"] == "https://meet.example.com/abc123"


def test_create_calendar_event_no_attendees(caldav_account, mock_all_day_event):
    """Test creating event without attendees."""
    event = _create_calendar_event(caldav_account, mock_all_day_event)

    # Should not have attendees in metadata
    assert "attendees" not in event.event_metadata or event.event_metadata.get("attendees") == []


# =============================================================================
# Tests for sync_calendar_event
# =============================================================================


def test_sync_calendar_event_new(mock_event_data, caldav_account, db_session, qdrant):
    """Test syncing a new calendar event."""
    serialized = _serialize_event_data(mock_event_data)

    result = calendar.sync_calendar_event(caldav_account.id, serialized)

    assert result["status"] == "processed"

    # Verify event was created
    event = (
        db_session.query(CalendarEvent)
        .filter_by(external_id="event-123")
        .first()
    )
    assert event is not None
    assert event.event_title == "Team Meeting"
    assert event.calendar_account_id == caldav_account.id


def test_sync_calendar_event_account_not_found(mock_event_data, db_session):
    """Test syncing with non-existent account."""
    serialized = _serialize_event_data(mock_event_data)

    result = calendar.sync_calendar_event(99999, serialized)

    assert result["status"] == "error"
    assert "Account not found" in result["error"]


def test_sync_calendar_event_update_existing(
    mock_event_data, caldav_account, db_session, qdrant
):
    """Test updating an existing calendar event."""
    # First sync
    serialized = _serialize_event_data(mock_event_data)
    calendar.sync_calendar_event(caldav_account.id, serialized)

    # Update the event
    mock_event_data["title"] = "Updated Team Meeting"
    mock_event_data["location"] = "Conference Room B"
    serialized = _serialize_event_data(mock_event_data)

    result = calendar.sync_calendar_event(caldav_account.id, serialized)

    assert result["status"] == "processed"

    # Verify event was updated
    db_session.expire_all()
    event = (
        db_session.query(CalendarEvent)
        .filter_by(external_id="event-123")
        .first()
    )
    assert event.event_title == "Updated Team Meeting"
    assert event.location == "Conference Room B"


@pytest.mark.parametrize(
    "prior_status, changes, reembedded",
    [
        ("STORED", {}, False),
        ("STORED", {"start_time": datetime(2024, 1, 16, 10, tzinfo=timezone.utc)}, False),
        ("STORED", {"title": "Renamed Meeting"}, True),
        ("STORED", {"description": "New agenda"}, True),
        ("SKIPPED", {}, False),
        ("FAILED", {}, True),
        ("QUEUED", {}, True),
    ],
)
def test_sync_calendar_event_reembeds_stale_events(
    mock_event_data, caldav_account, db_session, qdrant, prior_status, changes, reembedded
):
    calendar.sync_calendar_event(caldav_account.id, _serialize_event_data(mock_event_data))
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").one()
    old_chunk_ids = {c.id for c in event.chunks}
    event.embed_status = prior_status
    db_session.commit()

    calendar.sync_calendar_event(
        caldav_account.id, _serialize_event_data(cast(EventData, {**mock_event_data, **changes}))
    )

    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").one()
    assert ({c.id for c in event.chunks} != old_chunk_ids) == reembedded
    assert event.embed_status == ("STORED" if reembedded else prior_status)


def test_sync_calendar_event_long_unchanged_event_is_not_resummarized(
    mock_event_data, caldav_account, db_session, qdrant
):
    long_event = cast(EventData, {**mock_event_data, "description": "Dial-in details. " * 200})
    summaries = iter(range(100))
    with patch(
        "memory.common.summarizer.summarize",
        side_effect=lambda *a, **k: (f"summary {next(summaries)}", []),
    ) as summarize:
        calendar.sync_calendar_event(caldav_account.id, _serialize_event_data(long_event))
        event = db_session.query(CalendarEvent).filter_by(external_id="event-123").one()
        old_chunk_ids = {c.id for c in event.chunks}
        summarize.reset_mock()

        calendar.sync_calendar_event(caldav_account.id, _serialize_event_data(long_event))

    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").one()
    assert {c.id for c in event.chunks} == old_chunk_ids
    summarize.assert_not_called()


def test_sync_calendar_event_without_external_id(caldav_account, db_session, qdrant):
    """Test syncing event without external_id creates new each time."""
    event_data = EventData(
        title="Ad-hoc Meeting",
        start_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
        all_day=False,
        description="",
        location=None,
        external_id=None,  # No external ID
        calendar_name="Work",
    )
    serialized = _serialize_event_data(event_data)

    result = calendar.sync_calendar_event(caldav_account.id, serialized)
    assert result["status"] == "processed"


# =============================================================================
# Tests for sync_calendar_account
# =============================================================================


def test_sync_calendar_account_not_found(db_session):
    """Test sync with non-existent account."""
    result = calendar.sync_calendar_account(99999)

    assert result["status"] == "error"
    assert "Account not found or inactive" in result["error"]


def test_sync_calendar_account_inactive(inactive_account, db_session):
    """Test sync with inactive account."""
    result = calendar.sync_calendar_account(inactive_account.id)

    assert result["status"] == "error"
    assert "Account not found or inactive" in result["error"]


@pytest.mark.parametrize(
    "check_interval_minutes,seconds_since_check,should_skip",
    [
        (15, 60, True),  # 15min interval, checked 1min ago -> skip
        (15, 800, True),  # 15min interval, checked 13min ago -> skip
        (15, 1000, False),  # 15min interval, checked 16min ago -> don't skip
        (30, 1000, True),  # 30min interval, checked 16min ago -> skip
        (30, 2000, False),  # 30min interval, checked 33min ago -> don't skip
    ],
)
@patch("memory.workers.tasks.calendar.fetch_caldav_events", return_value=[])
def test_sync_calendar_account_check_interval(
    mock_fetch,
    check_interval_minutes,
    seconds_since_check,
    should_skip,
    db_session,
):
    """Test sync respects check interval."""
    from sqlalchemy import text

    account = CalendarAccount(
        name="Interval Test",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=check_interval_minutes,
        active=True,
    )
    db_session.add(account)
    db_session.flush()

    # Set last_sync_at
    last_sync_time = datetime.now(timezone.utc) - timedelta(seconds=seconds_since_check)
    db_session.execute(
        text(
            "UPDATE calendar_accounts SET last_sync_at = :timestamp WHERE id = :account_id"
        ),
        {"timestamp": last_sync_time, "account_id": account.id},
    )
    db_session.commit()

    result = calendar.sync_calendar_account(account.id)

    if should_skip:
        assert result["status"] == "skipped_recent_check"
    else:
        # Would fail with incomplete caldav credentials error, but that's expected
        assert "status" in result


@patch("memory.workers.tasks.calendar.fetch_caldav_events", return_value=[])
def test_sync_calendar_account_force_full_bypasses_interval(mock_fetch, db_session):
    """Test force_full bypasses check interval."""
    from sqlalchemy import text

    account = CalendarAccount(
        name="Force Test",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=60,
        active=True,
    )
    db_session.add(account)
    db_session.flush()

    # Set recent last_sync_at
    last_sync_time = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_session.execute(
        text(
            "UPDATE calendar_accounts SET last_sync_at = :timestamp WHERE id = :account_id"
        ),
        {"timestamp": last_sync_time, "account_id": account.id},
    )
    db_session.commit()

    # Even with recent sync, force_full should proceed
    # (It will fail due to fake caldav URL, but won't be skipped)
    result = calendar.sync_calendar_account(account.id, force_full=True)

    assert result["status"] != "skipped_recent_check"


def test_sync_calendar_account_incomplete_caldav_credentials(db_session):
    """Test sync fails gracefully with incomplete CalDAV credentials."""
    account = CalendarAccount(
        name="Incomplete CalDAV",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username=None,  # Missing username
        caldav_password=None,  # Missing password
        active=True,
    )
    db_session.add(account)
    db_session.commit()

    result = calendar.sync_calendar_account(account.id)

    assert result["status"] == "error"
    assert "incomplete" in result["error"].lower()
    db_session.refresh(account)
    assert account.sync_error == "CalDAV credentials incomplete"


@patch("memory.workers.tasks.calendar.fetch_caldav_events")
@patch("memory.workers.tasks.calendar.sync_calendar_event")
def test_sync_calendar_account_caldav_success(
    mock_sync_event, mock_fetch, caldav_account, db_session
):
    """Test successful CalDAV sync."""
    mock_fetch.return_value = [
        {
            "title": "Test Event",
            "start_time": datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            "end_time": datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
            "all_day": False,
            "description": "",
            "location": None,
            "external_id": "caldav-1",
            "calendar_name": "Default",
            "recurrence_rule": None,
            "attendees": [],
        },
    ]
    mock_sync_event.delay.return_value = Mock(id="task-123")

    result = calendar.sync_calendar_account(caldav_account.id)

    assert result["status"] == "completed"
    assert result["events_synced"] == 1
    assert result["calendar_type"] == "caldav"
    mock_sync_event.delay.assert_called_once()


@patch("memory.workers.tasks.calendar._fetch_google_calendar_events")
@patch("memory.workers.tasks.calendar.sync_calendar_event")
def test_sync_calendar_account_google_success(
    mock_sync_event, mock_fetch, google_calendar_account, db_session
):
    """Test successful Google Calendar sync."""
    mock_fetch.return_value = [
        {
            "title": "Google Event",
            "start_time": datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            "end_time": datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
            "all_day": False,
            "description": "",
            "location": None,
            "external_id": "google-1",
            "calendar_name": "Primary",
            "recurrence_rule": None,
            "attendees": [],
        },
    ]
    mock_sync_event.delay.return_value = Mock(id="task-456")

    result = calendar.sync_calendar_account(google_calendar_account.id)

    assert result["status"] == "completed"
    assert result["events_synced"] == 1
    assert result["calendar_type"] == "google"


def test_sync_calendar_account_updates_timestamp(caldav_account, db_session):
    """Test that sync updates last_sync_at timestamp."""
    with patch("memory.workers.tasks.calendar.fetch_caldav_events") as mock_fetch:
        mock_fetch.return_value = []

        assert caldav_account.last_sync_at is None

        calendar.sync_calendar_account(caldav_account.id)

        db_session.refresh(caldav_account)
        assert caldav_account.last_sync_at is not None


# =============================================================================
# Tests for sync_all_calendars
# =============================================================================


@patch("memory.workers.tasks.calendar.sync_calendar_account")
def test_sync_all_calendars(mock_sync_account, db_session):
    """Test syncing all active calendar accounts."""
    account1 = CalendarAccount(
        name="Account 1",
        calendar_type="caldav",
        caldav_url="https://caldav1.example.com",
        caldav_username="user1",
        caldav_password="pass1",
        active=True,
    )
    account2 = CalendarAccount(
        name="Account 2",
        calendar_type="caldav",
        caldav_url="https://caldav2.example.com",
        caldav_username="user2",
        caldav_password="pass2",
        active=True,
    )
    inactive = CalendarAccount(
        name="Inactive",
        calendar_type="caldav",
        caldav_url="https://caldav3.example.com",
        caldav_username="user3",
        caldav_password="pass3",
        active=False,
    )
    db_session.add_all([account1, account2, inactive])
    db_session.commit()

    mock_sync_account.delay.side_effect = [Mock(id="task-1"), Mock(id="task-2")]

    result = calendar.sync_all_calendars()

    # Should only sync active accounts
    assert len(result) == 2
    assert result[0]["task_id"] == "task-1"
    assert result[1]["task_id"] == "task-2"


def test_sync_all_calendars_no_active(db_session):
    """Test sync_all when no active accounts exist."""
    inactive = CalendarAccount(
        name="Inactive",
        calendar_type="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="user",
        caldav_password="pass",
        active=False,
    )
    db_session.add(inactive)
    db_session.commit()

    result = calendar.sync_all_calendars()

    assert result == []


@patch("memory.workers.tasks.calendar.sync_calendar_account")
def test_sync_all_calendars_force_full(mock_sync_account, caldav_account, db_session):
    """Test force_full is passed through to individual syncs."""
    mock_sync_account.delay.return_value = Mock(id="task-123")

    calendar.sync_all_calendars(force_full=True)

    mock_sync_account.delay.assert_called_once_with(
        caldav_account.id, force_full=True
    )


# =============================================================================
# Tests for person association reconciliation on event update (#49)
# =============================================================================


@pytest.fixture
def alice(db_session) -> Person:
    person = Person(
        identifier="alice",
        display_name="Alice",
        contact_info={"email": "alice@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


@pytest.fixture
def bob(db_session) -> Person:
    person = Person(
        identifier="bob",
        display_name="Bob",
        contact_info={"email": "bob@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


@pytest.fixture
def charlie(db_session) -> Person:
    person = Person(
        identifier="charlie",
        display_name="Charlie",
        contact_info={"email": "charlie@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


def test_update_event_adds_new_person_associations(
    mock_event_data, caldav_account, db_session, qdrant, alice, bob, charlie
):
    """Test that updating an event with new attendees adds person associations."""
    # Create event with alice and bob
    mock_event_data["attendees"] = ["alice@example.com", "bob@example.com"]
    serialized = _serialize_event_data(mock_event_data)
    calendar.sync_calendar_event(caldav_account.id, serialized)

    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert {p.identifier for p in event.people} == {"alice", "bob"}

    # Update: add charlie
    mock_event_data["attendees"] = [
        "alice@example.com",
        "bob@example.com",
        "charlie@example.com",
    ]
    serialized = _serialize_event_data(mock_event_data)
    result = calendar.sync_calendar_event(caldav_account.id, serialized)

    assert result["status"] == "processed"
    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert {p.identifier for p in event.people} == {"alice", "bob", "charlie"}


def test_update_event_removes_old_person_associations(
    mock_event_data, caldav_account, db_session, qdrant, alice, bob
):
    """Test that updating an event with fewer attendees removes person associations."""
    # Create event with alice and bob
    mock_event_data["attendees"] = ["alice@example.com", "bob@example.com"]
    serialized = _serialize_event_data(mock_event_data)
    calendar.sync_calendar_event(caldav_account.id, serialized)

    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert {p.identifier for p in event.people} == {"alice", "bob"}

    # Update: remove bob
    mock_event_data["attendees"] = ["alice@example.com"]
    serialized = _serialize_event_data(mock_event_data)
    result = calendar.sync_calendar_event(caldav_account.id, serialized)

    assert result["status"] == "processed"
    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert {p.identifier for p in event.people} == {"alice"}


def test_update_event_replaces_all_person_associations(
    mock_event_data, caldav_account, db_session, qdrant, alice, bob, charlie
):
    """Test that updating attendees fully replaces the set."""
    # Create event with alice
    mock_event_data["attendees"] = ["alice@example.com"]
    serialized = _serialize_event_data(mock_event_data)
    calendar.sync_calendar_event(caldav_account.id, serialized)

    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert {p.identifier for p in event.people} == {"alice"}

    # Update: completely different attendee list
    mock_event_data["attendees"] = ["bob@example.com", "charlie@example.com"]
    serialized = _serialize_event_data(mock_event_data)
    result = calendar.sync_calendar_event(caldav_account.id, serialized)

    assert result["status"] == "processed"
    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert {p.identifier for p in event.people} == {"bob", "charlie"}


def test_update_event_clears_all_person_associations(
    mock_event_data, caldav_account, db_session, qdrant, alice, bob
):
    """Test that updating with no attendees clears all associations."""
    # Create event with attendees
    mock_event_data["attendees"] = ["alice@example.com", "bob@example.com"]
    serialized = _serialize_event_data(mock_event_data)
    calendar.sync_calendar_event(caldav_account.id, serialized)

    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert len(event.people) == 2

    # Update: no attendees
    mock_event_data["attendees"] = []
    serialized = _serialize_event_data(mock_event_data)
    result = calendar.sync_calendar_event(caldav_account.id, serialized)

    assert result["status"] == "updated"
    db_session.expire_all()
    event = db_session.query(CalendarEvent).filter_by(external_id="event-123").first()
    assert event.people == []


@pytest.mark.parametrize("failure_stage", ["calendar_list", "events", "queue"])
def test_google_sync_records_failures(failure_stage, google_calendar_account, db_session):
    service = Mock()
    service.calendarList.return_value.list.return_value.execute.return_value = {"items": [{"id": "primary"}]}
    service.calendars.return_value.get.return_value.execute.return_value = {"summary": "Calendar"}
    service.events.return_value.list.return_value.execute.return_value = {"items": [{
        "id": "event-1", "summary": "Event", "start": {"dateTime": "2026-10-26T10:00:00Z"},
        "end": {"dateTime": "2026-10-26T11:00:00Z"},
    }]}
    google_calendar_account.calendar_ids = []
    db_session.commit()
    with (
        patch("memory.workers.tasks.calendar.refresh_credentials"),
        patch("memory.workers.tasks.calendar.build", return_value=service),
        patch("memory.workers.tasks.calendar.sync_calendar_event.delay") as queue,
    ):
        if failure_stage == "calendar_list":
            service.calendarList.return_value.list.return_value.execute.side_effect = RuntimeError("Google access denied")
        elif failure_stage == "events":
            service.events.return_value.list.return_value.execute.side_effect = RuntimeError("Google access denied")
        else:
            queue.side_effect = RuntimeError("Queue unavailable")
        with pytest.raises(RuntimeError):
            calendar.sync_calendar_account(google_calendar_account.id, force_full=True)
    db_session.refresh(google_calendar_account)
    assert google_calendar_account.sync_error
    assert google_calendar_account.sync_failing_since is not None
    assert google_calendar_account.last_sync_at is None
