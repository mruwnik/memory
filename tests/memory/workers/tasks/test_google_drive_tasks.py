"""Tests for Google Drive syncing tasks."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import GoogleDoc
from memory.workers.tasks.google_drive import (
    _serialize_file_data,
    _deserialize_file_data,
    _needs_reindex,
    _create_google_doc,
    _update_existing_doc,
)
from memory.parsers.google_drive import GoogleFileData
from memory.common.db import connection as db_connection


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.fixture
def sample_file_data() -> GoogleFileData:
    """Sample Google file data for testing."""
    return GoogleFileData(
        file_id="abc123",
        title="Test Document",
        mime_type="text/plain",
        original_mime_type="application/vnd.google-apps.document",
        folder_path="/My Drive/Docs",
        owner="user@example.com",
        last_modified_by="editor@example.com",
        modified_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        created_at=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        content="This is the document content.",
        content_hash="hash123",
        size=1024,
        word_count=5,
    )


@pytest.fixture
def mock_folder():
    """Mock GoogleFolder for testing."""
    folder = Mock()
    folder.id = 1
    folder.folder_name = "Test Folder"
    folder.tags = ["test", "docs"]
    return folder


@pytest.fixture
def mock_existing_doc():
    """Mock existing GoogleDoc."""
    doc = Mock(spec=GoogleDoc)
    doc.id = 100
    doc.google_file_id = "abc123"
    doc.modality = "doc"
    doc.content_hash = "old_hash"
    doc.google_modified_at = datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
    doc.chunks = []
    return doc


# Tests for _serialize_file_data / _deserialize_file_data


def test_serialize_file_data_converts_dates(sample_file_data):
    """Serializes datetime fields to ISO strings."""
    result = _serialize_file_data(sample_file_data)

    assert result["modified_at"] == "2024-01-15T10:30:00+00:00"
    assert result["created_at"] == "2024-01-01T09:00:00+00:00"
    assert result["file_id"] == "abc123"
    assert result["title"] == "Test Document"


def test_serialize_file_data_handles_none_dates():
    """Handles None dates correctly."""
    data = GoogleFileData(
        file_id="123",
        title="Test",
        mime_type="text/plain",
        original_mime_type="text/plain",
        folder_path="/",
        owner="user",
        last_modified_by="user",
        modified_at=None,
        created_at=None,
        content="content",
        content_hash="hash",
        size=100,
        word_count=1,
    )

    result = _serialize_file_data(data)

    assert result["modified_at"] is None
    assert result["created_at"] is None


def test_deserialize_file_data_restores_dates(sample_file_data):
    """Deserializes ISO strings back to datetime."""
    serialized = _serialize_file_data(sample_file_data)
    result = _deserialize_file_data(serialized)

    assert result["file_id"] == sample_file_data["file_id"]
    assert result["title"] == sample_file_data["title"]
    assert result["modified_at"] == sample_file_data["modified_at"]
    assert result["created_at"] == sample_file_data["created_at"]


# Tests for _needs_reindex


def test_needs_reindex_true_when_hash_different(mock_existing_doc, sample_file_data):
    """Returns True when content hash differs."""
    mock_existing_doc.content_hash = "different_hash"

    assert _needs_reindex(mock_existing_doc, sample_file_data) is True


def test_needs_reindex_true_when_newer_modified_time(mock_existing_doc, sample_file_data):
    """Returns True when new data has newer modified time."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    sample_file_data["modified_at"] = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    assert _needs_reindex(mock_existing_doc, sample_file_data) is True


def test_needs_reindex_false_when_unchanged(mock_existing_doc, sample_file_data):
    """Returns False when content hash and time are same or older."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = datetime(2024, 1, 20, 0, 0, 0, tzinfo=timezone.utc)

    assert _needs_reindex(mock_existing_doc, sample_file_data) is False


def test_needs_reindex_handles_none_modified_times(mock_existing_doc, sample_file_data):
    """Handles None modified times gracefully."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = None
    sample_file_data["modified_at"] = None

    assert _needs_reindex(mock_existing_doc, sample_file_data) is False


# Tests for _create_google_doc


def test_create_google_doc_sets_fields(mock_folder, sample_file_data):
    """Creates GoogleDoc with correct fields."""
    doc = _create_google_doc(mock_folder, sample_file_data)

    assert doc.google_file_id == "abc123"
    assert doc.title == "Test Document"
    assert doc.modality == "doc"
    assert doc.folder_id == 1
    assert doc.owner == "user@example.com"
    assert doc.word_count == 5
    assert "gdrive" in doc.tags
    assert "Test Folder" in doc.tags


def test_create_google_doc_handles_none_folder_tags(sample_file_data):
    """Handles folder with None tags."""
    folder = Mock()
    folder.id = 1
    folder.folder_name = None
    folder.tags = None

    doc = _create_google_doc(folder, sample_file_data)

    assert "gdrive" in doc.tags


# Tests for _update_existing_doc (including null chunks fix)


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_returns_unchanged_when_no_reindex(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Returns 'unchanged' when content doesn't need reindexing."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = datetime(2024, 1, 20, 0, 0, 0, tzinfo=timezone.utc)
    session = Mock()

    result = _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert result["status"] == "unchanged"
    mock_process.assert_not_called()


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_handles_null_chunks(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Handles existing.chunks being None gracefully (null chunks fix)."""
    mock_existing_doc.chunks = None  # This is the critical case!
    mock_existing_doc.content_hash = "old_hash"  # Force reindex
    session = Mock()
    mock_process.return_value = {"status": "success"}

    # Should not raise AttributeError
    result = _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert result["status"] == "success"
    mock_process.assert_called_once()


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_deletes_old_chunks(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Deletes old chunks from Qdrant and database."""
    chunk1 = Mock(id=1)
    chunk2 = Mock(id=2)
    mock_existing_doc.chunks = [chunk1, chunk2]
    mock_existing_doc.content_hash = "old_hash"
    session = Mock()
    mock_process.return_value = {"status": "success"}

    mock_client = Mock()
    mock_qdrant.get_qdrant_client.return_value = mock_client

    _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    # Verify chunks were deleted from database
    session.delete.assert_any_call(chunk1)
    session.delete.assert_any_call(chunk2)

    # Verify chunks were deleted from Qdrant
    mock_qdrant.delete_points.assert_called_once_with(
        mock_client, "doc", ["1", "2"]
    )


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_updates_fields(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Updates document fields correctly."""
    mock_existing_doc.chunks = []
    mock_existing_doc.content_hash = "old_hash"
    session = Mock()
    mock_process.return_value = {"status": "success"}

    _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert mock_existing_doc.content == sample_file_data["content"]
    assert mock_existing_doc.title == sample_file_data["title"]
    assert mock_existing_doc.folder_path == sample_file_data["folder_path"]
    assert mock_existing_doc.word_count == sample_file_data["word_count"]
    session.flush.assert_called_once()


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_handles_qdrant_error(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Handles Qdrant deletion errors gracefully."""
    chunk = Mock(id=1)
    mock_existing_doc.chunks = [chunk]
    mock_existing_doc.content_hash = "old_hash"
    session = Mock()
    mock_process.return_value = {"status": "success"}

    mock_qdrant.get_qdrant_client.side_effect = IOError("Connection failed")

    # Should not raise, just log error
    result = _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert result["status"] == "success"
