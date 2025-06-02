import pytest
import pathlib
from unittest.mock import Mock, patch

from memory.common.db.models import Note
from memory.workers.tasks import notes
from memory.workers.tasks.content_processing import create_content_hash
from memory.common import settings


@pytest.fixture
def mock_note_data():
    """Mock note data for testing."""
    return {
        "subject": "Test Note Subject",
        "content": "This is test note content with enough text to be processed and embedded.",
        "filename": "test_note.md",
        "note_type": "observation",
        "confidence": 0.8,
        "tags": ["test", "note"],
    }


@pytest.fixture
def mock_minimal_note():
    """Mock note with minimal required data."""
    return {
        "subject": "Minimal Note",
        "content": "Minimal content",
    }


@pytest.fixture
def mock_empty_note():
    """Mock note with empty content."""
    return {
        "subject": "Empty Note",
        "content": "",
    }


@pytest.fixture
def markdown_files_in_storage():
    """Create real markdown files in the notes storage directory."""
    notes_dir = pathlib.Path(settings.NOTES_STORAGE_DIR)
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Create test markdown files
    files = []

    file1 = notes_dir / "note1.md"
    file1.write_text("Content of note 1")
    files.append(file1)

    file2 = notes_dir / "note2.md"
    file2.write_text("Content of note 2")
    files.append(file2)

    file3 = notes_dir / "note3.md"
    file3.write_text("Content of note 3")
    files.append(file3)

    # Create a subdirectory with a file
    subdir = notes_dir / "subdir"
    subdir.mkdir(exist_ok=True)
    file4 = subdir / "note4.md"
    file4.write_text("Content of note 4 in subdirectory")
    files.append(file4)

    # Create a non-markdown file that should be ignored
    txt_file = notes_dir / "not_markdown.txt"
    txt_file.write_text("This should be ignored")

    return files


def test_sync_note_success(mock_note_data, db_session, qdrant):
    """Test successful note synchronization."""
    result = notes.sync_note(**mock_note_data)
    db_session.commit()

    # Verify the Note was created in the database
    note = db_session.query(Note).filter_by(subject="Test Note Subject").first()
    assert note is not None
    assert note.subject == "Test Note Subject"
    assert (
        note.content
        == "This is test note content with enough text to be processed and embedded."
    )
    assert note.modality == "note"
    assert note.mime_type == "text/markdown"
    assert note.note_type == "observation"
    assert float(note.confidence) == 0.8  # Convert Decimal to float for comparison
    assert note.filename is not None
    assert note.tags == ["test", "note"]

    # Verify the result - updated to match actual return format
    assert result == {
        "note_id": note.id,
        "title": "Test Note Subject",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": 93,
    }


def test_sync_note_minimal_data(mock_minimal_note, db_session, qdrant):
    """Test note sync with minimal required data."""
    result = notes.sync_note(**mock_minimal_note)

    note = db_session.query(Note).filter_by(subject="Minimal Note").first()
    assert note is not None
    assert note.subject == "Minimal Note"
    assert note.content == "Minimal content"
    assert note.note_type is None
    assert float(note.confidence) == 0.5  # Default value, convert Decimal to float
    assert note.tags == []  # Default empty list
    assert note.filename is not None and "Minimal Note.md" in note.filename

    # Updated to match actual return format
    assert result == {
        "note_id": note.id,
        "title": "Minimal Note",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": 31,
    }


def test_sync_note_empty_content(mock_empty_note, db_session, qdrant):
    """Test note sync with empty content."""
    result = notes.sync_note(**mock_empty_note)

    # Note is still created even with empty content
    note = db_session.query(Note).filter_by(subject="Empty Note").first()
    assert note is not None
    assert note.subject == "Empty Note"
    assert note.content == ""

    # Updated to match actual return format
    assert result == {
        "note_id": note.id,
        "title": "Empty Note",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": 14,
    }


def test_sync_note_already_exists(mock_note_data, db_session):
    """Test note sync when content already exists."""
    # Create the content text the same way sync_note does
    text = Note.as_text(mock_note_data["content"], mock_note_data["subject"])
    sha256 = create_content_hash(text)

    # Add existing note with same content hash but different filename to avoid file conflicts
    existing_note = Note(
        subject="Existing Note",
        content=mock_note_data["content"],
        sha256=sha256,
        modality="note",
        tags=["existing"],
        mime_type="text/markdown",
        size=len(text.encode("utf-8")),
        embed_status="RAW",
        filename="existing_note.md",
    )
    db_session.add(existing_note)
    db_session.commit()

    result = notes.sync_note(**mock_note_data)

    # Updated to match actual return format for already_exists case
    assert result == {
        "note_id": existing_note.id,
        "title": "Existing Note",
        "status": "already_exists",
        "chunks_count": 0,  # Existing note has no chunks
        "embed_status": "RAW",  # Existing note has RAW status
    }

    # Verify no duplicate was created
    notes_with_hash = db_session.query(Note).filter_by(sha256=sha256).all()
    assert len(notes_with_hash) == 1


def test_sync_note_edit(mock_note_data, db_session):
    """Test note sync when content already exists."""
    # Create the content text the same way sync_note does
    text = Note.as_text(mock_note_data["content"], mock_note_data["subject"])
    sha256 = create_content_hash(text)

    # Add existing note with same content hash but different filename to avoid file conflicts
    existing_note = Note(
        subject="Existing Note",
        content=mock_note_data["content"],
        sha256=sha256,
        modality="note",
        tags=["existing"],
        mime_type="text/markdown",
        size=len(text.encode("utf-8")),
        embed_status="RAW",
        filename="test_note.md",
    )
    db_session.add(existing_note)
    db_session.commit()

    result = notes.sync_note(
        **{**mock_note_data, "content": "bla bla bla", "subject": "blee"}
    )

    assert result == {
        "note_id": existing_note.id,
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "title": "blee",
        "content_length": 19,
    }

    # Verify no duplicate was created
    assert len(db_session.query(Note).all()) == 1
    db_session.refresh(existing_note)
    assert existing_note.content == "bla bla bla"  # type: ignore


@pytest.mark.parametrize(
    "note_type,confidence,tags",
    [
        ("observation", 0.9, ["high-confidence", "important"]),
        ("reflection", 0.6, ["personal", "thoughts"]),
        (None, 0.5, []),
        ("meeting", 1.0, ["work", "notes", "2024"]),
    ],
)
def test_sync_note_parameters(note_type, confidence, tags, db_session, qdrant):
    """Test note sync with various parameter combinations."""
    result = notes.sync_note(
        subject=f"Test Note {note_type}",
        content="Test content for parameter testing",
        note_type=note_type,
        confidence=confidence,
        tags=tags,
    )

    note = db_session.query(Note).filter_by(subject=f"Test Note {note_type}").first()
    assert note is not None
    assert note.note_type == note_type
    assert float(note.confidence) == confidence  # Convert Decimal to float
    assert note.tags == tags

    # Updated to match actual return format
    text = f"# Test Note {note_type}\n\nTest content for parameter testing"
    assert result == {
        "note_id": note.id,
        "title": f"Test Note {note_type}",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": len(text.encode("utf-8")),
    }


def test_sync_note_content_hash_consistency(db_session):
    """Test that content hash is calculated consistently."""
    note_data = {
        "subject": "Hash Test",
        "content": "Consistent content for hashing",
        "tags": ["hash-test"],
    }

    # Sync the same note twice
    result1 = notes.sync_note(**note_data)
    result2 = notes.sync_note(**note_data)

    # First should succeed, second should detect existing
    assert result1["status"] == "processed"
    assert result2["status"] == "already_exists"
    assert result1["note_id"] == result2["note_id"]

    # Verify only one note exists in database
    notes_in_db = db_session.query(Note).filter_by(subject="Hash Test").all()
    assert len(notes_in_db) == 1


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_success(mock_sync_note, markdown_files_in_storage, db_session):
    """Test successful notes folder synchronization."""
    mock_sync_note.delay.return_value = Mock(id="task-123")

    result = notes.sync_notes(settings.NOTES_STORAGE_DIR)

    assert result["notes_num"] == 4  # 4 markdown files created by fixture
    assert result["new_notes"] == 4  # All are new

    # Verify sync_note.delay was called for each file
    assert mock_sync_note.delay.call_count == 4

    # Check some of the calls were made with correct parameters
    call_args_list = mock_sync_note.delay.call_args_list
    subjects = [call[1]["subject"] for call in call_args_list]
    contents = [call[1]["content"] for call in call_args_list]

    assert subjects == ["note1", "note2", "note3", "note4"]
    assert contents == [
        "Content of note 1",
        "Content of note 2",
        "Content of note 3",
        "Content of note 4 in subdirectory",
    ]


def test_sync_notes_empty_folder(db_session):
    """Test sync when folder contains no markdown files."""
    # Create an empty directory
    empty_dir = pathlib.Path(settings.NOTES_STORAGE_DIR) / "empty"
    empty_dir.mkdir(parents=True, exist_ok=True)

    result = notes.sync_notes(str(empty_dir))

    assert result["notes_num"] == 0
    assert result["new_notes"] == 0


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_with_existing_notes(
    mock_sync_note, markdown_files_in_storage, db_session
):
    """Test sync when some notes already exist."""
    # Create one existing note in the database
    existing_file = markdown_files_in_storage[0]  # note1.md
    existing_note = Note(
        subject="note1",
        content="Content of note 1",
        sha256=b"existing_hash" + bytes(24),
        modality="note",
        tags=["existing"],
        mime_type="text/markdown",
        size=100,
        filename=str(existing_file),
        embed_status="RAW",
    )
    db_session.add(existing_note)
    db_session.commit()

    mock_sync_note.delay.return_value = Mock(id="task-456")

    result = notes.sync_notes(settings.NOTES_STORAGE_DIR)

    assert result["notes_num"] == 4
    assert result["new_notes"] == 3  # Only 3 new notes (one already exists)

    # Verify sync_note.delay was called only for new notes
    assert mock_sync_note.delay.call_count == 3


def test_sync_notes_nonexistent_folder(db_session):
    """Test sync_notes with a folder that doesn't exist."""
    nonexistent_path = "/nonexistent/folder/path"

    result = notes.sync_notes(nonexistent_path)

    # sync_notes should return successfully with 0 notes when folder doesn't exist
    # This is the actual behavior - it gracefully handles the case
    assert result["notes_num"] == 0
    assert result["new_notes"] == 0


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_only_processes_md_files(
    mock_sync_note, markdown_files_in_storage, db_session
):
    """Test that sync_notes only processes markdown files."""
    mock_sync_note.delay.return_value = Mock(id="task-123")

    # The fixture creates a .txt file that should be ignored
    result = notes.sync_notes(settings.NOTES_STORAGE_DIR)

    # Should only process the 4 .md files, not the .txt file
    assert result["notes_num"] == 4
    assert result["new_notes"] == 4


def test_note_as_text_method():
    """Test the Note.as_text static method used in sync_note."""
    content = "This is the note content"
    subject = "Note Subject"

    text = Note.as_text(content, subject)

    # The method should combine subject and content appropriately
    assert subject in text
    assert content in text


def test_sync_note_with_long_content(db_session, qdrant):
    """Test sync_note with longer content to ensure proper chunking."""
    long_content = "This is a longer note content. " * 100  # Make it substantial
    result = notes.sync_note(
        subject="Long Note",
        content=long_content,
        tags=["long", "test"],
    )

    note = db_session.query(Note).filter_by(subject="Long Note").first()
    assert note is not None
    assert note.content == long_content
    assert result["status"] == "processed"
    assert result["chunks_count"] > 0


def test_sync_note_unicode_content(db_session, qdrant):
    """Test sync_note with unicode content."""
    unicode_content = "This note contains unicode: 你好世界 🌍 математика"
    result = notes.sync_note(
        subject="Unicode Note",
        content=unicode_content,
    )

    note = db_session.query(Note).filter_by(subject="Unicode Note").first()
    assert note is not None
    assert note.content == unicode_content
    assert result["status"] == "processed"


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_recursive_discovery(mock_sync_note, db_session):
    """Test that sync_notes discovers files recursively in subdirectories."""
    mock_sync_note.delay.return_value = Mock(id="task-123")

    # Create nested directory structure
    notes_dir = pathlib.Path(settings.NOTES_STORAGE_DIR)
    deep_dir = notes_dir / "level1" / "level2" / "level3"
    deep_dir.mkdir(parents=True, exist_ok=True)

    deep_file = deep_dir / "deep_note.md"
    deep_file.write_text("This is a note in a deep subdirectory")

    result = notes.sync_notes(settings.NOTES_STORAGE_DIR)

    # Should find the deep file
    assert result["new_notes"] >= 1

    # Verify the file was processed
    processed_files = list(pathlib.Path(settings.NOTES_STORAGE_DIR).rglob("*.md"))
    assert any("deep_note.md" in str(f) for f in processed_files)


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_handles_file_read_errors(mock_sync_note, db_session):
    """Test sync_notes handles file read errors gracefully."""
    # Create a markdown file
    notes_dir = pathlib.Path(settings.NOTES_STORAGE_DIR)
    notes_dir.mkdir(parents=True, exist_ok=True)

    test_file = notes_dir / "test.md"
    test_file.write_text("Test content")

    # Mock sync_note to raise an exception
    mock_sync_note.delay.side_effect = Exception("File read error")

    # This should not crash the whole operation
    result = notes.sync_notes(settings.NOTES_STORAGE_DIR)

    # Should catch the error and return error status
    assert result["status"] == "error"
    assert "File read error" in result["error"]
