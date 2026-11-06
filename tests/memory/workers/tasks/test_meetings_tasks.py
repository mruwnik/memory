"""Tests for meeting Celery tasks."""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import call, patch

import anthropic
import fakeredis
import httpx2
import pytest
from sqlalchemy import update

from memory.agents import triggers
from memory.agents.triggers import IngestConfig
from memory.common import embedding, settings
from memory.common.access_control import user_can_access
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    CalendarAccount,
    CalendarEvent,
    HumanUser,
    JobStatus,
    JobType,
    PendingJob,
    Person,
    Project,
    Task,
    TranscriptAccount,
)
from memory.common.db.models.source_items import Meeting
from memory.common.celery_app import REPROCESS_MEETING
from memory.common.db.models.source_item import Chunk, SourceItem
from memory.workers.tasks import meetings
from memory.common.content_processing import create_content_hash
from memory.common.people import find_or_create_person, make_identifier
from tests.conftest import unique_sha256
from tests.memory.agents.fakes import key_rejected, submitted


def _make_mock_chunk(source_id: int) -> Chunk:
    """Create a mock chunk for testing with a unique ID."""
    return Chunk(
        id=str(uuid.uuid4()),
        content="test chunk content",
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={"source_id": source_id, "tags": ["test"]},
        collection_name="meeting",
    )


@pytest.fixture
def mock_make_session(db_session):
    """Mock make_session and embedding functions for meeting task tests."""

    @contextmanager
    def _mock_session():
        yield db_session

    with patch("memory.workers.tasks.meetings.make_session", _mock_session):
        with patch(
            "memory.common.embedding.embed_source_item",
            side_effect=lambda item, *_: [_make_mock_chunk(item.id or 1)],
        ):
            with patch("memory.common.content_processing.push_to_qdrant"):
                yield db_session


@pytest.fixture
def sample_transcript():
    """Sample meeting transcript for testing."""
    return """
    Alice: Good morning everyone. Let's discuss the Q1 roadmap.
    Bob: I think we should focus on the API improvements.
    Alice: Great idea. Bob, can you draft a proposal by Friday?
    Charlie: I'll help with the testing framework updates.
    Alice: Perfect. Charlie, please coordinate with the QA team by next week.
    Bob: Also, we need to fix the authentication bug before launch.
    Alice: Agreed. Let's wrap up. Thanks everyone!
    """


@pytest.fixture
def sample_extraction_response():
    """Sample LLM extraction response."""
    return {
        "summary": "Q1 roadmap discussion focusing on API improvements and testing.",
        "notes": "- Focus on API improvements\n- Testing framework updates needed\n- Authentication bug to fix before launch",
        "action_items": [
            {
                "description": "Draft API proposal",
                "assignee": "Bob",
                "due_date": "2024-01-15",
                "priority": "high",
            },
            {
                "description": "Coordinate with QA team",
                "assignee": "Charlie",
                "due_date": None,
                "priority": "medium",
            },
            {
                "description": "Fix authentication bug",
                "assignee": None,
                "due_date": None,
                "priority": "urgent",
            },
        ],
    }


# ============================================================================
# Tests for find_person_by_name
# ============================================================================


def test_find_person_by_name_exact_match(mock_make_session, qdrant):
    """Test finding person by exact display name match."""
    person = Person(
        identifier="alice_chen",
        display_name="Alice Chen",
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result = meetings.find_person_by_name(mock_make_session, "Alice Chen")
    assert result is not None
    assert result.identifier == "alice_chen"


def test_find_person_by_name_case_insensitive(mock_make_session, qdrant):
    """Test finding person is case insensitive."""
    person = Person(
        identifier="bob_smith",
        display_name="Bob Smith",
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result = meetings.find_person_by_name(mock_make_session, "bob smith")
    assert result is not None
    assert result.identifier == "bob_smith"


def test_find_person_by_name_by_alias(mock_make_session, qdrant):
    """Test finding person by alias."""
    person = Person(
        identifier="charlie_jones",
        display_name="Charlie Jones",
        aliases=["@charlie", "charlie.j@work.com"],
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result = meetings.find_person_by_name(mock_make_session, "@charlie")
    assert result is not None
    assert result.identifier == "charlie_jones"


def test_find_person_by_name_by_email_in_contact_info(mock_make_session, qdrant):
    """Test finding person by email in contact_info."""
    person = Person(
        identifier="dave_wilson",
        display_name="Dave Wilson",
        contact_info={"email": "dave@example.com"},
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result = meetings.find_person_by_name(mock_make_session, "dave@example.com")
    assert result is not None
    assert result.identifier == "dave_wilson"


def test_find_person_by_name_not_found(mock_make_session, qdrant):
    """Test finding non-existent person returns None."""
    result = meetings.find_person_by_name(mock_make_session, "Nonexistent Person")
    assert result is None


def test_find_person_by_name_empty_string(mock_make_session, qdrant):
    """Test finding with empty string returns None."""
    result = meetings.find_person_by_name(mock_make_session, "")
    assert result is None


def test_find_person_by_name_none(mock_make_session, qdrant):
    """Test finding with None returns None."""
    result = meetings.find_person_by_name(mock_make_session, None)
    assert result is None


def test_find_person_by_name_with_whitespace(mock_make_session, qdrant):
    """Test finding person with surrounding whitespace."""
    person = Person(
        identifier="eve_brown",
        display_name="Eve Brown",
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result = meetings.find_person_by_name(mock_make_session, "  Eve Brown  ")
    assert result is not None
    assert result.identifier == "eve_brown"


# ============================================================================
# Tests for parse_due_date
# ============================================================================


@pytest.mark.parametrize(
    "date_str, expected",
    [
        ("2026-10-22", datetime(2026, 10, 22)),
        ("2026-10-21", datetime(2026, 10, 21)),
        ("2026-10-29T10:30:00", datetime(2026, 10, 29, 10, 30)),
        ("October 30, 2026", datetime(2026, 10, 30)),
        ("2028-10-21", datetime(2028, 10, 21)),
        ("2025-01-01", None),
        ("2026-10-20", None),
        ("2028-10-23", None),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
@pytest.mark.parametrize(
    "reference",
    [datetime(2026, 10, 22, 15, 0), datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)],
)
def test_parse_due_date_keeps_only_dates_plausible_for_the_meeting(date_str, expected, reference):
    assert meetings.parse_due_date(date_str, reference) == expected


# ============================================================================
# Tests for make_task_sha256
# ============================================================================


def test_make_task_sha256_creates_hash():
    """Test that make_task_sha256 creates a valid hash."""
    result = meetings.make_task_sha256(1, "Test task description")
    assert result is not None
    assert isinstance(result, bytes)
    assert len(result) == 32  # SHA256 is 32 bytes


def test_make_task_sha256_deterministic():
    """Test that same input produces same hash."""
    hash1 = meetings.make_task_sha256(1, "Same description")
    hash2 = meetings.make_task_sha256(1, "Same description")
    assert hash1 == hash2


def test_make_task_sha256_different_meeting_id():
    """Test that different meeting IDs produce different hashes."""
    hash1 = meetings.make_task_sha256(1, "Same description")
    hash2 = meetings.make_task_sha256(2, "Same description")
    assert hash1 != hash2


def test_make_task_sha256_different_description():
    """Test that different descriptions produce different hashes."""
    hash1 = meetings.make_task_sha256(1, "Description A")
    hash2 = meetings.make_task_sha256(1, "Description B")
    assert hash1 != hash2


# ============================================================================
# Tests for make_identifier
# ============================================================================


def test_make_identifier_basic():
    """Test basic identifier creation."""
    result = make_identifier("John Smith")
    assert result == "john_smith"


def test_make_identifier_with_extra_spaces():
    """Test identifier with extra spaces - multiple spaces collapse to single underscore."""
    result = make_identifier("  John   Smith  ")
    assert result == "john_smith"  # Multiple spaces collapse to single underscore


def test_make_identifier_with_special_chars():
    """Test identifier strips special characters."""
    result = make_identifier("John O'Brien-Smith")
    assert result == "john_obriensmith"


def test_make_identifier_unicode():
    """Test identifier with unicode characters."""
    result = make_identifier("José García")
    assert result == "josé_garcía"  # Unicode preserved (isalnum includes unicode)


def test_make_identifier_numbers():
    """Test identifier preserves numbers."""
    result = make_identifier("User 123")
    assert result == "user_123"


# ============================================================================
# normalize_attendee_names tests
# ============================================================================


def test_normalize_attendee_names_simple_list():
    """Test basic list of names passes through."""
    result = meetings.normalize_attendee_names(["Alice", "Bob", "Charlie"])
    assert result == ["Alice", "Bob", "Charlie"]


def test_normalize_attendee_names_splits_comma_separated():
    """Test comma-separated values get split into individual names."""
    result = meetings.normalize_attendee_names(["alice@x.com,bob@y.com,charlie@z.com"])
    assert result == ["alice@x.com", "bob@y.com", "charlie@z.com"]


def test_normalize_attendee_names_mixed_input():
    """Test mix of individual names and comma-separated values."""
    result = meetings.normalize_attendee_names(["Alice", "bob@x.com,charlie@y.com", "Dave"])
    assert result == ["Alice", "bob@x.com", "charlie@y.com", "Dave"]


def test_normalize_attendee_names_strips_whitespace():
    """Test whitespace is stripped from names."""
    result = meetings.normalize_attendee_names(["  Alice  ", "bob@x.com , charlie@y.com "])
    assert result == ["Alice", "bob@x.com", "charlie@y.com"]


def test_normalize_attendee_names_skips_empty():
    """Test empty strings and None values are skipped."""
    result = meetings.normalize_attendee_names(["Alice", "", None, "  ", "Bob"])
    assert result == ["Alice", "Bob"]


def test_normalize_attendee_names_handles_trailing_commas():
    """Test trailing commas don't create empty entries."""
    result = meetings.normalize_attendee_names(["alice@x.com,bob@y.com,"])
    assert result == ["alice@x.com", "bob@y.com"]


# ============================================================================
# Tests for prettify_email_localpart
# ============================================================================


@pytest.mark.parametrize(
    "email,expected",
    [
        ("daniel.oconnell@x.com", "Daniel Oconnell"),
        ("dan_o@x.com", "Dan O"),
        ("dan-o@x.com", "Dan O"),
        ("alice@example.com", "Alice"),
        ("first.middle.last@example.com", "First Middle Last"),
        ("ALL.CAPS@example.com", "All Caps"),
        # Empty local-part falls through to the safety net.
        ("@example.com", "Unknown"),
        # No `@` at all is not an email — must not silently produce a "Person"
        # named after the raw string.
        ("foo", "Unknown"),
        ("", "Unknown"),
    ],
)
def test_prettify_email_localpart(email, expected):
    assert meetings.prettify_email_localpart(email) == expected


# ============================================================================
# Tests for find_or_create_person
# ============================================================================


def test_find_or_create_person_finds_existing(mock_make_session, qdrant):
    """Test finding an existing person."""
    person = Person(
        identifier="existing_person",
        display_name="Existing Person",
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result, created = find_or_create_person(mock_make_session, "Existing Person")
    assert result is not None
    assert result.identifier == "existing_person"
    assert created is False


def test_find_or_create_person_creates_new(mock_make_session, qdrant):
    """Test creating a new person."""
    result, created = find_or_create_person(mock_make_session, "New Person")
    assert result is not None
    assert result.identifier == "new_person"
    assert result.display_name == "New Person"
    assert "New Person" in result.aliases
    assert created is True


def test_find_or_create_person_finds_by_identifier(mock_make_session, qdrant):
    """Test finding person by identifier when name doesn't match exactly."""
    person = Person(
        identifier="john_doe",
        display_name="John Doe III",  # Different display name
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    # Create someone with a name that generates the same identifier
    result, created = find_or_create_person(mock_make_session, "John Doe")
    assert result is not None
    assert result.identifier == "john_doe"
    assert created is False


# ============================================================================
# Tests for link_attendees
# ============================================================================


def test_link_attendees_links_existing_people(mock_make_session, qdrant):
    """Test linking existing people to a meeting."""
    # Create people
    alice = Person(
        identifier="alice",
        display_name="Alice",
    )
    bob = Person(
        identifier="bob",
        display_name="Bob",
    )
    mock_make_session.add_all([alice, bob])
    mock_make_session.commit()

    # Create meeting
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["Alice", "Bob"], create_missing=False
    )

    assert result["linked"] == 2
    assert result["created"] == 0
    assert result["skipped"] == []
    assert len(meeting.attendees) == 2


def test_link_attendees_creates_new_people(mock_make_session, qdrant):
    """Test creating new people when linking attendees."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test2"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["New Person One", "New Person Two"], create_missing=True
    )

    assert result["linked"] == 0
    assert result["created"] == 2
    assert result["skipped"] == []
    assert len(meeting.attendees) == 2

    # Verify people were created
    person1 = mock_make_session.query(Person).filter_by(identifier="new_person_one").first()
    assert person1 is not None
    assert person1.display_name == "New Person One"


def test_link_attendees_skips_not_found_when_create_false(mock_make_session, qdrant):
    """Test skipping non-existent people when create_missing is False."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test3"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["Unknown Person"], create_missing=False
    )

    assert result["linked"] == 0
    assert result["created"] == 0
    assert result["skipped"] == ["Unknown Person"]
    assert len(meeting.attendees) == 0


def test_link_attendees_mixed_existing_and_new(mock_make_session, qdrant):
    """Test linking mix of existing and new people."""
    alice = Person(
        identifier="alice",
        display_name="Alice",
    )
    mock_make_session.add(alice)
    mock_make_session.commit()

    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test4"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["Alice", "New Bob"], create_missing=True
    )

    assert result["linked"] == 1  # Alice
    assert result["created"] == 1  # New Bob
    assert result["skipped"] == []


def test_link_attendees_skips_empty_names(mock_make_session, qdrant):
    """Test that empty names are skipped."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test5"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["", "  ", None, "Valid Name"], create_missing=True
    )

    assert result["created"] == 1
    assert len(meeting.attendees) == 1


def test_link_attendees_skips_duplicates(mock_make_session, qdrant):
    """Test that duplicate names don't create duplicate links."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test6"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["Alice", "Alice", "Alice"], create_missing=True
    )

    assert result["created"] == 1
    assert len(meeting.attendees) == 1


def test_link_attendees_skips_already_linked(mock_make_session, qdrant):
    """Test that already-linked attendees are skipped."""
    alice = Person(
        identifier="alice",
        display_name="Alice",
    )
    mock_make_session.add(alice)
    mock_make_session.commit()

    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:test7"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    meeting.attendees.append(alice)
    mock_make_session.add(meeting)
    mock_make_session.commit()

    result = meetings.link_attendees(
        mock_make_session, meeting, ["Alice"], create_missing=True
    )

    # Already linked, so no new links
    assert result["linked"] == 0
    assert result["created"] == 0
    assert len(meeting.attendees) == 1


# ============================================================================
# Tests for create_action_item_tasks
# ============================================================================


def test_create_action_item_tasks_basic(mock_make_session, qdrant):
    """Test creating tasks from action items."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:tasks1"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    action_items = [
        {"description": "Task 1", "assignee": None, "due_date": None, "priority": "high"},
        {"description": "Task 2", "assignee": None, "due_date": "2024-01-20", "priority": "low"},
    ]

    created = meetings.create_action_item_tasks(mock_make_session, meeting, action_items)

    assert len(created) == 2
    assert "Task 1" in created
    assert "Task 2" in created

    mock_make_session.flush()
    tasks = mock_make_session.query(Task).all()
    assert len(tasks) == 2


def test_create_action_item_tasks_with_assignee(mock_make_session, qdrant):
    """Test creating tasks with assignee tags."""
    alice = Person(
        identifier="alice",
        display_name="Alice",
    )
    mock_make_session.add(alice)
    mock_make_session.commit()

    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:tasks2"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    action_items = [
        {"description": "Alice's task", "assignee": "Alice", "due_date": None, "priority": "medium"},
    ]

    created = meetings.create_action_item_tasks(mock_make_session, meeting, action_items)

    assert len(created) == 1
    mock_make_session.flush()
    task = mock_make_session.query(Task).first()
    assert "assignee:alice" in task.tags


def test_create_action_item_tasks_skips_empty_description(mock_make_session, qdrant):
    """Test that items without description are skipped."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:tasks3"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    action_items = [
        {"description": "", "assignee": None, "due_date": None, "priority": None},
        {"description": None, "assignee": None, "due_date": None, "priority": None},
        {"assignee": None, "due_date": None, "priority": None},  # No description key
    ]

    created = meetings.create_action_item_tasks(mock_make_session, meeting, action_items)
    assert len(created) == 0


def test_create_action_item_tasks_priority_validation(mock_make_session, qdrant):
    """Test that invalid priorities default to medium."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:tasks4"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    action_items = [
        {"description": "Valid high", "priority": "high"},
        {"description": "Valid urgent", "priority": "urgent"},
        {"description": "Invalid priority", "priority": "super-important"},
        {"description": "No priority", "priority": None},
    ]

    meetings.create_action_item_tasks(mock_make_session, meeting, action_items)
    mock_make_session.commit()

    tasks = mock_make_session.query(Task).all()
    priorities = {t.task_title: t.priority for t in tasks}

    assert priorities["Valid high"] == "high"
    assert priorities["Valid urgent"] == "urgent"
    assert priorities["Invalid priority"] == "medium"
    assert priorities["No priority"] == "medium"


def test_create_action_item_tasks_with_due_date(mock_make_session, qdrant):
    """Test creating tasks with due dates."""
    meeting = Meeting(
        title="Test Meeting",
        content="Test transcript",
        sha256=create_content_hash("meeting:tasks5"),
        meeting_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
        modality="meeting",
        mime_type="text/plain",
        size=100,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    action_items = [
        {"description": "Task with date", "due_date": "2024-03-15", "priority": "medium"},
    ]

    meetings.create_action_item_tasks(mock_make_session, meeting, action_items)
    mock_make_session.commit()

    task = mock_make_session.query(Task).first()
    assert task.due_date is not None
    assert task.due_date.year == 2024
    assert task.due_date.month == 3
    assert task.due_date.day == 15


# ============================================================================
# Tests for extract_and_update_meeting
# ============================================================================


EXTRACTED = {"summary": "We met.", "notes": "- a", "action_items": [
    {"description": "Ship it", "assignee": None, "due_date": "2026-11-01", "priority": "high"},
]}


async def connection_dropped():
    raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))


def meeting_for(db, creator_id):
    meeting = Meeting(modality="meeting", sha256=unique_sha256("m"), content="Alice: hi", creator_id=creator_id)
    db.add(meeting)
    db.flush()
    return meeting


@pytest.mark.parametrize(
    "attendees",
    [
        {"attendee_pairs": [("new@example.com", "New Person")]},
        {"attendee_emails": ["new@example.com"]},
        {"attendee_names": ["New Person"]},
    ],
)
@pytest.mark.parametrize("pushed", [False, True])
def test_link_attendees_records_the_meetings_creator_on_new_people(db_session, regular_user, attendees, pushed):
    """A pushed meeting has no creator of its own; its transcript account's owner brought it in."""
    meeting = meeting_for(db_session, None if pushed else regular_user.id)
    account = TranscriptAccount(user_id=regular_user.id, name="FF", provider="fireflies")
    account.api_key = "k"
    meeting.transcript_account = account
    db_session.flush()

    meetings.link_attendees(db_session, meeting, **attendees)
    db_session.flush()

    assert [(p.creator_id, p.creator_trusted) for p in meeting.attendees] == [(regular_user.id, False)]


def test_extraction_runs_on_the_creators_key(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(EXTRACTED)]
    meeting = meeting_for(db_session, regular_user.id)
    meeting.meeting_date = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)

    result = meetings.extract_and_update_meeting(db_session, meeting)

    request = scripted_llm.requests[0]
    assert (result["status"], result["tasks_created"], meeting.summary) == ("success", 1, "We met.")
    assert request["api_key"] == "sk-test-default"
    assert request["messages"][0].content[0].text == (
        "Meeting date: Thursday 2026-10-22T15:00:00+00:00\n\nMeeting transcript:\nAlice: hi"
    )


def test_undated_meetings_are_dated_by_when_they_were_brought_in(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted(EXTRACTED)]
    meeting = meeting_for(db_session, regular_user.id)
    meeting.inserted_at = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)

    meetings.extract_and_update_meeting(db_session, meeting)

    task = db_session.query(Task).filter(Task.source_item_id == meeting.id).one()
    assert scripted_llm.requests[0]["messages"][0].content[0].text.startswith(
        "Meeting date: Thursday 2026-10-22T15:00:00+00:00\n"
    )
    assert task.due_date.date() == datetime(2026, 11, 1).date()


def test_meetings_without_a_key_are_kept_unextracted(db_session, regular_user, scripted_llm):
    meeting = meeting_for(db_session, regular_user.id)

    result = meetings.extract_and_update_meeting(db_session, meeting)

    assert (result["status"], meeting.summary, scripted_llm.requests) == ("skipped", None, [])


@pytest.mark.parametrize("failure", [key_rejected, connection_dropped])
def test_a_failed_extraction_marks_the_meeting_failed(db_session, regular_user, llm_key, scripted_llm, failure):
    scripted_llm.script = [failure]
    meeting = meeting_for(db_session, regular_user.id)

    result = meetings.extract_and_update_meeting(db_session, meeting)

    assert (result["status"], meeting.extraction_status) == ("error", "failed")
    assert "sk-test-default" not in result["error"]


@pytest.mark.parametrize(
    "failure",
    [
        patch("memory.common.content_processing.push_chunks_to_qdrant", side_effect=RuntimeError("qdrant down")),
        patch("memory.workers.tasks.meetings.process_content_item", side_effect=RuntimeError("embed down")),
    ],
)
def test_a_meeting_that_is_not_stored_keeps_no_tasks(
    failure, regular_user, llm_key, scripted_llm, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(EXTRACTED)]
    meeting = meeting_for(mock_make_session, regular_user.id)

    with failure:
        result = meetings.execute_meeting_processing(mock_make_session, meeting, None, None)

    tasks = mock_make_session.query(Task).filter(Task.source_item_id == meeting.id).all()
    assert (result["status"], meeting.extraction_status, tasks) == ("error", "extracted", [])


def test_a_meeting_keeps_its_tasks_when_embeddings_are_not_configured(
    regular_user, llm_key, scripted_llm, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(EXTRACTED)]
    meeting = meeting_for(mock_make_session, regular_user.id)

    with (
        patch.object(settings, "VOYAGE_API_KEY", ""),
        patch("memory.common.embedding.embed_source_item", side_effect=embedding.EmbeddingsNotConfigured()),
    ):
        result = meetings.execute_meeting_processing(mock_make_session, meeting, None, None)

    tasks = mock_make_session.query(Task).filter(Task.source_item_id == meeting.id).all()
    assert (result["status"], meeting.extraction_status, len(tasks)) == ("success", "complete", 1)


def test_keyless_meeting_is_embedded_with_extraction_skipped(
    regular_user, scripted_llm, mock_make_session, qdrant
):
    meeting = meeting_for(mock_make_session, regular_user.id)

    result = meetings.execute_meeting_processing(mock_make_session, meeting, None, None)

    assert (result["status"], meeting.extraction_status, meeting.embed_status) == ("success", "skipped", "STORED")


# ============================================================================
# Tests for process_meeting task
# ============================================================================


def test_process_meeting_success(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test successful meeting processing."""
    scripted_llm.script = [submitted(sample_extraction_response)]

    result = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=sample_transcript,
        title="Q1 Planning",
        meeting_date="2024-01-10T10:00:00",
        duration_minutes=60,
        attendee_names=["Alice", "Bob", "Charlie"],
        source_tool="test",
        external_id="test-123",
        tags=["planning", "q1"],
    )

    assert result["status"] == "success"
    assert "meeting_id" in result
    assert result["tasks_created"] == 3
    assert result["attendees_created"] == 3  # All new

    # Verify meeting was created
    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.title == "Q1 Planning"
    assert meeting.summary == sample_extraction_response["summary"]
    assert meeting.extraction_status == "complete"
    assert meeting.size == len(meeting.content.encode())


def test_synced_meeting_visible_to_account_owner_not_to_non_member(
    scripted_llm,
    llm_key,
    sample_extraction_response,
    sample_transcript,
    mock_make_session,
    qdrant,
    regular_user,
):
    scripted_llm.script = [submitted(sample_extraction_response)]
    db = mock_make_session
    project = Project(title="Sales", state="open")
    db.add(project)
    db.flush()
    account = TranscriptAccount(
        user_id=regular_user.id,
        name="FF",
        provider="fireflies",
        project_id=project.id,
        sensitivity="internal",
    )
    account.api_key = "k"
    outsider = HumanUser(
        name="Outsider", email="outsider@example.com", password_hash="x", scopes=[]
    )
    db.add_all([account, outsider])
    db.commit()

    result = meetings.process_meeting(
        transcript=sample_transcript,
        external_id="synced-1",
        transcript_account_id=account.id,
    )
    meeting = db.get(Meeting, result["meeting_id"])
    tasks = db.query(Task).filter(Task.source_item_id == meeting.id).all()

    assert len(tasks) == 3
    assert {
        (i.project_id, i.sensitivity, i.creator_id) for i in [meeting, *tasks]
    } == {(project.id, "internal", regular_user.id)}
    assert user_can_access(regular_user, meeting, {})
    assert not user_can_access(outsider, meeting, {})
    assert user_can_access(outsider, meeting, {project.id: "manager"})


def test_owner_trigger_fires_for_synced_meeting_before_any_reconcile(
    scripted_llm,
    llm_key,
    sample_extraction_response,
    sample_transcript,
    mock_make_session,
    qdrant,
    regular_user,
):
    scripted_llm.script = [submitted(sample_extraction_response)]
    db = mock_make_session
    project = Project(title="Sales", state="open")
    db.add(project)
    db.flush()
    account = TranscriptAccount(
        user_id=regular_user.id,
        name="FF",
        provider="fireflies",
        project_id=project.id,
        sensitivity="internal",
    )
    account.api_key = "k"
    definition = AgentDefinition(
        user_id=regular_user.id, name="t", spec={"system_prompt": "x", "model": "m"}
    )
    db.add_all([account, definition])
    db.flush()
    trigger = AgentTrigger(
        definition_id=definition.id,
        kind="ingest",
        enabled=True,
        config=IngestConfig.model_validate(
            {"filter": {"source_types": ["meeting"]}}
        ).model_dump(mode="json"),
    )
    db.add(trigger)
    db.commit()

    result = meetings.process_meeting(
        transcript=sample_transcript,
        external_id="synced-trigger",
        transcript_account_id=account.id,
    )

    matched = triggers.match_ingest_triggers(result["meeting_id"], fakeredis.FakeRedis())
    assert matched == [trigger.id]


def test_detached_tasks_keep_their_resolved_access_control(db_session, regular_user):
    project = Project(title="Sales", state="open")
    db_session.add(project)
    db_session.flush()
    account = TranscriptAccount(
        user_id=regular_user.id,
        name="FF",
        provider="fireflies",
        project_id=project.id,
        sensitivity="internal",
    )
    account.api_key = "k"
    meeting = Meeting(sha256=uuid.uuid4().bytes * 2, transcript_account=account)
    task = Task(sha256=uuid.uuid4().bytes * 2, task_title="Follow up", source_item=meeting)
    db_session.add_all([meeting, task])
    db_session.flush()
    meeting.apply_inherited_access_control()
    task.apply_inherited_access_control()

    meetings.detach_meeting_tasks(db_session, meeting)
    db_session.expire(task)
    task.apply_inherited_access_control()

    assert (task.project_id, task.sensitivity, task.creator_id) == (
        project.id,
        "internal",
        regular_user.id,
    )


def test_process_meeting_records_uploader_as_creator(
    scripted_llm, llm_key, sample_transcript, mock_make_session, qdrant, regular_user
):
    scripted_llm.script = [submitted({"summary": "s", "notes": "", "action_items": []})]

    result = meetings.process_meeting(
        transcript=sample_transcript, creator_id=regular_user.id
    )

    meeting = mock_make_session.get(Meeting, result["meeting_id"])
    assert meeting.creator_id == regular_user.id


def test_process_meeting_idempotent(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test that duplicate external_id skips processing."""
    scripted_llm.script = [submitted(sample_extraction_response)]

    # First call
    result1 = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=sample_transcript,
        external_id="duplicate-123",
    )
    assert result1["status"] == "success"

    # Second call with same external_id
    result2 = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript="Different transcript",
        external_id="duplicate-123",
    )
    assert result2["status"] == "exists"
    assert result2["meeting_id"] == result1["meeting_id"]

    # Verify only one meeting exists
    count = mock_make_session.query(Meeting).count()
    assert count == 1


def test_process_meeting_losing_an_insert_race_returns_exists(
    regular_user, sample_transcript, mock_make_session, qdrant
):
    """A concurrent ingest that commits between our existence check and our
    insert must not fail this one on the external_id unique index."""
    winner = Meeting(
        content="Winner's transcript",
        sha256=unique_sha256("race-winner"),
        modality="meeting",
        mime_type="text/plain",
        size=19,
        external_id="race-1",
    )
    mock_make_session.add(winner)
    mock_make_session.commit()

    with patch.object(meetings, "find_meeting", side_effect=[None, winner]):
        result = meetings.process_meeting(
            creator_id=regular_user.id, transcript=sample_transcript, external_id="race-1"
        )

    assert result == {"status": "exists", "meeting_id": winner.id, "external_id": "race-1"}
    assert mock_make_session.query(Meeting).count() == 1


class WorkerKilled(BaseException):
    """A worker SIGKILLed, OOMed or past its hard time limit stops with no
    handler running; a BaseException skips them all the same way."""


def killed_mid_extraction():
    return patch.object(meetings, "call_extraction_llm", side_effect=WorkerKilled)


def killed_mid_embedding():
    return patch.object(meetings, "process_content_item", side_effect=WorkerKilled)


@pytest.mark.parametrize("killed, runs", [(killed_mid_extraction, 0), (killed_mid_embedding, 1)])
@pytest.mark.parametrize("stored_before, replace", [([], False), (["Ann: The first draft."], True)])
def test_a_meeting_whose_worker_died_mid_run_is_processed_when_redelivered(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant,
    stored_before, replace, killed, runs,
):
    scripted_llm.script = [submitted(sample_extraction_response) for _ in range(len(stored_before) + runs + 1)]
    for transcript in stored_before:
        meetings.process_meeting(creator_id=regular_user.id, transcript=transcript, external_id="ff-1")
    ingest = {"creator_id": regular_user.id, "transcript": sample_transcript, "external_id": "ff-1", "replace": replace}
    with killed(), pytest.raises(WorkerKilled):
        meetings.process_meeting(**ingest)
    mock_make_session.rollback()

    result = meetings.process_meeting(**ingest)

    meeting = mock_make_session.get(Meeting, result["meeting_id"])
    tasks = mock_make_session.query(Task).filter(Task.source_item_id == meeting.id).count()
    assert (result["status"], meeting.extraction_status, meeting.content, tasks) == (
        "success", "complete", sample_transcript, 3
    )
    assert mock_make_session.query(Meeting).count() == 1


@pytest.mark.parametrize("status", ["retrying", "failed", "extracted", "complete", "skipped"])
def test_a_redelivered_ingest_reruns_only_a_first_attempt_that_died(
    status, scripted_llm, regular_user, sample_transcript, mock_make_session, qdrant
):
    """A second loss is left to cleanup_stuck_meetings to give up on, and a failure stays failed."""
    meeting = meeting_for(mock_make_session, regular_user.id)
    meeting.external_id, meeting.extraction_status = "ff-1", status
    mock_make_session.commit()

    result = meetings.process_meeting(creator_id=regular_user.id, transcript=sample_transcript, external_id="ff-1")

    assert result == {"status": "exists", "meeting_id": meeting.id, "external_id": "ff-1"}
    assert (meeting.extraction_status, scripted_llm.requests) == (status, [])


@pytest.mark.parametrize("killed", [killed_mid_extraction, killed_mid_embedding])
@pytest.mark.parametrize("retry, kept", [(False, "processing"), (True, "retrying")])
def test_a_reprocess_lost_mid_run_leaves_the_meeting_stuck_for_cleanup(
    retry, kept, killed, scripted_llm, llm_key, regular_user, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(EXTRACTED)]
    meeting = meeting_for(mock_make_session, regular_user.id)
    meeting.extraction_status = "complete"
    mock_make_session.commit()

    with killed(), pytest.raises(WorkerKilled):
        meetings.reprocess_meeting(item_id=meeting.id, retry=retry)
    mock_make_session.rollback()

    assert mock_make_session.get(Meeting, meeting.id).extraction_status == kept


def backdate(session, meeting: Meeting, hours: int) -> None:
    session.execute(
        update(SourceItem)
        .where(SourceItem.id == meeting.id)
        .values(updated_at=datetime.now(timezone.utc) - timedelta(hours=hours, minutes=1))
    )
    session.commit()


def reprocesses_sent(dispatch) -> list:
    return [c for c in dispatch.call_args_list if c.args == (REPROCESS_MEETING,)]


@pytest.mark.parametrize(
    "status, hours_ago, after, retried",
    [
        ("processing", 2, "retrying", True),
        ("pending", 2, "retrying", True),
        ("retrying", 2, "failed", False),
        ("processing", 0, "processing", False),
        ("pending", 0, "pending", False),
        ("failed", 2, "failed", False),
    ],
)
def test_cleanup_stuck_meetings_retries_a_lost_meeting_once(
    status, hours_ago, after, retried, db_session, regular_user, no_celery_dispatch
):
    meeting = meeting_for(db_session, regular_user.id)
    meeting.extraction_status = status
    db_session.commit()
    backdate(db_session, meeting, hours_ago)

    meetings.cleanup_stuck_meetings()

    db_session.refresh(meeting)
    assert meeting.extraction_status == after
    assert reprocesses_sent(no_celery_dispatch) == [
        call(REPROCESS_MEETING, kwargs={"item_id": meeting.id, "retry": True})
    ] * retried


@pytest.mark.parametrize(
    "status, rerun, in_flight",
    [
        ("complete", lambda m, user: meetings.reprocess_meeting(item_id=m.id), "processing"),
        (
            "processing",
            lambda m, user: meetings.process_meeting(creator_id=user.id, transcript="Bo: hi", external_id="ff-1"),
            "retrying",
        ),
    ],
)
def test_cleanup_stuck_meetings_leaves_a_run_started_on_an_old_meeting(
    status, rerun, in_flight, llm_key, regular_user, mock_make_session, qdrant, no_celery_dispatch
):
    meeting = meeting_for(mock_make_session, regular_user.id)
    meeting.external_id, meeting.extraction_status = "ff-1", status
    mock_make_session.commit()
    backdate(mock_make_session, meeting, 2)
    with killed_mid_extraction(), pytest.raises(WorkerKilled):
        rerun(meeting, regular_user)
    mock_make_session.rollback()

    meetings.cleanup_stuck_meetings()

    mock_make_session.refresh(meeting)
    assert (meeting.extraction_status, reprocesses_sent(no_celery_dispatch)) == (in_flight, [])


def test_cleanup_stuck_meetings_leaves_the_rerun_it_started(
    llm_key, regular_user, mock_make_session, qdrant, no_celery_dispatch
):
    meeting = meeting_for(mock_make_session, regular_user.id)
    meeting.extraction_status = "processing"
    mock_make_session.commit()
    backdate(mock_make_session, meeting, 2)
    meetings.cleanup_stuck_meetings()
    [sent] = reprocesses_sent(no_celery_dispatch)
    with killed_mid_extraction(), pytest.raises(WorkerKilled):
        meetings.reprocess_meeting(**sent.kwargs["kwargs"])
    mock_make_session.rollback()

    meetings.cleanup_stuck_meetings()

    mock_make_session.refresh(meeting)
    assert (meeting.extraction_status, len(reprocesses_sent(no_celery_dispatch))) == ("retrying", 1)


def open_job(db, external_id):
    job = PendingJob(job_type=JobType.MEETING.value, external_id=external_id, status=JobStatus.PROCESSING.value)
    db.add(job)
    db.commit()
    return job


def test_cleanup_stuck_meetings_reruns_a_lost_meeting_under_its_job(
    scripted_llm, llm_key, regular_user, mock_make_session, qdrant, no_celery_dispatch
):
    scripted_llm.script = [submitted(EXTRACTED)]
    meeting = meeting_for(mock_make_session, regular_user.id)
    meeting.external_id, meeting.extraction_status = "ff-1", "processing"
    job = open_job(mock_make_session, "ff-1")
    backdate(mock_make_session, meeting, 2)

    meetings.cleanup_stuck_meetings()
    [sent] = reprocesses_sent(no_celery_dispatch)
    meetings.reprocess_meeting(**sent.kwargs["kwargs"])

    mock_make_session.refresh(job)
    assert (job.status, job.result_id, meeting.extraction_status) == ("complete", meeting.id, "complete")


def test_cleanup_stuck_meetings_fails_the_job_of_a_meeting_it_gives_up_on(
    regular_user, db_session, no_celery_dispatch
):
    meeting = meeting_for(db_session, regular_user.id)
    meeting.external_id, meeting.extraction_status = "ff-1", "retrying"
    job = open_job(db_session, "ff-1")
    backdate(db_session, meeting, 2)

    meetings.cleanup_stuck_meetings()

    db_session.refresh(job)
    assert (job.status, meeting.extraction_status) == ("failed", "failed")


def test_process_meeting_replace_updates_the_stored_meeting(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(sample_extraction_response), submitted(sample_extraction_response)]
    first = meetings.process_meeting(
        creator_id=regular_user.id, transcript=sample_transcript, title="Draft", external_id="ff-1"
    )

    second = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript="Ann: The corrected transcript.",
        title="Final",
        duration_minutes=42,
        external_id="ff-1",
        replace=True,
    )

    assert second["status"] == "success"
    assert second["meeting_id"] == first["meeting_id"]
    meeting = mock_make_session.get(Meeting, first["meeting_id"])
    assert (meeting.title, meeting.duration_minutes) == ("Final", 42)
    assert meeting.content == "Ann: The corrected transcript."
    assert meeting.sha256 == create_content_hash("Ann: The corrected transcript.")
    assert meeting.size == len("Ann: The corrected transcript.")
    assert mock_make_session.query(Meeting).count() == 1


def test_process_meeting_replace_drops_untouched_action_items(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(sample_extraction_response), submitted(sample_extraction_response)]
    first = meetings.process_meeting(creator_id=regular_user.id, transcript=sample_transcript, external_id="ff-1")
    old = mock_make_session.query(Task).filter(Task.source_item_id == first["meeting_id"]).all()
    old[0].status = "done"
    mock_make_session.commit()
    done_id, pending_ids = old[0].id, [t.id for t in old[1:]]

    meetings.process_meeting(
        creator_id=regular_user.id, transcript="Ann: Corrected.", external_id="ff-1", replace=True
    )

    remaining = {t.id: t.source_item_id for t in mock_make_session.query(Task).all()}
    attached = {i for i, source in remaining.items() if source == first["meeting_id"]}
    assert done_id in attached
    assert not set(pending_ids) & set(remaining)
    assert len(attached) == 3


def rerun_by_reprocess(meeting_id, user_id, transcript, retry=False):
    meetings.reprocess_meeting(item_id=meeting_id, retry=retry)


def rerun_by_replace(meeting_id, user_id, transcript):
    meetings.process_meeting(creator_id=user_id, transcript="Ann: Corrected.", external_id="ff-1", replace=True)


def rerun_by_redelivery(meeting_id, user_id, transcript):
    meetings.process_meeting(creator_id=user_id, transcript=transcript, external_id="ff-1")


@pytest.mark.parametrize(
    "rerun, in_flight",
    [
        (rerun_by_reprocess, "complete"),
        (lambda *args: rerun_by_reprocess(*args, retry=True), "processing"),
        (rerun_by_replace, "complete"),
        (rerun_by_redelivery, "processing"),
    ],
)
def test_a_rerun_meeting_keeps_its_action_items(
    rerun, in_flight, scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript,
    mock_make_session, qdrant,
):
    scripted_llm.script = [submitted(sample_extraction_response), submitted(sample_extraction_response)]
    first = meetings.process_meeting(creator_id=regular_user.id, transcript=sample_transcript, external_id="ff-1")
    done = mock_make_session.query(Task).filter(Task.source_item_id == first["meeting_id"]).first()
    done.status = "done"
    mock_make_session.get(Meeting, first["meeting_id"]).extraction_status = in_flight
    mock_make_session.commit()

    rerun(first["meeting_id"], regular_user.id, sample_transcript)

    attached = mock_make_session.query(Task).filter(Task.source_item_id == first["meeting_id"]).all()
    assert len(attached) == 3
    assert (done.source_item_id, done.status) == (first["meeting_id"], "done")
    assert mock_make_session.query(Task).count() == 3


def test_reprocessing_reattaches_action_items_an_earlier_rerun_orphaned(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(sample_extraction_response), submitted(sample_extraction_response)]
    first = meetings.process_meeting(creator_id=regular_user.id, transcript=sample_transcript, external_id="ff-1")
    orphans = mock_make_session.query(Task).filter(Task.source_item_id == first["meeting_id"]).all()
    meetings.detach_meeting_tasks(mock_make_session, mock_make_session.get(Meeting, first["meeting_id"]))
    mock_make_session.commit()

    meetings.reprocess_meeting(item_id=first["meeting_id"])

    attached = mock_make_session.query(Task).filter(Task.source_item_id == first["meeting_id"]).all()
    assert {t.id for t in attached} == {t.id for t in orphans}


@pytest.mark.parametrize(
    "transcript, account_offset",
    [
        ("same transcript", 0),  # identical resend
        ("Ann: Corrected.", 1),  # another account's meeting
    ],
)
def test_process_meeting_replace_keeps_meeting_when_not_needed(
    scripted_llm, llm_key, regular_user, sample_extraction_response, mock_make_session, qdrant, transcript, account_offset
):
    accounts = [TranscriptAccount(user_id=regular_user.id, name=f"ff{i}", provider="fireflies") for i in range(2)]
    mock_make_session.add_all(accounts)
    mock_make_session.commit()
    scripted_llm.script = [submitted(sample_extraction_response)]
    first = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript="same transcript",
        external_id="ff-1",
        transcript_account_id=accounts[0].id,
    )

    second = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=transcript,
        external_id="ff-1",
        transcript_account_id=accounts[account_offset].id,
        replace=True,
    )

    assert second == {"status": "exists", "meeting_id": first["meeting_id"], "external_id": "ff-1"}
    assert mock_make_session.get(Meeting, first["meeting_id"]).content == "same transcript"


def test_process_meeting_minimal(
    scripted_llm, llm_key, regular_user, sample_transcript, mock_make_session, qdrant
):
    """Test meeting processing with minimal data."""
    scripted_llm.script = [submitted({"summary": "Brief summary", "notes": "", "action_items": []})]

    result = meetings.process_meeting(transcript=sample_transcript, creator_id=regular_user.id)

    assert result["status"] == "success"
    assert result["tasks_created"] == 0
    assert result["attendees_linked"] == 0


@patch("memory.workers.tasks.meetings.process_content_item")
def test_process_meeting_extraction_failure_short_circuits(
    mock_process_content, scripted_llm, llm_key, regular_user, sample_transcript, mock_make_session, qdrant
):
    """Regression: when extraction fails, embedding (process_content_item)
    must NOT run, extraction_status stays 'failed', and the result keeps
    status='error' rather than being overwritten to 'complete' by the
    embedding step. This pins the bugfix in execute_meeting_processing.
    """
    scripted_llm.script = [connection_dropped]

    result = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=sample_transcript,
        external_id="extraction-shortcircuit",
    )

    assert result["status"] == "error"
    # Crucially: embedding must not be called when extraction failed.
    mock_process_content.assert_not_called()

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.extraction_status == "failed"


@patch("memory.workers.tasks.meetings.create_action_item_tasks", side_effect=RuntimeError("db down"))
def test_a_task_creation_failure_marks_the_meeting_failed(
    mock_create_tasks, scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    scripted_llm.script = [submitted(sample_extraction_response)]

    result = meetings.process_meeting(transcript=sample_transcript, creator_id=regular_user.id)

    meeting = mock_make_session.get(Meeting, result["meeting_id"])
    assert (result["status"], meeting.extraction_status) == ("error", "failed")


def test_process_meeting_with_existing_people(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test meeting with existing attendees."""
    scripted_llm.script = [submitted(sample_extraction_response)]

    # Create existing people
    alice = Person(
        identifier="alice",
        display_name="Alice",
    )
    mock_make_session.add(alice)
    mock_make_session.commit()

    result = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=sample_transcript,
        attendee_names=["Alice", "New Bob"],
    )

    assert result["status"] == "success"
    assert result["attendees_linked"] == 1  # Alice
    assert result["attendees_created"] == 1  # New Bob


def test_process_meeting_parses_meeting_date(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test that meeting date is parsed correctly."""
    scripted_llm.script = [submitted(sample_extraction_response)]

    meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=sample_transcript,
        meeting_date="2024-06-15T14:30:00",
    )

    meeting = mock_make_session.query(Meeting).first()
    assert meeting.meeting_date is not None
    assert meeting.meeting_date.year == 2024
    assert meeting.meeting_date.month == 6
    assert meeting.meeting_date.day == 15


def test_process_meeting_sets_tags(
    scripted_llm, llm_key, regular_user, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test that tags are set correctly."""
    scripted_llm.script = [submitted(sample_extraction_response)]

    meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=sample_transcript,
        tags=["important", "q1", "planning"],
    )

    meeting = mock_make_session.query(Meeting).first()
    assert "meeting" in meeting.tags
    assert "important" in meeting.tags
    assert "q1" in meeting.tags
    assert "planning" in meeting.tags


# ============================================================================
# Parametrized tests
# ============================================================================


@pytest.mark.parametrize(
    "name,expected_identifier",
    [
        ("John Smith", "john_smith"),
        ("ALICE JONES", "alice_jones"),
        ("bob", "bob"),
        ("Mary Jane Watson", "mary_jane_watson"),
        ("Test 123", "test_123"),
    ],
)
def test_make_identifier_various_names(name, expected_identifier):
    """Test identifier creation with various name formats."""
    result = make_identifier(name)
    assert result == expected_identifier


@pytest.mark.parametrize(
    "priority_input,expected_priority",
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("urgent", "urgent"),
        ("LOW", "medium"),  # Not lowercase - invalid
        ("critical", "medium"),  # Invalid
        (None, "medium"),
        ("", "medium"),
    ],
)
def test_action_item_priority_normalization(
    priority_input, expected_priority, mock_make_session, qdrant
):
    """Test that action item priorities are normalized correctly."""
    meeting = Meeting(
        title="Test",
        content="Test",
        sha256=create_content_hash(f"meeting:priority_{priority_input}"),
        modality="meeting",
        mime_type="text/plain",
        size=10,
    )
    mock_make_session.add(meeting)
    mock_make_session.commit()

    meetings.create_action_item_tasks(
        mock_make_session,
        meeting,
        [{"description": "Test task", "priority": priority_input}],
    )
    mock_make_session.commit()

    task = mock_make_session.query(Task).first()
    assert task.priority == expected_priority


@pytest.mark.parametrize(
    "date_str",
    [
        "2024-01-15",
        "2024-01-15T10:30:00",
        "January 15, 2024",
        "15/01/2024",
    ],
)
def test_parse_date_valid_formats(date_str):
    result = meetings.parse_date(date_str)
    assert result is not None


@pytest.mark.parametrize(
    "date_str",
    [
        "invalid",
        "",
        None,
    ],
)
def test_parse_date_invalid_formats(date_str):
    result = meetings.parse_date(date_str)
    assert result is None


def test_process_meeting_prepends_attendees_to_transcript(
    scripted_llm, llm_key, regular_user, mock_make_session, qdrant
):
    """Test that attendee names are prepended to the transcript content."""
    scripted_llm.script = [submitted({"summary": "Test", "notes": "", "action_items": []})]

    transcript = "Let's discuss the roadmap."
    result = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=transcript,
        attendee_names=["Alice", "Bob", "Charlie"],
        external_id="attendee-test-123",
    )

    assert result["status"] == "success"

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.content.startswith("Attendees: Alice, Bob, Charlie\n\n")
    assert transcript in meeting.content


def test_process_meeting_no_attendees_no_prefix(
    scripted_llm, llm_key, regular_user, mock_make_session, qdrant
):
    """Test that transcript is unchanged when no attendees provided."""
    scripted_llm.script = [submitted({"summary": "Test", "notes": "", "action_items": []})]

    transcript = "Let's discuss the roadmap."
    result = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=transcript,
        attendee_names=None,
        external_id="no-attendee-test-123",
    )

    assert result["status"] == "success"

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.content == transcript


def test_process_meeting_empty_attendees_no_prefix(
    scripted_llm, llm_key, regular_user, mock_make_session, qdrant
):
    """Test that transcript is unchanged when attendees list is empty."""
    scripted_llm.script = [submitted({"summary": "Test", "notes": "", "action_items": []})]

    transcript = "Let's discuss the roadmap."
    result = meetings.process_meeting(
        creator_id=regular_user.id,
        transcript=transcript,
        attendee_names=[],
        external_id="empty-attendee-test-123",
    )

    assert result["status"] == "success"

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.content == transcript

# ============================================================================
# Two recordings of one call
# ============================================================================


CALL_START = datetime(2026, 11, 5, 16, 30, tzinfo=timezone.utc)


def at(minutes: float) -> str:
    return (CALL_START + timedelta(minutes=minutes)).isoformat()


LATER = at(0.25)


def fireflies_account(db, user):
    account = TranscriptAccount(user_id=user.id, name=f"FF {user.id}", provider="fireflies")
    account.api_key = "k"
    db.add(account)
    db.commit()
    return account


def ingest_call(account, external_id, **overrides):
    return meetings.process_meeting(**{
        "transcript": f"Dan: Let's ship release {external_id}.",
        "title": "Weekly sync",
        "meeting_date": at(0),
        "duration_minutes": 30,
        "attendee_emails": ["regular@example.com", "chris@example.com"],
        "source_tool": "fireflies",
        "external_id": external_id,
        "transcript_account_id": account.id,
        "calendar_event_uid": "evt-1",
        **overrides,
    })


def calendar_event(db, user, uid, start):
    account = CalendarAccount(user_id=user.id, name=f"Cal {user.id}", calendar_type="caldav")
    event = CalendarEvent(
        modality="calendar",
        sha256=unique_sha256("cal"),
        event_title="Weekly sync",
        start_time=start,
        calendar_account=account,
        external_id=uid,
    )
    db.add(event)
    db.commit()
    return event


def test_a_meeting_is_linked_to_its_owners_calendar_event(regular_user, admin_user, mock_make_session, qdrant):
    """A recurring event's occurrences can share one uid; the one nearest the meeting is it."""
    db = mock_make_session
    ours = [calendar_event(db, regular_user, "evt-1", CALL_START + timedelta(days=days)) for days in (-7, 0, 7)]
    calendar_event(db, admin_user, "evt-1", CALL_START)
    calendar_event(db, regular_user, "evt-2", CALL_START)

    result = ingest_call(fireflies_account(db, regular_user), "ff-1", meeting_date=at(2))

    assert db.get(Meeting, result["meeting_id"]).calendar_event_id == ours[1].id


@pytest.mark.parametrize(
    "first, second",
    [
        # Same calendar occurrence: titles and attendee lists may differ per notetaker.
        ({}, {"meeting_date": LATER, "title": "Notetaker: weekly", "attendee_emails": ["x@other.com"]}),
        # Same call even though the calendar ids are not shared.
        ({}, {"meeting_date": LATER, "calendar_event_uid": "evt-2", "attendee_emails": ["chris@example.com"]}),
        ({}, {"meeting_date": LATER, "calendar_event_uid": None, "title": "  weekly   SYNC"}),
        ({"calendar_event_uid": None}, {"meeting_date": LATER, "attendee_emails": ["CHRIS@example.com"]}),
        # A notetaker that joined late recorded part of the same call.
        ({}, {"meeting_date": at(20), "duration_minutes": 10}),
        ({"duration_minutes": None}, {"meeting_date": at(4)}),
    ],
)
def test_a_second_recording_of_a_call_is_not_ingested(
    scripted_llm, llm_key, regular_user, sample_extraction_response, mock_make_session, qdrant, first, second
):
    scripted_llm.script = [submitted(sample_extraction_response)]
    account = fireflies_account(mock_make_session, regular_user)
    kept = ingest_call(account, "ff-1", **first)

    result = ingest_call(account, "ff-2", **second)

    assert result == {"status": "duplicate", "meeting_id": kept["meeting_id"], "external_id": "ff-2"}
    assert [m.id for m in mock_make_session.query(Meeting)] == [kept["meeting_id"]]
    assert {t.source_item_id for t in mock_make_session.query(Task)} == {kept["meeting_id"]}
    assert len(scripted_llm.requests) == 1


@pytest.mark.parametrize(
    "first, second",
    [
        ({}, {"calendar_event_uid": "evt-2", "title": "Hiring sync"}),
        ({}, {"calendar_event_uid": None, "attendee_emails": ["x@other.com"]}),
        # The owner is in every call they record, so sharing only them says nothing.
        (
            {"calendar_event_uid": None, "attendee_emails": ["regular@example.com", "ann@x.com"]},
            {"calendar_event_uid": None, "meeting_date": at(5), "attendee_emails": ["Regular@example.com", "bo@x.com"]},
        ),
        # A recording restarted after the notetaker dropped holds the rest of the call.
        ({"duration_minutes": 3}, {"meeting_date": at(6), "duration_minutes": 50}),
        ({"calendar_event_uid": None}, {"calendar_event_uid": None, "meeting_date": at(30)}),
        ({"duration_minutes": None}, {"meeting_date": at(6)}),
    ],
)
def test_different_meetings_at_similar_times_are_both_ingested(regular_user, mock_make_session, qdrant, first, second):
    account = fireflies_account(mock_make_session, regular_user)
    ingest_call(account, "ff-1", **first)

    result = ingest_call(account, "ff-2", **second)

    assert result["status"] == "success"
    assert mock_make_session.query(Meeting).count() == 2


def test_another_users_recording_of_the_same_call_is_ingested(regular_user, admin_user, mock_make_session, qdrant):
    ingest_call(fireflies_account(mock_make_session, regular_user), "ff-1")

    result = ingest_call(fireflies_account(mock_make_session, admin_user), "ff-2")

    assert result["status"] == "success"
    assert mock_make_session.query(Meeting).count() == 2


def test_a_call_whose_extraction_failed_still_recognises_its_second_recording(
    scripted_llm, llm_key, regular_user, mock_make_session, qdrant
):
    scripted_llm.script = [connection_dropped]
    account = fireflies_account(mock_make_session, regular_user)
    first = ingest_call(account, "ff-1", calendar_event_uid=None)

    result = ingest_call(account, "ff-2", calendar_event_uid=None, meeting_date=LATER)

    assert first["status"] == "error"
    assert result == {"status": "duplicate", "meeting_id": first["meeting_id"], "external_id": "ff-2"}


def stored_call(db, account, people, *, content, minutes=0.0, duration=30, title="Weekly sync", statuses=("pending", "done")):
    meeting = Meeting(
        title=title,
        content=content,
        sha256=unique_sha256(content),
        modality="meeting",
        meeting_date=CALL_START + timedelta(minutes=minutes),
        duration_minutes=duration,
        source_tool="fireflies",
        transcript_account=account,
        people=list(people),
    )
    meeting.chunks = [_make_mock_chunk(0)]
    db.add_all([
        meeting,
        *(
            Task(task_title=f"{status} task", sha256=unique_sha256(status), status=status, source_item=meeting)
            for status in statuses
        ),
    ])
    db.commit()
    return meeting


@pytest.fixture
def duplicated_call(mock_make_session, regular_user, admin_user, request):
    db = mock_make_session
    dan = Person(identifier="dan", display_name="Dan", contact_info={"email": "dan@example.com"}, user_id=regular_user.id)
    chris = Person(identifier="chris", display_name="Chris", contact_info={"email": ["chris@example.com"]})
    eve = Person(identifier="eve", display_name="Eve", contact_info={"email": "eve@example.com"})
    account = fireflies_account(db, regular_user)
    (first_content, first_statuses), (second_content, second_statuses) = request.param
    copies = [
        stored_call(db, account, [dan, chris], content=first_content, statuses=first_statuses),
        stored_call(db, account, [chris], content=second_content, minutes=0.25, statuses=second_statuses),
    ]
    bystanders = [
        stored_call(db, account, [dan, chris], content="other", title="Hiring sync"),
        # Shares only the owner with the copies.
        stored_call(db, account, [dan, eve], content="eve's call", minutes=1),
        # Starts after the copies end.
        stored_call(db, account, [chris], content="next call", minutes=31),
        stored_call(db, fireflies_account(db, admin_user), [dan, chris], content=first_content),
    ]
    return copies, bystanders


WORKED_ON = ("pending", "done")


@pytest.mark.parametrize(
    "duplicated_call", [(("Dan: hi", WORKED_ON), ("Dan: hi. Chris: let's ship it.", WORKED_ON))], indirect=True
)
def test_dedupe_meetings_deletes_second_recordings(duplicated_call, mock_make_session, qdrant):
    copies, bystanders = duplicated_call
    keeper, dropped = copies[1], copies[0]
    ids = {"keeper": keeper.id, "dropped": dropped.id}
    done_task = mock_make_session.query(Task.id).filter(Task.source_item_id == dropped.id, Task.status == "done").scalar()

    result = meetings.dedupe_meetings()

    db = mock_make_session
    db.expire_all()
    assert result["groups"] == [{"keep": ids["keeper"], "delete": [ids["dropped"]]}]
    assert {m.id for m in db.query(Meeting)} == {ids["keeper"], *(b.id for b in bystanders)}
    assert db.query(Chunk).filter(Chunk.source_id == ids["dropped"]).count() == 0
    assert {(t.task_title, t.source_item_id) for t in db.query(Task) if t.source_item_id in (ids["dropped"], None)} == {
        ("done task", None)
    }
    assert db.get(Task, done_task) is not None


@pytest.mark.parametrize(
    "duplicated_call, processed, kept",
    [
        ((("Dan: hi", WORKED_ON), ("Dan: hi. Chris: let's ship it.", WORKED_ON)), [], 1),
        ((("Dan: hi", WORKED_ON), ("Dan: yo", WORKED_ON)), [], 0),
        # Tasks someone worked on stay beside their meeting rather than a pending copy of them.
        ((("Dan: hi", WORKED_ON), ("Dan: hi. Chris: let's ship it.", ("pending",))), [], 0),
        ((("Dan: hi", ("pending",)), ("Dan: hi. Chris: let's ship it.", WORKED_ON)), [0], 1),
        # A summary and embeddings beat a longer transcript that has neither.
        ((("Dan: hi", WORKED_ON), ("Dan: hi. Chris: let's ship it.", WORKED_ON)), [0], 0),
    ],
    indirect=["duplicated_call"],
)
def test_dedupe_meetings_dry_run_picks_the_keeper_and_changes_nothing(
    duplicated_call, processed, kept, mock_make_session, qdrant
):
    copies, _ = duplicated_call
    for i in processed:
        copies[i].extraction_status = "complete"
    mock_make_session.commit()
    before = (mock_make_session.query(Meeting).count(), mock_make_session.query(Task).count())

    result = meetings.dedupe_meetings(dry_run=True)

    assert result["groups"] == [{"keep": copies[kept].id, "delete": [copies[1 - kept].id]}]
    assert (mock_make_session.query(Meeting).count(), mock_make_session.query(Task).count()) == before


def test_dedupe_meetings_groups_only_recordings_that_overlap_each_other(mock_make_session, regular_user, qdrant):
    db = mock_make_session
    chris = Person(identifier="chris", display_name="Chris", contact_info={"email": "chris@example.com"})
    account = fireflies_account(db, regular_user)
    first, second, _ = (
        stored_call(db, account, [chris], content=f"copy {i}", minutes=start, duration=60)
        for i, start in enumerate((0, 20, 40))
    )

    result = meetings.dedupe_meetings(dry_run=True)

    assert result["groups"] == [{"keep": first.id, "delete": [second.id]}]
