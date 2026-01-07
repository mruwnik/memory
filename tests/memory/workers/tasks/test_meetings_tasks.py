"""Tests for meeting Celery tasks."""

import uuid
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

import pytest

from memory.common.db.models import Person, Task
from memory.common.db.models.source_items import Meeting
from memory.common.db.models.source_item import Chunk
from memory.workers.tasks import meetings
from memory.workers.tasks.content_processing import create_content_hash


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
            side_effect=lambda item: [_make_mock_chunk(item.id or 1)],
        ):
            with patch("memory.workers.tasks.content_processing.push_to_qdrant"):
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
# Tests for parse_extraction_response
# ============================================================================


def test_parse_extraction_response_valid_json():
    """Test parsing valid JSON response."""
    response = '{"summary": "Test summary", "notes": "Test notes", "action_items": []}'
    result = meetings.parse_extraction_response(response)
    assert result == {"summary": "Test summary", "notes": "Test notes", "action_items": []}


def test_parse_extraction_response_with_markdown_code_block():
    """Test parsing JSON wrapped in markdown code blocks."""
    response = """```json
{"summary": "Test summary", "notes": "Test notes", "action_items": []}
```"""
    result = meetings.parse_extraction_response(response)
    assert result == {"summary": "Test summary", "notes": "Test notes", "action_items": []}


def test_parse_extraction_response_with_plain_code_block():
    """Test parsing JSON wrapped in plain code blocks."""
    response = """```
{"summary": "Test", "notes": "Notes", "action_items": []}
```"""
    result = meetings.parse_extraction_response(response)
    assert result == {"summary": "Test", "notes": "Notes", "action_items": []}


def test_parse_extraction_response_with_whitespace():
    """Test parsing JSON with surrounding whitespace."""
    response = """
    {"summary": "Test", "notes": "Notes", "action_items": []}
    """
    result = meetings.parse_extraction_response(response)
    assert result["summary"] == "Test"


def test_parse_extraction_response_invalid_json():
    """Test parsing invalid JSON returns empty defaults."""
    response = "This is not valid JSON at all"
    result = meetings.parse_extraction_response(response)
    assert result == {"summary": "", "notes": "", "action_items": []}


def test_parse_extraction_response_with_action_items():
    """Test parsing response with action items."""
    response = """{
        "summary": "Meeting summary",
        "notes": "Key points",
        "action_items": [
            {"description": "Task 1", "assignee": "Alice", "due_date": "2024-01-15", "priority": "high"}
        ]
    }"""
    result = meetings.parse_extraction_response(response)
    assert len(result["action_items"]) == 1
    assert result["action_items"][0]["description"] == "Task 1"
    assert result["action_items"][0]["assignee"] == "Alice"


def test_parse_extraction_response_multiline_code_block():
    """Test parsing multiline JSON in code block."""
    response = """```json
{
    "summary": "Multi-line summary",
    "notes": "- Point 1\\n- Point 2",
    "action_items": [
        {
            "description": "Do something",
            "assignee": null,
            "due_date": null,
            "priority": null
        }
    ]
}
```"""
    result = meetings.parse_extraction_response(response)
    assert result["summary"] == "Multi-line summary"
    assert len(result["action_items"]) == 1


# ============================================================================
# Tests for find_person_by_name
# ============================================================================


def test_find_person_by_name_exact_match(mock_make_session, qdrant):
    """Test finding person by exact display name match."""
    person = Person(
        identifier="alice_chen",
        display_name="Alice Chen",
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:alice_chen"),
        size=0,
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:bob_smith"),
        size=0,
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:charlie_jones"),
        size=0,
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:dave_wilson"),
        size=0,
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:eve_brown"),
        size=0,
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
# Tests for _make_identifier
# ============================================================================


def test_make_identifier_basic():
    """Test basic identifier creation."""
    result = meetings._make_identifier("John Smith")
    assert result == "john_smith"


def test_make_identifier_with_extra_spaces():
    """Test identifier with extra spaces - multiple spaces collapse to single underscore."""
    result = meetings._make_identifier("  John   Smith  ")
    assert result == "john_smith"  # Multiple spaces collapse to single underscore


def test_make_identifier_with_special_chars():
    """Test identifier strips special characters."""
    result = meetings._make_identifier("John O'Brien-Smith")
    assert result == "john_obriensmith"


def test_make_identifier_unicode():
    """Test identifier with unicode characters."""
    result = meetings._make_identifier("José García")
    assert result == "josé_garcía"  # Unicode preserved (isalnum includes unicode)


def test_make_identifier_numbers():
    """Test identifier preserves numbers."""
    result = meetings._make_identifier("User 123")
    assert result == "user_123"


# ============================================================================
# Tests for _find_or_create_person
# ============================================================================


def test_find_or_create_person_finds_existing(mock_make_session, qdrant):
    """Test finding an existing person."""
    person = Person(
        identifier="existing_person",
        display_name="Existing Person",
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:existing_person"),
        size=0,
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    result, created = meetings._find_or_create_person(mock_make_session, "Existing Person")
    assert result.identifier == "existing_person"
    assert created is False


def test_find_or_create_person_creates_new(mock_make_session, qdrant):
    """Test creating a new person."""
    result, created = meetings._find_or_create_person(mock_make_session, "New Person")
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:john_doe"),
        size=0,
    )
    mock_make_session.add(person)
    mock_make_session.commit()

    # Create someone with a name that generates the same identifier
    result, created = meetings._find_or_create_person(mock_make_session, "John Doe")
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:alice"),
        size=0,
    )
    bob = Person(
        identifier="bob",
        display_name="Bob",
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:bob"),
        size=0,
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:alice2"),
        size=0,
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
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:alice3"),
        size=0,
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

    tasks = mock_make_session.query(Task).all()
    assert len(tasks) == 2


def test_create_action_item_tasks_with_assignee(mock_make_session, qdrant):
    """Test creating tasks with assignee tags."""
    alice = Person(
        identifier="alice",
        display_name="Alice",
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:alice4"),
        size=0,
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
# Tests for call_extraction_llm
# ============================================================================


@patch("memory.workers.tasks.meetings.llms.summarize")
def test_call_extraction_llm_success(mock_summarize):
    """Test successful LLM extraction call."""
    mock_summarize.return_value = '{"summary": "Test", "notes": "Notes", "action_items": []}'

    result = meetings.call_extraction_llm("Test transcript")

    assert result["summary"] == "Test"
    mock_summarize.assert_called_once()


@patch("memory.workers.tasks.meetings.llms.summarize")
def test_call_extraction_llm_uses_custom_prompts(mock_summarize):
    """Test that custom prompts are used."""
    mock_summarize.return_value = '{"summary": "Custom", "notes": "", "action_items": []}'

    meetings.call_extraction_llm(
        "Transcript",
        extraction_prompt="Custom prompt: {transcript}",
        system_prompt="Custom system",
        model="custom-model",
    )

    call_args = mock_summarize.call_args
    assert "Custom prompt: Transcript" == call_args[0][0]
    assert call_args[1]["system_prompt"] == "Custom system"
    assert call_args[1]["model"] == "custom-model"


# ============================================================================
# Tests for process_meeting task
# ============================================================================


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_success(
    mock_extraction, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test successful meeting processing."""
    mock_extraction.return_value = sample_extraction_response

    result = meetings.process_meeting(
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


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_idempotent(
    mock_extraction, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test that duplicate external_id skips processing."""
    mock_extraction.return_value = sample_extraction_response

    # First call
    result1 = meetings.process_meeting(
        transcript=sample_transcript,
        external_id="duplicate-123",
    )
    assert result1["status"] == "success"

    # Second call with same external_id
    result2 = meetings.process_meeting(
        transcript="Different transcript",
        external_id="duplicate-123",
    )
    assert result2["status"] == "exists"
    assert result2["meeting_id"] == result1["meeting_id"]

    # Verify only one meeting exists
    count = mock_make_session.query(Meeting).count()
    assert count == 1


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_minimal(mock_extraction, sample_transcript, mock_make_session, qdrant):
    """Test meeting processing with minimal data."""
    mock_extraction.return_value = {
        "summary": "Brief summary",
        "notes": "",
        "action_items": [],
    }

    result = meetings.process_meeting(transcript=sample_transcript)

    assert result["status"] == "success"
    assert result["tasks_created"] == 0
    assert result["attendees_linked"] == 0


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_handles_extraction_error(
    mock_extraction, sample_transcript, mock_make_session, qdrant
):
    """Test that extraction errors are handled gracefully."""
    mock_extraction.side_effect = Exception("LLM API error")

    result = meetings.process_meeting(
        transcript=sample_transcript,
        external_id="error-test",
    )

    assert result["status"] == "error"
    assert "LLM API error" in result["message"]

    # Verify meeting was created but marked as failed
    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.extraction_status == "failed"


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_with_existing_people(
    mock_extraction, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test meeting with existing attendees."""
    mock_extraction.return_value = sample_extraction_response

    # Create existing people
    alice = Person(
        identifier="alice",
        display_name="Alice",
        modality="person",
        mime_type="text/plain",
        sha256=create_content_hash("person:alice5"),
        size=0,
    )
    mock_make_session.add(alice)
    mock_make_session.commit()

    result = meetings.process_meeting(
        transcript=sample_transcript,
        attendee_names=["Alice", "New Bob"],
    )

    assert result["status"] == "success"
    assert result["attendees_linked"] == 1  # Alice
    assert result["attendees_created"] == 1  # New Bob


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_parses_meeting_date(
    mock_extraction, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test that meeting date is parsed correctly."""
    mock_extraction.return_value = sample_extraction_response

    meetings.process_meeting(
        transcript=sample_transcript,
        meeting_date="2024-06-15T14:30:00",
    )

    meeting = mock_make_session.query(Meeting).first()
    assert meeting.meeting_date is not None
    assert meeting.meeting_date.year == 2024
    assert meeting.meeting_date.month == 6
    assert meeting.meeting_date.day == 15


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_sets_tags(
    mock_extraction, sample_extraction_response, sample_transcript, mock_make_session, qdrant
):
    """Test that tags are set correctly."""
    mock_extraction.return_value = sample_extraction_response

    meetings.process_meeting(
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
    result = meetings._make_identifier(name)
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


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_prepends_attendees_to_transcript(
    mock_extraction, mock_make_session, qdrant
):
    """Test that attendee names are prepended to the transcript content."""
    mock_extraction.return_value = {"summary": "Test", "notes": "", "action_items": []}

    transcript = "Let's discuss the roadmap."
    result = meetings.process_meeting(
        transcript=transcript,
        attendee_names=["Alice", "Bob", "Charlie"],
        external_id="attendee-test-123",
    )

    assert result["status"] == "success"

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.content.startswith("Attendees: Alice, Bob, Charlie\n\n")
    assert transcript in meeting.content


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_no_attendees_no_prefix(
    mock_extraction, mock_make_session, qdrant
):
    """Test that transcript is unchanged when no attendees provided."""
    mock_extraction.return_value = {"summary": "Test", "notes": "", "action_items": []}

    transcript = "Let's discuss the roadmap."
    result = meetings.process_meeting(
        transcript=transcript,
        attendee_names=None,
        external_id="no-attendee-test-123",
    )

    assert result["status"] == "success"

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.content == transcript


@patch("memory.workers.tasks.meetings.call_extraction_llm")
def test_process_meeting_empty_attendees_no_prefix(
    mock_extraction, mock_make_session, qdrant
):
    """Test that transcript is unchanged when attendees list is empty."""
    mock_extraction.return_value = {"summary": "Test", "notes": "", "action_items": []}

    transcript = "Let's discuss the roadmap."
    result = meetings.process_meeting(
        transcript=transcript,
        attendee_names=[],
        external_id="empty-attendee-test-123",
    )

    assert result["status"] == "success"

    meeting = mock_make_session.query(Meeting).first()
    assert meeting is not None
    assert meeting.content == transcript
