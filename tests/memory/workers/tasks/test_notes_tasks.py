import uuid
import pytest
import pathlib
from contextlib import contextmanager
from unittest.mock import Mock, patch

from memory.common.db.models import Note
from memory.common.db.models.source_item import Chunk
from memory.workers.tasks import notes
from memory.workers.tasks.content_processing import create_content_hash
from memory.common import settings


def _make_mock_chunk(source_id: int) -> Chunk:
    """Create a mock chunk for testing with a unique ID."""
    return Chunk(
        id=str(uuid.uuid4()),
        content="test chunk content",
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={"source_id": source_id, "tags": ["test"]},
        collection_name="note",
    )


@pytest.fixture
def mock_make_session(db_session):
    """Mock make_session and embedding functions for note task tests."""

    @contextmanager
    def _mock_session():
        yield db_session

    with patch("memory.workers.tasks.notes.make_session", _mock_session):
        with patch(
            "memory.common.embedding.embed_source_item",
            side_effect=lambda item: [_make_mock_chunk(item.id or 1)],
        ):
            with patch("memory.workers.tasks.content_processing.push_to_qdrant"):
                yield db_session


@pytest.fixture
def mock_note_data():
    """Mock note data for testing."""
    return {
        "subject": "Test Note Subject",
        "content": "This is test note content with enough text to be processed and embedded.",
        "filename": "test_note.md",
        "note_type": "observation",
        "confidences": {"observation_accuracy": 0.8},
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


def test_sync_note_success(mock_note_data, mock_make_session, qdrant):
    """Test successful note synchronization."""
    result = notes.sync_note(**mock_note_data)
    mock_make_session.commit()

    # Verify the Note was created in the database
    note = mock_make_session.query(Note).filter_by(subject="Test Note Subject").first()
    assert note is not None
    assert note.subject == "Test Note Subject"
    assert (
        note.content
        == "This is test note content with enough text to be processed and embedded."
    )
    assert note.modality == "note"
    assert note.mime_type == "text/markdown"
    assert note.note_type == "observation"
    assert note.confidence_dict == {"observation_accuracy": 0.8}
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


def test_sync_note_minimal_data(mock_minimal_note, mock_make_session, qdrant):
    """Test note sync with minimal required data."""
    result = notes.sync_note(**mock_minimal_note)

    note = mock_make_session.query(Note).filter_by(subject="Minimal Note").first()
    assert note is not None
    assert note.subject == "Minimal Note"
    assert note.content == "Minimal content"
    assert note.note_type is None
    assert note.confidence_dict == {}
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


def test_sync_note_empty_content(mock_empty_note, mock_make_session, qdrant):
    """Test note sync with empty content."""
    result = notes.sync_note(**mock_empty_note)

    # Note is still created even with empty content
    note = mock_make_session.query(Note).filter_by(subject="Empty Note").first()
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


def test_sync_note_already_exists(mock_note_data, mock_make_session):
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
    mock_make_session.add(existing_note)
    mock_make_session.commit()

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
    notes_with_hash = mock_make_session.query(Note).filter_by(sha256=sha256).all()
    assert len(notes_with_hash) == 1


def test_sync_note_edit(mock_note_data, mock_make_session):
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
    existing_note.update_confidences(
        {"observation_accuracy": 0.2, "predictive_value": 0.3}
    )
    mock_make_session.add(existing_note)
    mock_make_session.commit()

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
    assert len(mock_make_session.query(Note).all()) == 1
    mock_make_session.refresh(existing_note)
    assert existing_note.content == "bla bla bla"  # type: ignore
    assert existing_note.confidence_dict == {
        "observation_accuracy": 0.8,
        "predictive_value": 0.3,
    }


@pytest.mark.parametrize(
    "note_type,confidence,tags",
    [
        ("observation", 0.9, ["high-confidence", "important"]),
        ("reflection", 0.6, ["personal", "thoughts"]),
        (None, 0.5, []),
        ("meeting", 1.0, ["work", "notes", "2024"]),
    ],
)
def test_sync_note_parameters(note_type, confidence, tags, mock_make_session, qdrant):
    """Test note sync with various parameter combinations."""
    result = notes.sync_note(
        subject=f"Test Note {note_type}",
        content="Test content for parameter testing",
        note_type=note_type,
        confidences={"observation_accuracy": confidence},
        tags=tags,
    )

    note = mock_make_session.query(Note).filter_by(subject=f"Test Note {note_type}").first()
    assert note is not None
    assert note.note_type == note_type
    assert note.confidence_dict == {"observation_accuracy": confidence}
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


def test_sync_note_content_hash_consistency(mock_make_session):
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
    notes_in_db = mock_make_session.query(Note).filter_by(subject="Hash Test").all()
    assert len(notes_in_db) == 1


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_success(mock_sync_note, markdown_files_in_storage, mock_make_session):
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


def test_sync_notes_empty_folder(mock_make_session):
    """Test sync when folder contains no markdown files."""
    # Create an empty directory
    empty_dir = pathlib.Path(settings.NOTES_STORAGE_DIR) / "empty"
    empty_dir.mkdir(parents=True, exist_ok=True)

    result = notes.sync_notes(str(empty_dir))

    assert result["notes_num"] == 0
    assert result["new_notes"] == 0


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_with_existing_notes(
    mock_sync_note, markdown_files_in_storage, mock_make_session
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
    mock_make_session.add(existing_note)
    mock_make_session.commit()

    mock_sync_note.delay.return_value = Mock(id="task-456")

    result = notes.sync_notes(settings.NOTES_STORAGE_DIR)

    assert result["notes_num"] == 4
    assert result["new_notes"] == 3  # Only 3 new notes (one already exists)

    # Verify sync_note.delay was called only for new notes
    assert mock_sync_note.delay.call_count == 3


def test_sync_notes_nonexistent_folder(mock_make_session):
    """Test sync_notes with a folder that doesn't exist."""
    nonexistent_path = "/nonexistent/folder/path"

    result = notes.sync_notes(nonexistent_path)

    # sync_notes should return successfully with 0 notes when folder doesn't exist
    # This is the actual behavior - it gracefully handles the case
    assert result["notes_num"] == 0
    assert result["new_notes"] == 0


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_only_processes_md_files(
    mock_sync_note, markdown_files_in_storage, mock_make_session
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


def test_sync_note_with_long_content(mock_make_session, qdrant):
    """Test sync_note with longer content to ensure proper chunking."""
    long_content = "This is a longer note content. " * 100  # Make it substantial
    result = notes.sync_note(
        subject="Long Note",
        content=long_content,
        tags=["long", "test"],
    )

    note = mock_make_session.query(Note).filter_by(subject="Long Note").first()
    assert note is not None
    assert note.content == long_content
    assert result["status"] == "processed"
    assert result["chunks_count"] > 0


def test_sync_note_unicode_content(mock_make_session, qdrant):
    """Test sync_note with unicode content."""
    unicode_content = "This note contains unicode: 你好世界 🌍 математика"
    result = notes.sync_note(
        subject="Unicode Note",
        content=unicode_content,
    )

    note = mock_make_session.query(Note).filter_by(subject="Unicode Note").first()
    assert note is not None
    assert note.content == unicode_content
    assert result["status"] == "processed"


@patch("memory.workers.tasks.notes.sync_note")
def test_sync_notes_recursive_discovery(mock_sync_note, mock_make_session):
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
def test_sync_notes_handles_file_read_errors(mock_sync_note, mock_make_session):
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


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_success(mock_git_command):
    """Test check_git_command with successful git command execution."""
    # Mock successful git command
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "  main  \n"  # Test that it strips whitespace
    mock_result.stderr = ""
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")
    result = notes.check_git_command(repo_root, "branch", "--show-current")

    assert result == "main"
    mock_git_command.assert_called_once_with(
        repo_root, "branch", "--show-current", force=False
    )


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_with_force(mock_git_command):
    """Test check_git_command with force=True parameter."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "output"
    mock_result.stderr = ""
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")
    result = notes.check_git_command(repo_root, "status", force=True)

    assert result == "output"
    mock_git_command.assert_called_once_with(repo_root, "status", force=True)


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_no_git_repo(mock_git_command):
    """Test check_git_command when git_command returns None (no git repo)."""
    mock_git_command.return_value = None

    repo_root = pathlib.Path("/test/repo")

    with pytest.raises(RuntimeError, match=r"`status` failed"):
        notes.check_git_command(repo_root, "status")

    mock_git_command.assert_called_once_with(repo_root, "status", force=False)


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_git_failure(mock_git_command):
    """Test check_git_command when git command fails with non-zero return code."""
    mock_result = Mock()
    mock_result.returncode = 1
    mock_result.stdout = "fatal: not a git repository"
    mock_result.stderr = "error: unknown command"
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")

    with pytest.raises(
        RuntimeError, match=r"`branch --invalid` failed with return code 1"
    ):
        notes.check_git_command(repo_root, "branch", "--invalid")

    mock_git_command.assert_called_once_with(
        repo_root, "branch", "--invalid", force=False
    )


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_multiple_args(mock_git_command):
    """Test check_git_command with multiple arguments."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "commit-hash"
    mock_result.stderr = ""
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")
    result = notes.check_git_command(repo_root, "rev-parse", "--short", "HEAD")

    assert result == "commit-hash"
    mock_git_command.assert_called_once_with(
        repo_root, "rev-parse", "--short", "HEAD", force=False
    )


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_empty_stdout(mock_git_command):
    """Test check_git_command when git command succeeds but returns empty stdout."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")
    result = notes.check_git_command(repo_root, "diff", "--exit-code")

    assert result == ""
    mock_git_command.assert_called_once_with(
        repo_root, "diff", "--exit-code", force=False
    )


@patch("memory.workers.tasks.notes.git_command")
def test_check_git_command_whitespace_handling(mock_git_command):
    """Test check_git_command properly strips whitespace from stdout."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "\n\n  some output with spaces  \n\n"
    mock_result.stderr = ""
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")
    result = notes.check_git_command(repo_root, "log", "--oneline", "-1")

    assert result == "some output with spaces"
    mock_git_command.assert_called_once_with(
        repo_root, "log", "--oneline", "-1", force=False
    )


@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.logger")
def test_check_git_command_logs_errors(mock_logger, mock_git_command):
    """Test check_git_command logs error details when git command fails."""
    mock_result = Mock()
    mock_result.returncode = 128
    mock_result.stdout = "some output"
    mock_result.stderr = "fatal: repository not found"
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")

    with pytest.raises(RuntimeError):
        notes.check_git_command(repo_root, "clone", "invalid-url")

    # Verify error logging
    mock_logger.error.assert_any_call("Git command failed: 128")
    mock_logger.error.assert_any_call("stderr: fatal: repository not found")
    mock_logger.error.assert_any_call("stdout: some output")


@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.logger")
def test_check_git_command_logs_errors_no_stdout(mock_logger, mock_git_command):
    """Test check_git_command logs appropriately when there's no stdout."""
    mock_result = Mock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "error: command failed"
    mock_git_command.return_value = mock_result

    repo_root = pathlib.Path("/test/repo")

    with pytest.raises(RuntimeError):
        notes.check_git_command(repo_root, "invalid-command")

    # Verify error logging - should not log stdout when empty
    mock_logger.error.assert_any_call("Git command failed: 1")
    mock_logger.error.assert_any_call("stderr: error: command failed")
    # stdout logging should not have been called since stdout is empty
    stdout_calls = [
        call for call in mock_logger.error.call_args_list if "stdout:" in str(call)
    ]
    assert len(stdout_calls) == 0


@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_no_git_repo(mock_settings):
    """Test track_git_changes when no git repository exists."""
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = False
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    result = notes.track_git_changes()

    assert result == {"status": "no_git_repo"}


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_no_changes(
    mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes when there are no new changes."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Mock git commands to return same commit hash
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin (no return needed)
        "abc123",  # latest commit (same as current)
    ]
    mock_git_command.return_value = Mock()  # pull command

    result = notes.track_git_changes()

    assert result == {
        "status": "no_changes",
        "current_commit": "abc123",
        "latest_commit": "abc123",
        "changed_files": [],
    }

    # Should not call sync_note when no changes
    mock_sync_note.delay.assert_not_called()


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_diff_failure(
    mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes when diff command fails."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Mock git commands
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit (different from current)
    ]

    # Mock pull command success, diff command failure
    mock_git_command.side_effect = [
        Mock(),  # pull command
        Mock(returncode=1, stdout="", stderr="diff failed"),  # diff command fails
    ]

    result = notes.track_git_changes()

    assert result == {
        "status": "error",
        "error": "Failed to get changed files",
    }

    # Should not call sync_note when diff fails
    mock_sync_note.delay.assert_not_called()


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_diff_returns_none(
    mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes when diff command returns None."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Mock git commands
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit (different from current)
    ]

    # Mock pull command success, diff command returns None
    mock_git_command.side_effect = [
        Mock(),  # pull command
        None,  # diff command returns None
    ]

    result = notes.track_git_changes()

    assert result == {
        "status": "error",
        "error": "Failed to get changed files",
    }

    # Should not call sync_note when diff returns None
    mock_sync_note.delay.assert_not_called()


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_empty_diff(
    mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes when diff returns empty (no actual file changes)."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Mock git commands
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit (different from current)
    ]

    # Mock pull command success, diff command returns empty
    mock_git_command.side_effect = [
        Mock(),  # pull command
        Mock(returncode=0, stdout=""),  # diff command returns empty
    ]

    result = notes.track_git_changes()

    assert result == {
        "status": "success",
        "current_commit": "abc123",
        "latest_commit": "def456",
        "changed_files": [],
    }

    # Should not call sync_note when no files changed
    mock_sync_note.delay.assert_not_called()


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_whitespace_in_filenames(
    mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes handles whitespace in filenames correctly."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Mock git commands
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit (different from current)
    ]

    # Mock diff with whitespace and empty lines
    mock_git_command.side_effect = [
        Mock(),  # pull command
        Mock(
            returncode=0, stdout="  file1.md  \n\n  file2.md  \n\n"
        ),  # diff with whitespace
    ]

    # Mock file reading
    mock_file1 = Mock()
    mock_file1.stem = "file1"
    mock_file1.read_text.return_value = "Content 1"
    mock_file1.as_posix.return_value = "file1.md"

    mock_file2 = Mock()
    mock_file2.stem = "file2"
    mock_file2.read_text.return_value = "Content 2"
    mock_file2.as_posix.return_value = "file2.md"

    with patch("memory.workers.tasks.notes.pathlib.Path") as mock_path:
        mock_path.side_effect = [mock_file1, mock_file2]

        result = notes.track_git_changes()

    assert result == {
        "status": "success",
        "current_commit": "abc123",
        "latest_commit": "def456",
        "changed_files": ["file1.md", "file2.md"],
    }

    # Should call sync_note for each non-empty file
    assert mock_sync_note.delay.call_count == 2


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
def test_track_git_changes_feature_branch(
    mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes works with feature branches."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Mock git commands for feature branch
    mock_check_git.side_effect = [
        "feature/notes-sync",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit from origin/feature/notes-sync
    ]

    mock_git_command.side_effect = [
        Mock(),  # pull origin feature/notes-sync
        Mock(returncode=0, stdout="feature_file.md\n"),  # diff command
    ]

    # Mock file reading
    mock_file = Mock()
    mock_file.stem = "feature_file"
    mock_file.read_text.return_value = "Feature content"
    mock_file.as_posix.return_value = "feature_file.md"

    with patch("memory.workers.tasks.notes.pathlib.Path") as mock_path:
        mock_path.return_value = mock_file

        result = notes.track_git_changes()

    assert result == {
        "status": "success",
        "current_commit": "abc123",
        "latest_commit": "def456",
        "changed_files": ["feature_file.md"],
    }

    # Verify correct branch was used in git commands
    mock_git_command.assert_any_call(
        mock_repo_root, "pull", "origin", "feature/notes-sync"
    )
    mock_check_git.assert_any_call(
        mock_repo_root, "rev-parse", "origin/feature/notes-sync"
    )


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
@patch("memory.workers.tasks.notes.settings")
@patch("memory.workers.tasks.notes.logger")
def test_track_git_changes_logging(
    mock_logger, mock_settings, mock_check_git, mock_git_command, mock_sync_note
):
    """Test track_git_changes logs appropriately."""
    # Mock git repo exists
    mock_repo_root = Mock()
    mock_repo_root.__truediv__ = Mock(return_value=Mock())
    mock_repo_root.__truediv__.return_value.exists.return_value = True
    mock_settings.NOTES_STORAGE_DIR = mock_repo_root

    # Test no changes scenario
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "abc123",  # latest commit (same as current)
    ]
    mock_git_command.return_value = Mock()  # pull command

    notes.track_git_changes()

    # Verify logging
    mock_logger.info.assert_any_call("Tracking git changes")
    mock_logger.info.assert_any_call("No new changes")

    # Reset mocks for changes scenario
    mock_logger.reset_mock()
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit (different)
    ]
    mock_git_command.side_effect = [
        Mock(),  # pull command
        Mock(returncode=0, stdout="test.md\n"),  # diff command
    ]

    mock_file = Mock()
    mock_file.stem = "test"
    mock_file.read_text.return_value = "Test content"
    mock_file.as_posix.return_value = "test.md"

    with patch("memory.workers.tasks.notes.pathlib.Path") as mock_path:
        mock_path.return_value = mock_file
        notes.track_git_changes()

    # Verify logging for changes scenario
    mock_logger.info.assert_any_call("Tracking git changes")
    mock_logger.info.assert_any_call("Changed files: ['test.md']")


# Profile handling tests


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.people.sync_profile_from_file")
def test_sync_notes_routes_profiles_to_sync_profile_from_file(
    mock_sync_profile, mock_sync_note, mock_make_session, tmp_path
):
    """Test that sync_notes routes profile files to sync_profile_from_file."""

    # Create notes dir with profile and regular notes
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Create regular note
    regular_note = notes_dir / "regular_note.md"
    regular_note.write_text("Regular note content")

    # Create profiles directory with profile file
    profiles_dir = notes_dir / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    profile_file = profiles_dir / "john_doe.md"
    profile_file.write_text(
        """---
identifier: john_doe
display_name: John Doe
---

Profile notes."""
    )

    mock_sync_note.delay.return_value = Mock(id="task-note")
    mock_sync_profile.delay.return_value = Mock(id="task-profile")

    with patch("memory.common.settings.NOTES_STORAGE_DIR", notes_dir):
        with patch("memory.common.settings.PROFILES_FOLDER", "profiles"):
            result = notes.sync_notes(str(notes_dir))

    # Should have found 2 files total
    assert result["notes_num"] == 2

    # Regular note should go to sync_note
    assert mock_sync_note.delay.call_count == 1
    note_call_args = mock_sync_note.delay.call_args
    assert note_call_args[1]["subject"] == "regular_note"

    # Profile should go to sync_profile_from_file
    assert mock_sync_profile.delay.call_count == 1
    profile_call_args = mock_sync_profile.delay.call_args
    assert "profiles/john_doe.md" in profile_call_args[0][0]


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.people.sync_profile_from_file")
@patch("memory.workers.tasks.notes.git_command")
@patch("memory.workers.tasks.notes.check_git_command")
def test_track_git_changes_routes_profiles_to_sync_profile_from_file(
    mock_check_git, mock_git_command, mock_sync_profile, mock_sync_note, tmp_path
):
    """Test that track_git_changes routes profile files to sync_profile_from_file."""
    from unittest.mock import Mock

    # Create notes dir structure
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / ".git").mkdir()  # Fake git repo

    # Create regular note and profile file
    regular_note = notes_dir / "regular_note.md"
    regular_note.write_text("Regular note content")

    profiles_dir = notes_dir / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    profile_file = profiles_dir / "jane_doe.md"
    profile_file.write_text(
        """---
identifier: jane_doe
display_name: Jane Doe
---

Jane's notes."""
    )

    # Mock git commands to return both files as changed
    mock_check_git.side_effect = [
        "main",  # current branch
        "abc123",  # current commit
        None,  # fetch origin
        "def456",  # latest commit
    ]
    mock_git_command.side_effect = [
        Mock(),  # pull command
        Mock(
            returncode=0, stdout="regular_note.md\nprofiles/jane_doe.md\n"
        ),  # diff command
    ]

    mock_sync_note.delay.return_value = Mock(id="task-note")
    mock_sync_profile.delay.return_value = Mock(id="task-profile")

    with patch("memory.common.settings.NOTES_STORAGE_DIR", notes_dir):
        with patch("memory.common.settings.PROFILES_FOLDER", "profiles"):
            result = notes.track_git_changes()

    assert result["status"] == "success"
    assert "regular_note.md" in result["changed_files"]
    assert "profiles/jane_doe.md" in result["changed_files"]

    # Regular note should go to sync_note
    assert mock_sync_note.delay.call_count == 1
    note_call_args = mock_sync_note.delay.call_args
    assert note_call_args[1]["subject"] == "regular_note"
    assert note_call_args[1]["save_to_file"] is False

    # Profile should go to sync_profile_from_file
    assert mock_sync_profile.delay.call_count == 1
    profile_call_args = mock_sync_profile.delay.call_args
    assert profile_call_args[0][0] == "profiles/jane_doe.md"


@patch("memory.workers.tasks.notes.sync_note")
@patch("memory.workers.tasks.people.sync_profile_from_file")
def test_sync_notes_skips_existing_profiles(
    mock_sync_profile, mock_sync_note, mock_make_session, tmp_path
):
    """Test that sync_notes skips profiles that already have a Person record."""
    from memory.common.db.models import Person

    # Create notes dir with profile
    notes_dir = tmp_path / "notes"
    profiles_dir = notes_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    profile_file = profiles_dir / "existing_person.md"
    profile_file.write_text("Profile content")

    # Create existing Person in database
    sha256 = create_content_hash("person:existing_person")
    existing_person = Person(
        identifier="existing_person",
        display_name="Existing Person",
        modality="person",
        mime_type="text/plain",
        sha256=sha256,
        size=0,
    )
    mock_make_session.add(existing_person)
    mock_make_session.commit()

    mock_sync_profile.delay.return_value = Mock(id="task-profile")

    with patch("memory.common.settings.NOTES_STORAGE_DIR", notes_dir):
        with patch("memory.common.settings.PROFILES_FOLDER", "profiles"):
            result = notes.sync_notes(str(notes_dir))

    # Should not call sync_profile_from_file for existing person
    assert mock_sync_profile.delay.call_count == 0
    assert result["new_profiles"] == 0
