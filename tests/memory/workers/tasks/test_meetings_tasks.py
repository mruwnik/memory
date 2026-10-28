"""Tests for meeting Celery tasks."""

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import anthropic
import fakeredis
import httpx2
import pytest

from memory.agents import triggers
from memory.agents.triggers import IngestConfig
from memory.common.access_control import user_can_access
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    HumanUser,
    Person,
    Project,
    Task,
    TranscriptAccount,
)
from memory.common.db.models.source_items import Meeting
from memory.common.db.models.source_item import Chunk
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


def test_parse_due_date_valid_iso():
    """Test parsing valid ISO date string."""
    result = meetings.parse_due_date("2024-01-15")
    assert result is not None
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15


def test_parse_due_date_with_time():
    """Test parsing date with time."""
    result = meetings.parse_due_date("2024-01-15T14:30:00")
    assert result is not None
    assert result.hour == 14
    assert result.minute == 30


def test_parse_due_date_natural_format():
    """Test parsing natural date format."""
    result = meetings.parse_due_date("January 15, 2024")
    assert result is not None
    assert result.year == 2024
    assert result.month == 1


def test_parse_due_date_none():
    """Test parsing None returns None."""
    result = meetings.parse_due_date(None)
    assert result is None


def test_parse_due_date_empty_string():
    """Test parsing empty string returns None."""
    result = meetings.parse_due_date("")
    assert result is None


def test_parse_due_date_invalid():
    """Test parsing invalid date returns None."""
    result = meetings.parse_due_date("not a date")
    assert result is None


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

    result = meetings.extract_and_update_meeting(db_session, meeting)

    request = scripted_llm.requests[0]
    assert (result["status"], result["tasks_created"], meeting.summary) == ("success", 1, "We met.")
    assert request["api_key"] == "sk-test-default"
    assert request["messages"][0].content[0].text == "Meeting transcript:\nAlice: hi"


def test_meetings_without_a_key_are_kept_unextracted(db_session, regular_user, scripted_llm):
    meeting = meeting_for(db_session, regular_user.id)

    result = meetings.extract_and_update_meeting(db_session, meeting)

    assert (result["status"], meeting.extraction_status, scripted_llm.requests) == ("skipped", "skipped", [])


@pytest.mark.parametrize("failure", [key_rejected, connection_dropped])
def test_a_failed_extraction_marks_the_meeting_failed(db_session, regular_user, llm_key, scripted_llm, failure):
    scripted_llm.script = [failure]
    meeting = meeting_for(db_session, regular_user.id)

    result = meetings.extract_and_update_meeting(db_session, meeting)

    assert (result["status"], meeting.extraction_status) == ("error", "failed")
    assert "sk-test-default" not in result["error"]


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
def test_parse_due_date_valid_formats(date_str):
    """Test due date parsing succeeds with valid formats."""
    result = meetings.parse_due_date(date_str)
    assert result is not None


@pytest.mark.parametrize(
    "date_str",
    [
        "invalid",
        "",
        None,
    ],
)
def test_parse_due_date_invalid_formats(date_str):
    """Test due date parsing returns None for invalid formats."""
    result = meetings.parse_due_date(date_str)
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
