"""Tests for MCP notes server functions."""
# pyright: reportFunctionMemberAccess=false

import pytest
from unittest.mock import patch, MagicMock

from memory.api.MCP.servers.notes import note_files, upsert


@pytest.fixture
def mock_notes_dir(tmp_path):
    """Create a mock notes directory."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)
    return notes_dir


@pytest.fixture
def mock_settings(mock_notes_dir):
    """Mock settings with temporary notes directory."""
    with patch("memory.api.MCP.servers.notes.settings") as mock:
        mock.NOTES_STORAGE_DIR = mock_notes_dir
        mock.CELERY_QUEUE_PREFIX = "test"
        yield mock


@pytest.fixture
def mock_celery():
    """Mock celery app to prevent actual task dispatch."""
    with patch("memory.api.MCP.servers.notes.celery_app") as mock:
        task = MagicMock()
        task.id = "test-task-id"
        mock.send_task.return_value = task
        yield mock


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "subdir/../../../etc/passwd",
    ],
)
async def test_upsert_blocks_path_traversal(
    mock_settings, mock_celery, malicious_filename
):
    """Blocks path traversal attempts in upsert."""
    # Access underlying function via .fn since upsert is a FunctionTool
    with pytest.raises(ValueError, match="Invalid filename"):
        await upsert.fn(
            subject="Test Note",
            content="Test content",
            filename=malicious_filename,
        )

    # Celery task should NOT be called for malicious paths
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_handles_absolute_paths(mock_settings, mock_celery, mock_notes_dir):
    """Absolute paths are converted to relative paths within notes dir."""
    # /etc/passwd becomes etc/passwd inside notes dir - this is correct behavior
    # The path validator strips leading slashes
    result = await upsert.fn(
        subject="Test Note",
        content="Test content",
        filename="/subdir/note.md",  # Becomes subdir/note.md
    )

    # Task should be queued (path is valid after normalization)
    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_upsert_allows_valid_filename(mock_settings, mock_celery, mock_notes_dir):
    """Allows valid filenames in upsert."""
    # No file creation needed - upsert validates path containment
    # but doesn't require the file to exist (it's creating a new file)
    result = await upsert.fn(
        subject="Test Note",
        content="Test content",
        filename="valid_note.md",
    )

    assert result["status"] == "queued"
    assert result["task_id"] == "test-task-id"
    mock_celery.send_task.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_allows_nested_valid_filename(
    mock_settings, mock_celery, mock_notes_dir
):
    """Allows nested valid filenames in upsert."""
    # No file/directory creation needed - upsert validates path containment
    # but doesn't require the file to exist (it's creating a new file)
    result = await upsert.fn(
        subject="Project Ideas",
        content="Ideas content",
        filename="projects/ideas.md",
    )

    assert result["status"] == "queued"
    mock_celery.send_task.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_without_filename(mock_settings, mock_celery):
    """Allows upsert without filename (filename is optional)."""
    result = await upsert.fn(
        subject="Test Note",
        content="Test content",
    )

    assert result["status"] == "queued"
    mock_celery.send_task.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malicious_path",
    [
        "../",
        "../../",
    ],
)
async def test_note_files_blocks_path_traversal(mock_settings, malicious_path):
    """Blocks path traversal attempts in note_files."""
    # Access underlying function via .fn since note_files is a FunctionTool
    with pytest.raises(ValueError, match="Invalid path"):
        await note_files.fn(path=malicious_path)


@pytest.mark.asyncio
async def test_note_files_handles_absolute_paths(mock_settings, mock_notes_dir):
    """Absolute paths are converted to relative paths within notes dir."""
    # Create a test file
    (mock_notes_dir / "note.md").write_text("content")

    # /etc becomes etc inside notes dir - this is correct behavior
    # The path validator strips leading slashes
    result = await note_files.fn(path="/")  # Root becomes notes dir

    assert any("note.md" in f for f in result)


@pytest.mark.asyncio
async def test_note_files_lists_markdown_files(mock_settings, mock_notes_dir):
    """Lists markdown files in the notes directory."""
    # Create some test files
    (mock_notes_dir / "note1.md").write_text("content1")
    (mock_notes_dir / "note2.md").write_text("content2")
    (mock_notes_dir / "not_a_note.txt").write_text("ignored")

    subdir = mock_notes_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.md").write_text("nested content")

    # Access underlying function via .fn since note_files is a FunctionTool
    result = await note_files.fn(path="/")

    assert len(result) == 3
    assert any("note1.md" in f for f in result)
    assert any("note2.md" in f for f in result)
    assert any("nested.md" in f for f in result)
    assert not any("not_a_note.txt" in f for f in result)
