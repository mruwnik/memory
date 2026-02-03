"""Tests for people Celery tasks.

The people module has been simplified - only sync_person_tidbit remains as a task.
Person creation/update is now synchronous in the MCP layer.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from memory.common.db.models import Person, PersonTidbit
from memory.common.db.models.source_item import Chunk
from memory.workers.tasks import people


def _make_mock_chunk(source_id: int) -> Chunk:
    """Create a mock chunk for testing with a unique ID."""
    return Chunk(
        id=str(uuid.uuid4()),
        content="test chunk content",
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={"source_id": source_id, "tags": ["test"]},
        collection_name="person_tidbit",
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
            with patch("memory.common.content_processing.push_to_qdrant"):
                yield db_session


@pytest.fixture
def sample_person(db_session):
    """Create a sample person for tidbit tests."""
    person = Person(
        identifier="test_person",
        display_name="Test Person",
        aliases=["@test"],
        contact_info={"email": "test@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


# =============================================================================
# Tests for sync_person_tidbit
# =============================================================================


def test_sync_person_tidbit_success(mock_make_session, sample_person, qdrant):
    """Test creating a tidbit for an existing person."""
    result = people.sync_person_tidbit(
        person_id=sample_person.id,
        content="This is a test tidbit",
        tidbit_type="note",
        tags=["test"],
    )

    assert result["status"] == "processed"
    assert result["person_id"] == sample_person.id
    assert result["person_identifier"] == "test_person"

    tidbit = (
        mock_make_session.query(PersonTidbit)
        .filter_by(person_id=sample_person.id)
        .first()
    )
    assert tidbit is not None
    assert tidbit.content == "This is a test tidbit"
    assert tidbit.tidbit_type == "note"
    assert tidbit.tags == ["test"]


def test_sync_person_tidbit_person_not_found(mock_make_session, qdrant):
    """Test creating a tidbit for a non-existent person."""
    result = people.sync_person_tidbit(
        person_id=99999,
        content="This should fail",
    )

    assert result["status"] == "not_found"
    assert result["person_id"] == 99999


def test_sync_person_tidbit_with_access_control(mock_make_session, sample_person, qdrant):
    """Test that tidbits can have project_id and sensitivity."""
    result = people.sync_person_tidbit(
        person_id=sample_person.id,
        content="Confidential note",
        tidbit_type="preference",
        project_id=None,  # Creator-only
        sensitivity="confidential",
        creator_id=42,
    )

    assert result["status"] == "processed"

    tidbit = (
        mock_make_session.query(PersonTidbit)
        .filter_by(person_id=sample_person.id)
        .first()
    )
    assert tidbit is not None
    assert tidbit.sensitivity == "confidential"
    assert tidbit.creator_id == 42
    assert tidbit.project_id is None


def test_sync_person_tidbit_various_types(mock_make_session, sample_person, qdrant):
    """Test creating tidbits of different types."""
    tidbit_types = ["note", "preference", "fact", "observation"]

    for tidbit_type in tidbit_types:
        result = people.sync_person_tidbit(
            person_id=sample_person.id,
            content=f"Content for {tidbit_type}",
            tidbit_type=tidbit_type,
        )

        assert result["status"] == "processed"

    # Verify all tidbits were created
    tidbits = (
        mock_make_session.query(PersonTidbit)
        .filter_by(person_id=sample_person.id)
        .all()
    )
    assert len(tidbits) == len(tidbit_types)


def test_sync_person_tidbit_unicode(mock_make_session, sample_person, qdrant):
    """Test creating a tidbit with unicode content."""
    result = people.sync_person_tidbit(
        person_id=sample_person.id,
        content="Привет мир 🌍 日本語",
        tidbit_type="note",
        tags=["日本語", "emoji"],
    )

    assert result["status"] == "processed"

    tidbit = (
        mock_make_session.query(PersonTidbit)
        .filter_by(person_id=sample_person.id)
        .first()
    )
    assert tidbit is not None
    assert tidbit.content == "Привет мир 🌍 日本語"
    assert "日本語" in tidbit.tags
