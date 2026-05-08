"""Tests for MCP core server functions."""
# pyright: reportFunctionMemberAccess=false

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_notes_dir(tmp_path):
    """Create a mock notes directory."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)
    return notes_dir


@pytest.fixture
def mock_settings(mock_notes_dir, tmp_path):
    """Mock settings with temporary notes directory."""
    with patch("memory.api.MCP.servers.notes.settings") as mock:
        mock.FILE_STORAGE_DIR = tmp_path
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
async def test_create_note_blocks_path_traversal(
    mock_settings, mock_celery, malicious_filename
):
    """Blocks path traversal attempts in create_note."""
    from memory.api.MCP.servers.notes import upsert

    # Access underlying function via .fn since create_note is a FunctionTool
    with pytest.raises(ValueError, match="Invalid filename"):
        await upsert.fn(
            subject="Test Note",
            content="Test content",
            filename=malicious_filename,
        )

    # Celery task should NOT be called for malicious paths
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_note_handles_absolute_paths(mock_settings, mock_celery, mock_notes_dir):
    """Absolute paths are converted to relative paths within notes dir."""
    from memory.api.MCP.servers.notes import upsert

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
async def test_create_note_allows_valid_filename(mock_settings, mock_celery, mock_notes_dir):
    """Allows valid filenames in create_note."""
    from memory.api.MCP.servers.notes import upsert

    # No file creation needed - create_note validates path containment
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
async def test_create_note_allows_nested_valid_filename(
    mock_settings, mock_celery, mock_notes_dir
):
    """Allows nested valid filenames in create_note."""
    from memory.api.MCP.servers.notes import upsert

    # No file/directory creation needed - create_note validates path containment
    # but doesn't require the file to exist (it's creating a new file)
    result = await upsert.fn(
        subject="Project Ideas",
        content="Ideas content",
        filename="projects/ideas.md",
    )

    assert result["status"] == "queued"
    mock_celery.send_task.assert_called_once()


@pytest.mark.asyncio
async def test_create_note_without_filename(mock_settings, mock_celery):
    """Allows create_note without filename (filename is optional)."""
    from memory.api.MCP.servers.notes import upsert

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
    from memory.api.MCP.servers.notes import note_files

    # Access underlying function via .fn since note_files is a FunctionTool
    with pytest.raises(ValueError, match="Invalid path"):
        await note_files.fn(path=malicious_path)


def _make_note(db_session, user_id: int, filename: str, content: str = "x"):
    from memory.common.db.models.source_items import Note

    note = Note(
        sha256=filename.encode() + b"\x00" + content.encode(),
        content=content,
        modality="text",
        mime_type="text/markdown",
        size=len(content),
        filename=filename,
        creator_id=user_id,
    )
    db_session.add(note)
    db_session.commit()
    return note


@pytest.mark.asyncio
async def test_note_files_handles_absolute_paths(
    mock_settings, db_session, admin_user, admin_session
):
    """Absolute paths are normalized to relative when filtering DB notes."""
    from memory.api.MCP.servers.notes import note_files
    from tests.conftest import mcp_auth_context

    _make_note(db_session, admin_user.id, "notes/note.md")

    with mcp_auth_context(admin_session.id):
        result = await note_files.fn(path="/")

    assert any("note.md" in f for f in result)


@pytest.mark.asyncio
async def test_note_files_lists_markdown_files(
    mock_settings, db_session, admin_user, admin_session
):
    """Lists markdown notes for the current user."""
    from memory.api.MCP.servers.notes import note_files
    from tests.conftest import mcp_auth_context

    _make_note(db_session, admin_user.id, "notes/note1.md", "content1")
    _make_note(db_session, admin_user.id, "notes/note2.md", "content2")
    _make_note(db_session, admin_user.id, "notes/subdir/nested.md", "nested")

    with mcp_auth_context(admin_session.id):
        result = await note_files.fn(path="/")

    assert len(result) == 3
    assert any("note1.md" in f for f in result)
    assert any("note2.md" in f for f in result)
    assert any("nested.md" in f for f in result)
    assert not any("not_a_note.txt" in f for f in result)


@pytest.mark.asyncio
async def test_note_files_visible_via_team_project_access(
    mock_settings, db_session, regular_user, user_session
):
    """A non-admin who's a member of a Team assigned to a Project should see
    notes in that project via team-project membership.

    Regression guard for the round-1 BLOCKING bug — `note_files` previously
    called `get_user_project_roles(session, user)` with a UserProxy that
    has no `.person`, crashing the query. The fix routes via
    `get_project_roles_by_user_id(user.id, session)`, mirroring deadlines.
    """
    from memory.api.MCP.servers.notes import note_files
    from memory.common.db.models import Person, Team
    from memory.common.db.models.source_items import Note
    from memory.common.db.models.sources import (
        Project,
        project_teams,
        team_members,
    )
    from tests.conftest import mcp_auth_context

    person = Person(identifier="notes_person", display_name="Notes Person")
    db_session.add(person)
    db_session.flush()

    regular_user.person = person
    db_session.flush()

    project = Project(title="Notes Project", state="open")
    db_session.add(project)
    db_session.flush()

    team = Team(name="Notes Team", slug="notes-team", is_active=True)
    db_session.add(team)
    db_session.flush()

    db_session.execute(
        team_members.insert().values(
            team_id=team.id, person_id=person.id, role="member"
        )
    )
    db_session.execute(
        project_teams.insert().values(project_id=project.id, team_id=team.id)
    )

    note = Note(
        sha256=b"team-visible-note\x00",
        content="content",
        modality="text",
        mime_type="text/markdown",
        size=7,
        filename="notes/team_visible.md",
        creator_id=None,  # Not the regular_user — visibility comes via team
        project_id=project.id,
        sensitivity="basic",
    )
    db_session.add(note)
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await note_files.fn(path="/")

    # The team-visible note must be in the listing — which it can only be if
    # the team-project AC path was reached without the UserProxy.person
    # crash. (Notes have include_public=False so this isn't a public bypass.)
    assert any("team_visible.md" in f for f in result)
