"""Tests for MCP core server functions."""
# pyright: reportFunctionMemberAccess=false

import pytest
from unittest.mock import patch, MagicMock

from memory.api.MCP.servers.notes import note_files, upsert
from memory.common import settings
from memory.common.db.models import APIKey, Note, Person, Team
from memory.common.db.models.sources import Project, project_teams, team_members
from tests.conftest import mcp_auth_context


@pytest.fixture
def mock_notes_dir(tmp_path):
    """Create a mock notes directory."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)
    return notes_dir


@pytest.fixture
def mock_settings(mock_notes_dir, tmp_path):
    """Mock settings with temporary notes directory."""
    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "NOTES_STORAGE_DIR", mock_notes_dir),
        patch.object(settings, "CELERY_QUEUE_PREFIX", "test"),
    ):
        yield settings


@pytest.fixture
def mock_celery():
    """Mock celery app to prevent actual task dispatch."""
    with patch("memory.api.MCP.servers.notes.celery_app") as mock:
        task = MagicMock()
        task.id = "test-task-id"
        mock.send_task.return_value = task
        yield mock


@pytest.fixture
def as_user(user_session):
    with mcp_auth_context(user_session.token):
        yield


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
async def test_create_note_relativises_absolute_paths_inside_the_notes_dir(
    mock_settings, mock_celery, as_user
):
    await upsert.fn(
        subject="s", content="c", filename=str(mock_settings.NOTES_STORAGE_DIR / "sub/note.md")
    )

    assert mock_celery.send_task.call_args.kwargs["kwargs"]["filename"] == "notes/sub/note.md"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        "/subdir/note.md",
        "/notes/note.md",
        "/app/memory_files/notes/note.md",
        "note",
        "main.js",
        ".gitignore",
        ".obsidian/workspace.json",
        ".obsidian/plugins/obsidian-git/main.js",
        ".obsidian/note.md",
        "sub/.memory/note.md",
    ],
)
async def test_create_note_rejects_non_markdown_hidden_or_absolute_filenames(
    filename, mock_settings, mock_celery, as_user
):
    with pytest.raises(ValueError, match="Invalid filename"):
        await upsert.fn(subject="s", content="c", filename=filename)
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject, filename",
    [("s", "taken.md"), ("s", "sub/../taken.md"), ("taken", None)],
)
async def test_create_note_create_only_rejects_own_existing_filename(
    subject, filename, mock_settings, mock_celery, as_user, db_session, regular_user
):
    make_note(db_session, regular_user.id, "notes/taken.md")

    with pytest.raises(ValueError, match="already exists"):
        await upsert.fn(subject=subject, content="c", filename=filename, create_only=True)
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["fresh.md", "theirs.md"])
async def test_create_note_create_only_hides_other_users_notes(
    filename, mock_settings, mock_celery, as_user, db_session, admin_user
):
    make_note(db_session, admin_user.id, "notes/theirs.md")

    result = await upsert.fn(subject="s", content="c", filename=filename, create_only=True)

    assert result["status"] == "queued"
    assert mock_celery.send_task.call_args.kwargs["kwargs"]["create_only"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_fixture, scopes, admin",
    [
        ("user_session", None, False),
        ("admin_session", None, True),
        # An admin's key narrowed to notes can only write as the user.
        ("admin_session", ["notes", "notes:write"], False),
    ],
)
async def test_create_note_passes_the_writer_to_the_task(
    session_fixture, scopes, admin, request, mock_settings, mock_celery
):
    user_session = request.getfixturevalue(session_fixture)

    with mcp_auth_context(user_session.token, scopes=scopes):
        await upsert.fn(subject="s", content="c", filename="n.md")

    kwargs = mock_celery.send_task.call_args.kwargs["kwargs"]
    assert (kwargs["creator_id"], kwargs["admin"]) == (user_session.user_id, admin)


@pytest.mark.asyncio
async def test_create_note_requires_a_user(mock_settings, mock_celery, db_session):
    with pytest.raises(PermissionError):
        await upsert.fn(subject="s", content="c", filename="n.md")
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
async def test_create_note_allows_valid_filename(mock_settings, mock_celery, as_user):
    """Allows valid filenames in create_note."""

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
async def test_create_note_allows_nested_valid_filename(mock_settings, mock_celery, as_user):
    """Allows nested valid filenames in create_note."""

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
async def test_create_note_without_filename(mock_settings, mock_celery, as_user):
    """Allows create_note without filename (filename is optional)."""

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


def make_note(db_session, user_id: int, filename: str, content: str = "x"):
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
    from tests.conftest import mcp_auth_context

    make_note(db_session, admin_user.id, "notes/note.md")

    with mcp_auth_context(admin_session.token):
        result = await note_files.fn(path="/")

    assert any("note.md" in f for f in result)


@pytest.mark.asyncio
async def test_note_files_lists_markdown_files(
    mock_settings, db_session, admin_user, admin_session
):
    """Lists markdown notes for the current user."""
    from tests.conftest import mcp_auth_context

    make_note(db_session, admin_user.id, "notes/note1.md", "content1")
    make_note(db_session, admin_user.id, "notes/note2.md", "content2")
    make_note(db_session, admin_user.id, "notes/subdir/nested.md", "nested")

    with mcp_auth_context(admin_session.token):
        result = await note_files.fn(path="/")

    assert len(result) == 3
    assert any("note1.md" in f for f in result)
    assert any("note2.md" in f for f in result)
    assert any("nested.md" in f for f in result)
    assert not any("not_a_note.txt" in f for f in result)



@pytest.mark.asyncio
async def test_note_files_skips_hidden_paths(mock_settings, db_session, admin_user, admin_session):
    for filename in [
        "notes/note.md",
        "notes/.obsidian/workspace.json.md",
        "notes/.gitignore.md",
        "notes/sub/.notes-critic/review.md",
    ]:
        make_note(db_session, admin_user.id, filename)

    with mcp_auth_context(admin_session.token):
        result = await note_files.fn(path="/")

    assert result == ["/notes/note.md"]

@pytest.fixture
def team_visible_note(db_session, regular_user):
    """A note regular_user sees through a team's project, but didn't create."""
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
        creator_id=None,
        project_id=project.id,
        sensitivity="basic",
    )
    db_session.add(note)
    db_session.commit()
    return note


@pytest.mark.asyncio
async def test_note_files_visible_via_team_project_access(
    mock_settings, team_visible_note, user_session
):
    """A non-admin who's a member of a Team assigned to a Project should see
    notes in that project via team-project membership.

    Regression guard for the round-1 BLOCKING bug — `note_files` previously
    called `get_user_project_roles(session, user)` with a UserProxy that
    has no `.person`, crashing the query. The fix routes via
    `get_project_roles_by_user_id(user.id, session)`, mirroring deadlines.
    """
    with mcp_auth_context(user_session.token):
        result = await note_files.fn(path="/")

    # The team-visible note must be in the listing — which it can only be if
    # the team-project AC path was reached without the UserProxy.person
    # crash. (Notes have include_public=False so this isn't a public bypass.)
    assert any("team_visible.md" in f for f in result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_only, error", [(False, PermissionError), (True, ValueError)]
)
async def test_create_note_refuses_a_visible_note_the_caller_cannot_edit(
    create_only, error, mock_settings, mock_celery, team_visible_note, as_user
):
    with pytest.raises(error):
        await upsert.fn(
            subject="s", content="c", filename="team_visible.md", create_only=create_only
        )
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "narrowed, expected",
    [(False, ["/notes/mine.md", "/notes/theirs.md"]), (True, ["/notes/mine.md"])],
)
async def test_note_files_follows_the_token_scopes(
    mock_settings, db_session, admin_user, regular_user, admin_session, narrowed, expected
):
    """A narrowed key on an admin account is not admin: it lists only its own notes."""
    for name, creator in [("mine", admin_user), ("theirs", regular_user)]:
        db_session.add(
            Note(
                sha256=f"{name}-note".encode(),
                content="content",
                modality="text",
                mime_type="text/markdown",
                size=7,
                filename=f"notes/{name}.md",
                creator_id=creator.id,
                sensitivity="basic",
            )
        )
    key = APIKey.create(user_id=admin_user.id, name="narrow", scopes=["read", "notes"])
    db_session.add(key)
    db_session.commit()
    tokens = {False: (admin_session.token,), True: (key.key, ["read", "notes"])}

    with mcp_auth_context(*tokens[narrowed]):
        result = await note_files.fn(path="/")

    assert sorted(result) == expected
