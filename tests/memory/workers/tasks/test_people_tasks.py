"""Tests for people Celery tasks."""

import uuid
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest

from memory.common.db.models import Person
from memory.common.db.models.source_item import Chunk
from memory.workers.tasks import people
from memory.workers.tasks.content_processing import create_content_hash


def _make_mock_chunk(source_id: int) -> Chunk:
    """Create a mock chunk for testing with a unique ID."""
    return Chunk(
        id=str(uuid.uuid4()),
        content="test chunk content",
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={"source_id": source_id, "tags": ["test"]},
        collection_name="person",
    )


@pytest.fixture
def mock_make_session(db_session):
    """Mock make_session and embedding functions for task tests."""

    @contextmanager
    def _mock_session():
        yield db_session

    with patch("memory.workers.tasks.people.make_session", _mock_session):
        # Mock embedding to return a fake chunk
        with patch(
            "memory.common.embedding.embed_source_item",
            side_effect=lambda item: [_make_mock_chunk(item.id or 1)],
        ):
            # Mock push_to_qdrant to do nothing
            with patch("memory.workers.tasks.content_processing.push_to_qdrant"):
                yield db_session


@pytest.fixture
def person_data():
    """Standard person test data."""
    return {
        "identifier": "alice_chen",
        "display_name": "Alice Chen",
        "aliases": ["@alice_c", "alice.chen@work.com"],
        "contact_info": {"email": "alice@example.com", "phone": "555-1234"},
        "tags": ["work", "engineering"],
        "notes": "Tech lead on Platform team.",
    }


@pytest.fixture
def minimal_person_data():
    """Minimal person test data."""
    return {
        "identifier": "bob_smith",
        "display_name": "Bob Smith",
    }


def test_sync_person_success(person_data, mock_make_session, qdrant):
    """Test successful person sync."""
    result = people.sync_person(**person_data)

    # Verify the Person was created in the database
    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    assert person is not None
    assert person.identifier == "alice_chen"
    assert person.display_name == "Alice Chen"
    assert person.aliases == ["@alice_c", "alice.chen@work.com"]
    assert person.contact_info == {"email": "alice@example.com", "phone": "555-1234"}
    assert person.tags == ["work", "engineering"]
    assert person.content == "Tech lead on Platform team."
    assert person.modality == "person"

    # Verify the result
    assert result["status"] == "processed"
    assert "person_id" in result


def test_sync_person_minimal_data(minimal_person_data, mock_make_session, qdrant):
    """Test person sync with minimal required data."""
    result = people.sync_person(**minimal_person_data)

    person = mock_make_session.query(Person).filter_by(identifier="bob_smith").first()
    assert person is not None
    assert person.identifier == "bob_smith"
    assert person.display_name == "Bob Smith"
    assert person.aliases == []
    assert person.contact_info == {}
    assert person.tags == []
    assert person.content is None

    assert result["status"] == "processed"


def test_sync_person_already_exists(person_data, mock_make_session, qdrant):
    """Test sync when person already exists."""
    # Create the person first
    sha256 = create_content_hash(f"person:{person_data['identifier']}")
    existing_person = Person(
        identifier=person_data["identifier"],
        display_name=person_data["display_name"],
        aliases=person_data["aliases"],
        contact_info=person_data["contact_info"],
        tags=person_data["tags"],
        content=person_data["notes"],
        modality="person",
        mime_type="text/plain",
        sha256=sha256,
        size=len(person_data["notes"]),
    )
    mock_make_session.add(existing_person)
    mock_make_session.commit()

    # Try to sync again
    result = people.sync_person(**person_data)

    assert result["status"] == "already_exists"
    assert result["person_id"] == existing_person.id

    # Verify no duplicate was created
    count = mock_make_session.query(Person).filter_by(identifier="alice_chen").count()
    assert count == 1


def test_update_person_display_name(person_data, mock_make_session, qdrant):
    """Test updating display name."""
    # Create person first
    people.sync_person(**person_data)

    # Update display name
    result = people.update_person(
        identifier="alice_chen",
        display_name="Alice M. Chen",
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    assert person.display_name == "Alice M. Chen"
    # Other fields unchanged
    assert person.aliases == ["@alice_c", "alice.chen@work.com"]


def test_update_person_merge_aliases(person_data, mock_make_session, qdrant):
    """Test that aliases are merged, not replaced."""
    # Create person first
    people.sync_person(**person_data)

    # Update with new aliases
    result = people.update_person(
        identifier="alice_chen",
        aliases=["@alice_chen", "alice@company.com"],
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    # Should be union of old and new
    assert set(person.aliases) == {
        "@alice_c",
        "alice.chen@work.com",
        "@alice_chen",
        "alice@company.com",
    }


def test_update_person_merge_contact_info(person_data, mock_make_session, qdrant):
    """Test that contact_info is deep merged."""
    # Create person first
    people.sync_person(**person_data)

    # Update with new contact info
    result = people.update_person(
        identifier="alice_chen",
        contact_info={"twitter": "@alice_c", "phone": "555-5678"},  # Update existing
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    # Should have all keys
    assert person.contact_info["email"] == "alice@example.com"  # Original
    assert person.contact_info["phone"] == "555-5678"  # Updated
    assert person.contact_info["twitter"] == "@alice_c"  # New


def test_update_person_merge_tags(person_data, mock_make_session, qdrant):
    """Test that tags are merged, not replaced."""
    # Create person first
    people.sync_person(**person_data)

    # Update with new tags
    result = people.update_person(
        identifier="alice_chen",
        tags=["climbing", "london"],
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    # Should be union of old and new
    assert set(person.tags) == {"work", "engineering", "climbing", "london"}


def test_update_person_append_notes(person_data, mock_make_session, qdrant):
    """Test that notes are appended by default."""
    # Create person first
    people.sync_person(**person_data)

    # Update with new notes
    result = people.update_person(
        identifier="alice_chen",
        notes="Also enjoys rock climbing.",
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    # Should be appended with separator
    assert "Tech lead on Platform team." in person.content
    assert "Also enjoys rock climbing." in person.content
    assert "---" in person.content


def test_update_person_replace_notes(person_data, mock_make_session, qdrant):
    """Test replacing notes instead of appending."""
    # Create person first
    people.sync_person(**person_data)

    # Replace notes
    result = people.update_person(
        identifier="alice_chen",
        notes="Completely new notes.",
        replace_notes=True,
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    assert person.content == "Completely new notes."
    assert "Tech lead" not in person.content


def test_update_person_not_found(mock_make_session, qdrant):
    """Test updating a person that doesn't exist."""
    result = people.update_person(
        identifier="nonexistent_person",
        display_name="New Name",
    )

    assert result["status"] == "not_found"
    assert result["identifier"] == "nonexistent_person"


def test_update_person_no_changes(person_data, mock_make_session, qdrant):
    """Test update with no actual changes."""
    # Create person first
    people.sync_person(**person_data)

    # Update with nothing
    result = people.update_person(identifier="alice_chen")

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="alice_chen").first()
    # Should be unchanged
    assert person.display_name == "Alice Chen"


@pytest.mark.parametrize(
    "identifier,display_name,tags",
    [
        ("john_doe", "John Doe", []),
        ("jane_smith", "Jane Smith", ["friend"]),
        ("bob_jones", "Bob Jones", ["work", "climbing", "london"]),
    ],
)
def test_sync_person_various_configurations(identifier, display_name, tags, mock_make_session, qdrant):
    """Test sync_person with various configurations."""
    result = people.sync_person(
        identifier=identifier,
        display_name=display_name,
        tags=tags,
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier=identifier).first()
    assert person is not None
    assert person.display_name == display_name
    assert person.tags == tags


def test_deep_merge_helper():
    """Test the _deep_merge helper function."""
    base = {
        "a": 1,
        "b": {"c": 2, "d": 3},
        "e": 4,
    }
    updates = {
        "b": {"c": 5, "f": 6},
        "g": 7,
    }

    result = people._deep_merge(base, updates)

    assert result == {
        "a": 1,
        "b": {"c": 5, "d": 3, "f": 6},
        "e": 4,
        "g": 7,
    }


def test_deep_merge_nested():
    """Test deep merge with deeply nested structures."""
    base = {
        "level1": {
            "level2": {
                "level3": {"a": 1},
            },
        },
    }
    updates = {
        "level1": {
            "level2": {
                "level3": {"b": 2},
            },
        },
    }

    result = people._deep_merge(base, updates)

    assert result == {
        "level1": {
            "level2": {
                "level3": {"a": 1, "b": 2},
            },
        },
    }


def test_sync_person_unicode(mock_make_session, qdrant):
    """Test sync_person with unicode content."""
    result = people.sync_person(
        identifier="unicode_person",
        display_name="日本語 名前",
        notes="Привет мир 🌍",
        tags=["日本語", "emoji"],
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="unicode_person").first()
    assert person is not None
    assert person.display_name == "日本語 名前"
    assert person.content == "Привет мир 🌍"


def test_sync_person_empty_notes(mock_make_session, qdrant):
    """Test sync_person with empty notes."""
    result = people.sync_person(
        identifier="empty_notes_person",
        display_name="Empty Notes Person",
        notes="",
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="empty_notes_person").first()
    assert person is not None
    assert person.content == ""


def test_update_person_first_notes(mock_make_session, qdrant):
    """Test adding notes to a person who had no notes."""
    # Create person without notes
    people.sync_person(
        identifier="no_notes_person",
        display_name="No Notes Person",
    )

    # Add notes
    result = people.update_person(
        identifier="no_notes_person",
        notes="First notes!",
    )

    assert result["status"] == "processed"

    person = mock_make_session.query(Person).filter_by(identifier="no_notes_person").first()
    assert person.content == "First notes!"
    # Should not have separator when there were no previous notes
    assert "---" not in person.content
