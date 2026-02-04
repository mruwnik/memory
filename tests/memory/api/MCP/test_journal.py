"""Tests for Journal MCP tools."""

from datetime import datetime, timedelta

import pytest

from memory.api.MCP.servers.journal import journal_add, journal_list
from memory.common.content_processing import create_content_hash
from memory.common.db import connection as db_connection
from memory.common.db.models import JournalEntry, SourceItem, HumanUser, UserSession, Person, Team
from memory.common.db.models.sources import Project, team_members, project_teams
from tests.conftest import mcp_auth_context


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped."""
    return getattr(tool, "fn", tool)


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
def sample_project(db_session):
    """Create a sample project for testing."""
    project = Project(
        title="Test Project",
        state="open",
    )
    db_session.add(project)
    db_session.commit()
    return project


@pytest.fixture
def sample_item(db_session, sample_project):
    """Create a sample source item for testing."""
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("test item content"),
        content="test item content",
        project_id=sample_project.id,
        sensitivity="basic",
    )
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def user_with_project_access(db_session, regular_user, sample_project):
    """Set up regular_user with access to sample_project via team membership."""
    # Create a person for the regular user
    person = Person(identifier="regular_person", display_name="Regular Person")
    db_session.add(person)
    db_session.flush()

    # Link regular_user to person
    regular_user.person = person
    db_session.flush()

    # Create a team
    team = Team(name="Test Team", slug="test-team", is_active=True)
    db_session.add(team)
    db_session.flush()

    # Add person to team as member
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )

    # Assign team to project
    db_session.execute(
        project_teams.insert().values(project_id=sample_project.id, team_id=team.id)
    )

    db_session.commit()
    return regular_user


@pytest.mark.asyncio
async def test_journal_add(db_session, admin_user, admin_session, sample_item):
    """Test adding a journal entry."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_add)(
            target_id=sample_item.id,
            content="First journal entry",
        )

    assert result["status"] == "created"
    assert result["entry"]["content"] == "First journal entry"
    assert result["entry"]["target_id"] == sample_item.id
    assert result["entry"]["private"] is False


@pytest.mark.asyncio
async def test_journal_add_private(db_session, admin_user, admin_session, sample_item):
    """Test adding a private journal entry."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_add)(
            target_id=sample_item.id,
            content="Private thought",
            private=True,
        )

    assert result["entry"]["private"] is True


@pytest.mark.asyncio
async def test_journal_add_nonexistent_item(db_session, admin_user, admin_session):
    """Test adding a journal entry to a non-existent item."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(journal_add)(
                target_id=999999,
                content="Should fail",
            )


@pytest.mark.asyncio
async def test_journal_list(db_session, admin_user, admin_session, sample_item):
    """Test listing journal entries for an item."""
    # Add some entries directly
    entries = [
        JournalEntry(
            target_type="source_item",
            target_id=sample_item.id,
            creator_id=admin_user.id,
            content=f"Entry {i}",
            project_id=sample_item.project_id,
        )
        for i in range(3)
    ]
    db_session.add_all(entries)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_list)(target_id=sample_item.id)

    assert len(result["entries"]) == 3
    assert result["entries"][0]["content"] == "Entry 0"
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_journal_list_empty(db_session, admin_user, admin_session, sample_item):
    """Test listing journal entries when there are none."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_list)(target_id=sample_item.id)

    assert len(result["entries"]) == 0
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_journal_list_pagination(db_session, admin_user, admin_session, sample_item):
    """Test journal list pagination."""
    # Add 10 entries
    entries = [
        JournalEntry(
            target_type="source_item",
            target_id=sample_item.id,
            creator_id=admin_user.id,
            content=f"Entry {i}",
            project_id=sample_item.project_id,
        )
        for i in range(10)
    ]
    db_session.add_all(entries)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_list)(target_id=sample_item.id, limit=3, offset=2)

    assert len(result["entries"]) == 3
    assert result["total"] == 10
    assert result["limit"] == 3
    assert result["offset"] == 2
    # Check entries are in chronological order (oldest first)
    assert result["entries"][0]["content"] == "Entry 2"


@pytest.mark.asyncio
async def test_journal_list_excludes_private(
    db_session, admin_user, user_with_project_access, user_session, sample_item
):
    """Test that private entries are excluded for non-creators."""
    # Admin creates a private entry
    private_entry = JournalEntry(
        target_type="source_item",
        target_id=sample_item.id,
        creator_id=admin_user.id,
        content="Admin's private thought",
        private=True,
        project_id=sample_item.project_id,
    )
    # Admin creates a shared entry
    shared_entry = JournalEntry(
        target_type="source_item",
        target_id=sample_item.id,
        creator_id=admin_user.id,
        content="Shared entry",
        private=False,
        project_id=sample_item.project_id,
    )
    db_session.add_all([private_entry, shared_entry])
    db_session.commit()

    # Regular user (with project access) should only see shared entry
    with mcp_auth_context(user_session.id):
        result = await get_fn(journal_list)(target_id=sample_item.id)

    assert len(result["entries"]) == 1
    assert result["entries"][0]["content"] == "Shared entry"


@pytest.mark.asyncio
async def test_journal_list_shows_own_private(
    db_session, user_with_project_access, user_session, sample_item
):
    """Test that users can see their own private entries."""
    # Regular user creates a private entry
    private_entry = JournalEntry(
        target_type="source_item",
        target_id=sample_item.id,
        creator_id=user_with_project_access.id,
        content="My private thought",
        private=True,
        project_id=sample_item.project_id,
    )
    db_session.add(private_entry)
    db_session.commit()

    # Regular user should see their own private entry
    with mcp_auth_context(user_session.id):
        result = await get_fn(journal_list)(target_id=sample_item.id)

    assert len(result["entries"]) == 1
    assert result["entries"][0]["content"] == "My private thought"


@pytest.mark.asyncio
async def test_journal_list_admin_sees_all(
    db_session, admin_user, admin_session, regular_user, sample_item
):
    """Test that admins can see all private entries."""
    # Regular user creates a private entry
    private_entry = JournalEntry(
        target_type="source_item",
        target_id=sample_item.id,
        creator_id=regular_user.id,
        content="User's private thought",
        private=True,
        project_id=sample_item.project_id,
    )
    # Shared entry
    shared_entry = JournalEntry(
        target_type="source_item",
        target_id=sample_item.id,
        creator_id=regular_user.id,
        content="Shared entry",
        private=False,
        project_id=sample_item.project_id,
    )
    db_session.add_all([private_entry, shared_entry])
    db_session.commit()

    # Admin should see both entries
    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_list)(target_id=sample_item.id)

    assert len(result["entries"]) == 2


@pytest.mark.asyncio
async def test_journal_list_nonexistent_item(db_session, admin_user, admin_session):
    """Test listing journal entries for a non-existent item."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(journal_list)(target_id=999999)


@pytest.mark.asyncio
async def test_journal_add_inherits_project(db_session, admin_user, admin_session, sample_item):
    """Test that journal entries inherit project_id from target item."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(journal_add)(
            target_id=sample_item.id,
            content="Test entry",
        )

    assert result["entry"]["project_id"] == sample_item.project_id
