"""Tests for Journal MCP tools."""

import pytest

from memory.api.MCP.servers.journal import add, list_all
from memory.common.content_processing import create_content_hash
from memory.common.db import connection as db_connection
from memory.common.db.models import JournalEntry, SourceItem, Person, Team
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
def other_user(db_session):
    """Create another user that's distinct from ``regular_user``/``admin_user``.

    Used by access-control tests that need a ``creator_id`` for a journal
    entry/poll which the calling user does NOT own. The FK to ``users``
    means we can't use a synthetic id like 999.
    """
    from memory.common.db.models import HumanUser

    user = HumanUser(
        name="Other User",
        email="other@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["teams"],
    )
    db_session.add(user)
    db_session.commit()
    return user


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
async def test_add(db_session, admin_user, admin_session, sample_item):
    """Test adding a journal entry."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(add)(
            target_id=sample_item.id,
            content="First journal entry",
        )

    assert result["status"] == "created"
    assert result["entry"]["content"] == "First journal entry"
    assert result["entry"]["target_id"] == sample_item.id
    assert result["entry"]["private"] is False


@pytest.mark.asyncio
async def test_add_private(db_session, admin_user, admin_session, sample_item):
    """Test adding a private journal entry."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(add)(
            target_id=sample_item.id,
            content="Private thought",
            private=True,
        )

    assert result["entry"]["private"] is True


@pytest.mark.asyncio
async def test_add_nonexistent_item(db_session, admin_user, admin_session):
    """Test adding a journal entry to a non-existent item."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(add)(
                target_id=999999,
                content="Should fail",
            )


@pytest.mark.asyncio
async def test_list_all(db_session, admin_user, admin_session, sample_item):
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
        result = await get_fn(list_all)(target_id=sample_item.id)

    assert len(result["entries"]) == 3
    assert result["entries"][0]["content"] == "Entry 0"
    assert result["total"] == 3


@pytest.fixture
def hidden_item(db_session, sample_project):
    """A source item with the "hidden" tombstone sensitivity."""
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("hidden item content"),
        content="hidden item content",
        project_id=sample_project.id,
        sensitivity="hidden",
    )
    db_session.add(item)
    db_session.commit()
    return item


@pytest.mark.asyncio
async def test_list_all_denies_hidden_item_for_admin(
    db_session, admin_user, admin_session, hidden_item
):
    """Journal entries on a "hidden" source_item are denied even to admins.

    The tombstone guard runs before the admin bypass in
    can_access_journal_target, so list_all can't read a hidden item's entries.
    """
    entry = JournalEntry(
        target_type="source_item",
        target_id=hidden_item.id,
        creator_id=admin_user.id,
        content="should be invisible",
        project_id=hidden_item.project_id,
    )
    db_session.add(entry)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found or access denied"):
            await get_fn(list_all)(target_id=hidden_item.id)


@pytest.mark.asyncio
async def test_add_denies_hidden_item_for_admin(
    db_session, admin_user, admin_session, hidden_item
):
    """Admins can't plant journal entries on a "hidden" source_item either."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found or access denied"):
            await get_fn(add)(target_id=hidden_item.id, content="nope")


@pytest.mark.asyncio
async def test_list_all_empty(db_session, admin_user, admin_session, sample_item):
    """Test listing journal entries when there are none."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(list_all)(target_id=sample_item.id)

    assert len(result["entries"]) == 0
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_list_all_pagination(db_session, admin_user, admin_session, sample_item):
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
        result = await get_fn(list_all)(target_id=sample_item.id, limit=3, offset=2)

    assert len(result["entries"]) == 3
    assert result["total"] == 10
    assert result["limit"] == 3
    assert result["offset"] == 2
    # Check entries are in chronological order (oldest first)
    assert result["entries"][0]["content"] == "Entry 2"


@pytest.mark.asyncio
async def test_list_all_excludes_private(
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
        result = await get_fn(list_all)(target_id=sample_item.id)

    assert len(result["entries"]) == 1
    assert result["entries"][0]["content"] == "Shared entry"


@pytest.mark.asyncio
async def test_list_all_shows_own_private(
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
        result = await get_fn(list_all)(target_id=sample_item.id)

    assert len(result["entries"]) == 1
    assert result["entries"][0]["content"] == "My private thought"


@pytest.mark.asyncio
async def test_list_all_admin_sees_all(
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
        result = await get_fn(list_all)(target_id=sample_item.id)

    assert len(result["entries"]) == 2


@pytest.mark.asyncio
async def test_list_all_nonexistent_item(db_session, admin_user, admin_session):
    """Test listing journal entries for a non-existent item."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(list_all)(target_id=999999)


@pytest.mark.asyncio
async def test_add_inherits_project(db_session, admin_user, admin_session, sample_item):
    """Test that journal entries inherit project_id from target item."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(add)(
            target_id=sample_item.id,
            content="Test entry",
        )

    assert result["entry"]["project_id"] == sample_item.project_id


# =============================================================================
# Per-target-type access control (the audit task)
# =============================================================================


@pytest.mark.asyncio
async def test_list_all_blocks_non_member_on_project(
    db_session, regular_user, user_session, sample_project, other_user
):
    """A user without team membership in a project must NOT read its journal entries."""
    # Plant a non-private entry on the project so there's something to leak.
    db_session.add(
        JournalEntry(
            target_type="project",
            target_id=sample_project.id,
            creator_id=other_user.id,  # someone else
            project_id=sample_project.id,
            content="confidential project note",
            private=False,
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found or access denied"):
            await get_fn(list_all)(
                target_id=sample_project.id,
                target_type="project",
            )


@pytest.mark.asyncio
async def test_list_all_allows_member_on_project(
    db_session, user_session, sample_project, user_with_project_access
):
    """A user WITH team membership in a project may read its journal entries."""
    db_session.add(
        JournalEntry(
            target_type="project",
            target_id=sample_project.id,
            creator_id=user_with_project_access.id,
            project_id=sample_project.id,
            content="member-visible note",
            private=False,
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await get_fn(list_all)(
            target_id=sample_project.id,
            target_type="project",
        )

    assert result["total"] == 1
    assert result["entries"][0]["content"] == "member-visible note"


@pytest.mark.asyncio
async def test_add_blocks_non_member_on_project(
    db_session, regular_user, user_session, sample_project
):
    """A user without team membership cannot plant journal entries on a project."""
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found or access denied"):
            await get_fn(add)(
                target_id=sample_project.id,
                content="<misinformation>",
                target_type="project",
            )

    # No entry was created
    rows = (
        db_session.query(JournalEntry)
        .filter(JournalEntry.target_id == sample_project.id)
        .filter(JournalEntry.target_type == "project")
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_list_all_blocks_non_member_on_team(
    db_session, regular_user, user_session, other_user
):
    """A user not in a team must NOT read its journal entries."""
    other_team = Team(name="Other Team", slug="other-team", is_active=True)
    db_session.add(other_team)
    db_session.commit()

    db_session.add(
        JournalEntry(
            target_type="team",
            target_id=other_team.id,
            creator_id=other_user.id,
            project_id=None,
            content="other-team-internal note",
            private=False,
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found or access denied"):
            await get_fn(list_all)(
                target_id=other_team.id,
                target_type="team",
            )


@pytest.mark.asyncio
async def test_list_all_blocks_non_creator_on_poll(
    db_session, regular_user, user_session, other_user
):
    """Polls have no project_id; only the creator (or admin) can read its journal."""
    from memory.common.db.models.polls import AvailabilityPoll
    from datetime import datetime, timedelta, timezone

    poll = AvailabilityPoll(
        title="someone else's poll",
        datetime_start=datetime.now(timezone.utc),
        datetime_end=datetime.now(timezone.utc) + timedelta(days=7),
        user_id=other_user.id,  # owned by a different user
    )
    db_session.add(poll)
    db_session.commit()

    db_session.add(
        JournalEntry(
            target_type="poll",
            target_id=poll.id,
            creator_id=other_user.id,
            project_id=None,
            content="poll-creator note",
            private=False,
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found or access denied"):
            await get_fn(list_all)(
                target_id=poll.id,
                target_type="poll",
            )


@pytest.mark.asyncio
async def test_admin_bypasses_per_type_gate(
    db_session, admin_session, sample_project, other_user
):
    """Admins bypass the per-target-type access gate (existing behaviour preserved)."""
    db_session.add(
        JournalEntry(
            target_type="project",
            target_id=sample_project.id,
            creator_id=other_user.id,
            project_id=sample_project.id,
            content="admin should see this",
            private=False,
        )
    )
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(list_all)(
            target_id=sample_project.id,
            target_type="project",
        )

    assert result["total"] == 1
