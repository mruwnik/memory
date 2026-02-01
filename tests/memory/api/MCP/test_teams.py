"""Tests for Teams MCP tools with access control."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from memory.common.db.models import Person, Team, HumanUser, UserSession
from memory.common.db.models.sources import Project, team_members, project_teams
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
def admin_user(db_session):
    """Create an admin user with superadmin scope."""
    user = HumanUser(
        name="Admin User",
        email="admin@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["*"],  # Admin scope
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def regular_user(db_session):
    """Create a regular user without admin scope."""
    user = HumanUser(
        name="Regular User",
        email="regular@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["teams"],  # Only teams scope, not admin
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def user_session(db_session, regular_user):
    """Create a user session for the regular user."""
    session = UserSession(
        id="test-session-token",
        user_id=regular_user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def admin_session(db_session, admin_user):
    """Create a user session for the admin user."""
    session = UserSession(
        id="admin-session-token",
        user_id=admin_user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def teams_and_projects(db_session, regular_user, qdrant):
    """Create sample teams, projects, and memberships for testing access control."""
    # Create persons
    person1 = Person(identifier="person1", display_name="Person One")
    person2 = Person(identifier="person2", display_name="Person Two")
    db_session.add_all([person1, person2])
    db_session.flush()

    # Link regular_user to person1 (use relationship, not just ID)
    regular_user.person = person1
    db_session.flush()

    # Create teams
    team_alpha = Team(
        name="Team Alpha",
        slug="team-alpha",
        description="First team",
        tags=["engineering"],
        is_active=True,
    )
    team_beta = Team(
        name="Team Beta",
        slug="team-beta",
        description="Second team",
        tags=["design"],
        is_active=True,
    )
    team_gamma = Team(
        name="Team Gamma",
        slug="team-gamma",
        description="Third team - user not a member",
        tags=["marketing"],
        is_active=True,
    )
    db_session.add_all([team_alpha, team_beta, team_gamma])
    db_session.flush()

    # Add person1 (regular_user's person) to team_alpha and team_beta
    db_session.execute(
        team_members.insert().values(team_id=team_alpha.id, person_id=person1.id, role="member")
    )
    db_session.execute(
        team_members.insert().values(team_id=team_beta.id, person_id=person1.id, role="lead")
    )
    # Add person2 to team_gamma only
    db_session.execute(
        team_members.insert().values(team_id=team_gamma.id, person_id=person2.id, role="member")
    )

    # Create projects (standalone projects use negative IDs)
    project_one = Project(id=-1, title="Project One", description="First project", state="open")
    project_two = Project(id=-2, title="Project Two", description="Second project", state="open")
    project_three = Project(id=-3, title="Project Three", description="Third project", state="open")
    db_session.add_all([project_one, project_two, project_three])
    db_session.flush()

    # Assign teams to projects
    db_session.execute(project_teams.insert().values(project_id=-1, team_id=team_alpha.id))
    db_session.execute(project_teams.insert().values(project_id=-2, team_id=team_beta.id))
    db_session.execute(project_teams.insert().values(project_id=-3, team_id=team_gamma.id))

    db_session.commit()

    return {
        "person1": person1,
        "person2": person2,
        "team_alpha": team_alpha,
        "team_beta": team_beta,
        "team_gamma": team_gamma,
        "project_one": project_one,
        "project_two": project_two,
        "project_three": project_three,
    }


def make_mock_access_token(session_id: str | None):
    """Create a mock access token object."""
    if session_id is None:
        return None
    mock_token = MagicMock()
    mock_token.token = session_id
    return mock_token


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped, else return as-is."""
    return getattr(tool, "fn", tool)


# =============================================================================
# team_list access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,expected_team_count,expected_slugs",
    [
        pytest.param(False, 2, {"team-alpha", "team-beta"}, id="regular_user_sees_member_teams"),
        pytest.param(True, 3, {"team-alpha", "team-beta", "team-gamma"}, id="admin_sees_all_teams"),
    ],
)
async def test_team_list_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, expected_team_count, expected_slugs
):
    """Test that team_list respects access control."""
    from memory.api.MCP.servers.teams import team_list

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_list)()

    assert "teams" in result, f"Expected 'teams' in result, got: {result}"
    team_slugs = {t["slug"] for t in result["teams"]}
    # Admin may see additional teams from fixtures, so check expected teams are present
    assert expected_slugs <= team_slugs, f"Expected {expected_slugs} to be subset of {team_slugs}"
    assert result["count"] >= expected_team_count


@pytest.mark.asyncio
async def test_team_list_unauthenticated_returns_error(db_session):
    """Unauthenticated requests should return an error."""
    from memory.api.MCP.servers.teams import team_list

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=None),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_list)()

    assert "error" in result
    assert "Not authenticated" in result["error"]


# =============================================================================
# team_get access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,team_slug,expect_success",
    [
        pytest.param(False, "team-alpha", True, id="regular_user_can_access_member_team"),
        pytest.param(False, "team-gamma", False, id="regular_user_cannot_access_non_member_team"),
        pytest.param(True, "team-gamma", True, id="admin_can_access_any_team"),
    ],
)
async def test_team_get_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, team_slug, expect_success
):
    """Test that team_get respects access control."""
    from memory.api.MCP.servers.teams import team_get

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_get)(team_slug)

    if expect_success:
        assert "team" in result, f"Expected team in result, got: {result}"
        assert result["team"]["slug"] == team_slug
    else:
        assert "error" in result
        assert "Team not found" in result["error"]


# =============================================================================
# project_list_teams access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,project_id,expect_success",
    [
        pytest.param(False, -1, True, id="regular_user_can_access_accessible_project"),
        pytest.param(False, -3, False, id="regular_user_cannot_access_inaccessible_project"),
        pytest.param(True, -3, True, id="admin_can_access_any_project"),
    ],
)
async def test_project_list_teams_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, project_id, expect_success
):
    """Test that project_list_teams respects access control."""
    from memory.api.MCP.servers.teams import project_list_teams

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_list_teams)(project_id)

    if expect_success:
        assert "teams" in result, f"Expected teams in result, got: {result}"
    else:
        assert "error" in result
        assert "Project not found" in result["error"]


# =============================================================================
# team_list with include_projects tests
# =============================================================================


@pytest.mark.asyncio
async def test_team_list_includes_projects_when_requested(
    db_session, user_session, teams_and_projects
):
    """team_list should include projects when include_projects=True."""
    from memory.api.MCP.servers.teams import team_list

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_list)(include_projects=True)

    assert "teams" in result
    team_alpha = next((t for t in result["teams"] if t["slug"] == "team-alpha"), None)
    assert team_alpha is not None
    assert "projects" in team_alpha
    assert len(team_alpha["projects"]) == 1
    assert team_alpha["projects"][0]["title"] == "Project One"


# =============================================================================
# team_add_member access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,team_slug,expect_success",
    [
        pytest.param(False, "team-alpha", True, id="regular_user_can_add_to_member_team"),
        pytest.param(False, "team-gamma", False, id="regular_user_cannot_add_to_non_member_team"),
        pytest.param(True, "team-gamma", True, id="admin_can_add_to_any_team"),
    ],
)
async def test_team_add_member_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, team_slug, expect_success
):
    """Test that team_add_member respects access control."""
    from memory.api.MCP.servers.teams import team_add_member

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_add_member)(team_slug, "person2", sync_external=False)

    if expect_success:
        assert result.get("success") is True or result.get("already_member") is True
    else:
        assert "error" in result
        assert "Team not found" in result["error"]


# =============================================================================
# project_assign_team access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,project_id,team_slug,expect_error",
    [
        pytest.param(False, -1, "team-gamma", "Team not found", id="requires_team_access"),
        pytest.param(False, -3, "team-alpha", "Project not found", id="requires_project_access"),
        pytest.param(True, -3, "team-alpha", None, id="admin_can_assign_any"),
    ],
)
async def test_project_assign_team_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, project_id, team_slug, expect_error
):
    """Test that project_assign_team respects access control."""
    from memory.api.MCP.servers.teams import project_assign_team

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_assign_team)(project_id, team_slug)

    if expect_error:
        assert "error" in result
        assert expect_error in result["error"]
    else:
        assert result.get("success") is True or result.get("already_assigned") is True


# =============================================================================
# Unit tests for helper functions
# =============================================================================


@pytest.mark.parametrize(
    "name,expected_slug",
    [
        pytest.param("Engineering Core", "engineering-core", id="spaces_to_hyphens"),
        pytest.param("Team Beta!", "team-beta", id="removes_special_chars"),
        pytest.param("  Whitespace  ", "whitespace", id="trims_whitespace"),
        pytest.param("Multiple   Spaces", "multiple-spaces", id="collapses_spaces"),
        pytest.param("CamelCase", "camelcase", id="lowercases"),
        pytest.param("with-hyphen", "with-hyphen", id="preserves_hyphens"),
        pytest.param("123 Numbers", "123-numbers", id="keeps_numbers"),
        pytest.param("---leading---", "leading", id="strips_leading_trailing_hyphens"),
    ],
)
def test_make_slug(name, expected_slug):
    """Test make_slug generates correct URL-safe slugs."""
    from memory.api.MCP.servers.teams import make_slug

    assert make_slug(name) == expected_slug


def test_resolve_guild_with_int(db_session):
    """Test resolve_guild returns int directly."""
    from memory.common import discord as discord_client

    assert discord_client.resolve_guild(123456789, db_session) == 123456789


def test_resolve_guild_with_numeric_string(db_session):
    """Test resolve_guild parses numeric string as ID."""
    from memory.common import discord as discord_client

    assert discord_client.resolve_guild("123456789", db_session) == 123456789


def test_resolve_guild_with_name(db_session):
    """Test resolve_guild looks up server by name."""
    from memory.common import discord as discord_client
    from memory.common.db.models import DiscordServer

    # Create a Discord server
    server = DiscordServer(id=987654321, name="Test Server")
    db_session.add(server)
    db_session.commit()

    assert discord_client.resolve_guild("Test Server", db_session) == 987654321


def test_resolve_guild_not_found(db_session):
    """Test resolve_guild raises for unknown server name."""
    from memory.common import discord as discord_client

    with pytest.raises(ValueError, match="Discord server 'Unknown Server' not found"):
        discord_client.resolve_guild("Unknown Server", db_session)


def test_resolve_guild_none(db_session):
    """Test resolve_guild returns None for None input."""
    from memory.common import discord as discord_client

    assert discord_client.resolve_guild(None, db_session) is None


def test_find_or_create_person_creates_new(db_session):
    """Test find_or_create_person creates a new person."""
    from memory.api.MCP.servers.teams import find_or_create_person

    person = find_or_create_person(
        db_session,
        identifier="alice_chen",
        display_name="Alice Chen",
        contact_info={"github_username": "alicechen"},
    )

    assert person.identifier == "alice_chen"
    assert person.display_name == "Alice Chen"
    assert person.contact_info == {"github_username": "alicechen"}


def test_find_or_create_person_finds_existing(db_session):
    """Test find_or_create_person returns existing person."""
    from memory.api.MCP.servers.teams import find_or_create_person

    # Create first
    person1 = find_or_create_person(db_session, "bob_smith", "Bob Smith")
    db_session.commit()

    # Should find existing
    person2 = find_or_create_person(db_session, "bob_smith", "Robert Smith")

    assert person1.id == person2.id
    assert person2.display_name == "Bob Smith"  # Original name preserved


def test_find_or_create_person_normalizes_identifier(db_session):
    """Test find_or_create_person normalizes identifiers."""
    from memory.api.MCP.servers.teams import find_or_create_person

    person = find_or_create_person(db_session, "Alice Chen", "Alice Chen")
    assert person.identifier == "alice_chen"


# =============================================================================
# upsert() tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_creates_new_team(db_session, admin_session, qdrant):
    """Test upsert creates a new team."""
    from memory.api.MCP.servers.teams import upsert

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="New Test Team",
            slug="new-test-team",
            description="A test team",
            tags=["test", "engineering"],
        )

    assert result["success"] is True
    assert result["action"] == "created"
    assert result["team"]["name"] == "New Test Team"
    assert result["team"]["slug"] == "new-test-team"
    assert result["team"]["description"] == "A test team"
    assert result["team"]["tags"] == ["test", "engineering"]


@pytest.mark.asyncio
async def test_upsert_updates_existing_team(db_session, admin_session, qdrant):
    """Test upsert updates an existing team."""
    from memory.api.MCP.servers.teams import upsert

    # Create initial team
    team = Team(name="Original Name", slug="update-me", description="Original")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Updated Name",
            slug="update-me",
            description="Updated description",
        )

    assert result["success"] is True
    assert result["action"] == "updated"
    assert result["team"]["name"] == "Updated Name"
    assert result["team"]["description"] == "Updated description"


@pytest.mark.asyncio
async def test_upsert_auto_generates_slug(db_session, admin_session, qdrant):
    """Test upsert generates slug from name if not provided."""
    from memory.api.MCP.servers.teams import upsert

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(name="Auto Slug Team")

    assert result["success"] is True
    assert result["team"]["slug"] == "auto-slug-team"


@pytest.mark.asyncio
async def test_upsert_with_members_list(db_session, admin_session, qdrant):
    """Test upsert with explicit members list."""
    from memory.api.MCP.servers.teams import upsert

    # Create some people
    person1 = Person(identifier="member_one", display_name="Member One")
    person2 = Person(identifier="member_two", display_name="Member Two")
    db_session.add_all([person1, person2])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.sync_membership_add", return_value={}),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Members Test Team",
            members=["member_one", "member_two"],
        )

    assert result["success"] is True
    assert "member_one" in result["membership_changes"]["added"]
    assert "member_two" in result["membership_changes"]["added"]
    assert len(result["team"]["members"]) == 2


@pytest.mark.asyncio
async def test_upsert_creates_missing_person(db_session, admin_session, qdrant):
    """Test upsert creates Person for unknown member identifier."""
    from memory.api.MCP.servers.teams import upsert

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.sync_membership_add", return_value={}),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Create Person Team",
            members=["new_person"],
        )

    assert result["success"] is True
    assert "new_person" in result["membership_changes"]["created_people"]
    assert "new_person" in result["membership_changes"]["added"]


@pytest.mark.asyncio
async def test_upsert_clears_members_with_empty_list(db_session, admin_session, qdrant):
    """Test upsert removes all members when passed empty list."""
    from memory.api.MCP.servers.teams import upsert

    # Create team with members
    team = Team(name="Clear Me", slug="clear-me")
    person = Person(identifier="existing_member", display_name="Existing")
    db_session.add_all([team, person])
    db_session.flush()
    team.members.append(person)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.sync_membership_remove", return_value={}),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Clear Me",
            slug="clear-me",
            members=[],
        )

    assert result["success"] is True
    assert "existing_member" in result["membership_changes"]["removed"]
    assert "Removed all" in result["membership_changes"]["warnings"][0]
    assert len(result["team"]["members"]) == 0


@pytest.mark.asyncio
async def test_upsert_with_discord_guild_by_id(db_session, admin_session, qdrant):
    """Test upsert with Discord guild specified by ID."""
    from memory.api.MCP.servers.teams import upsert

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Discord Team",
            guild=123456789,
            auto_sync_discord=True,
        )

    assert result["success"] is True
    assert result["team"]["discord_guild_id"] == 123456789
    assert result["team"]["auto_sync_discord"] is True


@pytest.mark.asyncio
async def test_upsert_with_discord_role_creates_role(db_session, admin_session, qdrant):
    """Test upsert creates Discord role when it doesn't exist."""
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models import DiscordServer

    # Create Discord server
    server = DiscordServer(id=111222333, name="Test Guild")
    db_session.add(server)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    # Mock Discord API calls
    mock_role_result = {"success": True, "role": {"id": "999888777", "name": "New Role"}}

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_roles", return_value={"roles": []}),
        patch("memory.common.discord.create_role", return_value=mock_role_result),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Role Test Team",
            guild=111222333,
            discord_role="New Role",
        )

    assert result["success"] is True
    assert result["discord_sync"].get("role_created") is True
    assert result["team"]["discord_role_id"] == 999888777


@pytest.mark.asyncio
async def test_upsert_with_existing_discord_role(db_session, admin_session, qdrant):
    """Test upsert links to existing Discord role."""
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=111222333, name="Test Guild")
    db_session.add(server)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    # Mock Discord API - role exists
    mock_roles = {"roles": [{"id": "555666777", "name": "Existing Role"}]}

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_roles", return_value=mock_roles),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Existing Role Team",
            guild=111222333,
            discord_role="Existing Role",
        )

    assert result["success"] is True
    assert result["discord_sync"].get("role_created") is not True
    assert result["team"]["discord_role_id"] == 555666777


@pytest.mark.asyncio
async def test_team_create_alias_removed(db_session, admin_session, qdrant):
    """Test that team_create alias was removed - upsert is the canonical name."""
    import importlib.util

    spec = importlib.util.find_spec("memory.api.MCP.servers.teams")
    assert spec is not None
    # team_create was removed, only upsert exists now
    from memory.api.MCP.servers.teams import upsert
    assert upsert is not None


@pytest.mark.asyncio
async def test_upsert_with_github_org(db_session, admin_session, qdrant):
    """Test upsert with GitHub organization."""
    from memory.api.MCP.servers.teams import upsert

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="GitHub Team",
            github_org="myorg",
            github_team_slug="myteam",
            auto_sync_github=True,
        )

    assert result["success"] is True
    assert result["team"]["github_org"] == "myorg"
    assert result["team"]["github_team_slug"] == "myteam"
    assert result["team"]["auto_sync_github"] is True


# =============================================================================
# Discord create_role tests
# =============================================================================


@pytest.mark.asyncio
async def test_discord_create_role_basic(db_session, admin_session):
    """Test Discord create MCP tool for roles."""
    from memory.api.MCP.servers.discord import create
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=123456789, name="Test Server")
    db_session.add(server)
    db_session.commit()

    mock_result = {"success": True, "role": {"id": "111", "name": "Test Role", "color": 0}}
    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.discord.resolve_bot_id", return_value=1),
        patch("memory.api.MCP.servers.discord.make_session") as mock_make_session,
        patch("memory.common.discord.create_role", return_value=mock_result) as mock_create,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(create)(
            name="Test Role",
            guild=123456789,
        )

    assert result["success"] is True
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_discord_create_role_with_options(db_session, admin_session):
    """Test Discord create with color and mentionable options."""
    from memory.api.MCP.servers.discord import create
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=123456789, name="Test Server")
    db_session.add(server)
    db_session.commit()

    mock_result = {
        "success": True,
        "role": {"id": "222", "name": "Colored Role", "color": 16711680},
    }
    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.discord.resolve_bot_id", return_value=1),
        patch("memory.api.MCP.servers.discord.make_session") as mock_make_session,
        patch("memory.common.discord.create_role", return_value=mock_result) as mock_create,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(create)(
            name="Colored Role",
            guild="Test Server",
            color=16711680,  # Red
            mentionable=True,
            hoist=True,
        )

    assert result["success"] is True
    mock_create.assert_called_once_with(
        1, 123456789, "Colored Role",
        color=16711680,
        mentionable=True,
        hoist=True,
    )


def test_discord_resolve_role_finds_existing():
    """Test discord_client.resolve_role finds existing role by name."""
    from memory.common import discord as discord_client

    mock_roles = {"roles": [
        {"id": "111", "name": "Admin"},
        {"id": "222", "name": "Member"},
    ]}

    with patch("memory.common.discord.list_roles", return_value=mock_roles):
        role_id, created = discord_client.resolve_role(
            "Member",
            guild_id=123,
            bot_id=1,
            create_if_missing=False,
        )

    assert role_id == 222
    assert created is False


def test_discord_resolve_role_creates_when_missing():
    """Test discord_client.resolve_role creates role when missing."""
    from memory.common import discord as discord_client

    mock_roles = {"roles": []}
    mock_created = {"success": True, "role": {"id": "333", "name": "New Role"}}

    with (
        patch("memory.common.discord.list_roles", return_value=mock_roles),
        patch("memory.common.discord.create_role", return_value=mock_created),
    ):
        role_id, created = discord_client.resolve_role(
            "New Role",
            guild_id=123,
            bot_id=1,
            create_if_missing=True,
        )

    assert role_id == 333
    assert created is True


# =============================================================================
# GitHub create_team tests
# =============================================================================


def test_github_create_team_success():
    """Test GitHub create_team method."""
    from memory.common.github import GithubClient, GithubCredentials

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "id": 12345,
        "node_id": "T_123",
        "slug": "new-team",
        "name": "New Team",
        "description": "A new team",
        "privacy": "closed",
    }
    mock_response.headers = {}

    credentials = GithubCredentials(
        auth_type="pat",
        access_token="test-token",
    )
    client = GithubClient(credentials)

    with patch.object(client.session, "post", return_value=mock_response):
        result = client.create_team(
            org="testorg",
            name="New Team",
            description="A new team",
        )

    assert result is not None
    assert result["id"] == 12345
    assert result["slug"] == "new-team"
    assert result["name"] == "New Team"


def test_github_create_team_already_exists():
    """Test GitHub create_team when team already exists."""
    from memory.common.github import GithubClient, GithubCredentials

    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.json.return_value = {"message": "Team already exists"}

    credentials = GithubCredentials(
        auth_type="pat",
        access_token="test-token",
    )
    client = GithubClient(credentials)

    with patch.object(client.session, "post", return_value=mock_response):
        result = client.create_team(org="testorg", name="Existing Team")

    assert result is None


# =============================================================================
# team_update tests
# =============================================================================


@pytest.mark.asyncio
async def test_team_update_basic(db_session, admin_session, qdrant):
    """Test team_update modifies team fields."""
    from memory.api.MCP.servers.teams import team_update

    team = Team(name="Original", slug="update-test", description="Original desc")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(
            team="update-test",
            name="Updated Name",
            description="Updated desc",
            tags=["new-tag"],
        )

    assert result["success"] is True
    assert result["team"]["name"] == "Updated Name"
    assert result["team"]["description"] == "Updated desc"
    assert result["team"]["tags"] == ["new-tag"]


@pytest.mark.asyncio
async def test_team_update_discord_settings_by_id(db_session, admin_session, qdrant):
    """Test team_update modifies Discord integration settings with guild and role IDs."""
    from memory.api.MCP.servers.teams import team_update

    team = Team(name="Discord Team", slug="discord-update-test")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(
            team="discord-update-test",
            discord_role=123456789,  # Using unified discord_role parameter with ID
            guild=987654321,  # Using unified guild parameter with ID
            auto_sync_discord=True,
        )

    assert result["success"] is True
    assert result["team"]["discord_role_id"] == 123456789
    assert result["team"]["discord_guild_id"] == 987654321
    assert result["team"]["auto_sync_discord"] is True


@pytest.mark.asyncio
async def test_team_update_discord_settings_by_name(db_session, admin_session, qdrant):
    """Test team_update accepts guild and role by name."""
    from memory.api.MCP.servers.teams import team_update
    from memory.common.db.models import DiscordServer

    team = Team(name="Discord Team 2", slug="discord-update-test-2")
    server = DiscordServer(id=555666777, name="My Test Server")
    db_session.add_all([team, server])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)
    mock_roles = {"roles": [{"id": "888999000", "name": "Developer Role"}]}

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_roles", return_value=mock_roles),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(
            team="discord-update-test-2",
            guild="My Test Server",  # Using server name instead of ID
            discord_role="Developer Role",  # Using role name instead of ID
            auto_sync_discord=True,
        )

    assert result["success"] is True
    assert result["team"]["discord_guild_id"] == 555666777  # Resolved from name
    assert result["team"]["discord_role_id"] == 888999000  # Resolved from name
    assert result["team"]["auto_sync_discord"] is True


@pytest.mark.asyncio
async def test_team_update_discord_role_without_guild_fails(db_session, admin_session, qdrant):
    """Test team_update fails when resolving role by name without guild."""
    from memory.api.MCP.servers.teams import team_update

    team = Team(name="Discord Team 3", slug="discord-update-test-3")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(
            team="discord-update-test-3",
            discord_role="Some Role",  # Role name without guild should fail
        )

    assert "error" in result
    assert "without guild" in result["error"]


@pytest.mark.asyncio
async def test_team_update_github_settings(db_session, admin_session, qdrant):
    """Test team_update modifies GitHub integration settings."""
    from memory.api.MCP.servers.teams import team_update

    team = Team(name="GitHub Team", slug="github-update-test")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(
            team="github-update-test",
            github_org="myorg",
            github_team_slug="myteam",
            github_team_id=12345,
            auto_sync_github=True,
        )

    assert result["success"] is True
    assert result["team"]["github_org"] == "myorg"
    assert result["team"]["github_team_slug"] == "myteam"
    assert result["team"]["github_team_id"] == 12345
    assert result["team"]["auto_sync_github"] is True


@pytest.mark.asyncio
async def test_team_update_archive(db_session, admin_session, qdrant):
    """Test team_update can archive a team."""
    from memory.api.MCP.servers.teams import team_update

    team = Team(name="Archive Me", slug="archive-test", is_active=True)
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(
            team="archive-test",
            is_active=False,
        )

    assert result["success"] is True
    assert result["team"]["is_active"] is False
    assert result["team"]["archived_at"] is not None


@pytest.mark.asyncio
async def test_team_update_not_found(db_session, admin_session, qdrant):
    """Test team_update returns error for non-existent team."""
    from memory.api.MCP.servers.teams import team_update

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_update)(team="nonexistent")

    assert "error" in result
    assert "Team not found" in result["error"]


# =============================================================================
# team_remove_member tests
# =============================================================================


@pytest.mark.asyncio
async def test_team_remove_member_success(db_session, admin_session, qdrant):
    """Test team_remove_member removes a person from a team."""
    from memory.api.MCP.servers.teams import team_remove_member
    from memory.common.db.models.sources import team_members

    team = Team(name="Remove Test", slug="remove-member-test")
    person = Person(identifier="removable", display_name="Removable Person")
    db_session.add_all([team, person])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.sync_membership_remove", return_value={}),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_remove_member)(
            team="remove-member-test",
            person="removable",
            sync_external=False,
        )

    assert result["success"] is True
    assert result["person"] == "removable"


@pytest.mark.asyncio
async def test_team_remove_member_not_a_member(db_session, admin_session, qdrant):
    """Test team_remove_member handles non-member gracefully."""
    from memory.api.MCP.servers.teams import team_remove_member

    team = Team(name="Remove Test 2", slug="remove-test-2")
    person = Person(identifier="not_member", display_name="Not Member")
    db_session.add_all([team, person])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_remove_member)(
            team="remove-test-2",
            person="not_member",
        )

    assert result["success"] is True
    assert result.get("was_not_member") is True


@pytest.mark.asyncio
async def test_team_remove_member_with_discord_sync(db_session, admin_session, qdrant):
    """Test team_remove_member syncs to Discord when enabled."""
    from memory.api.MCP.servers.teams import team_remove_member
    from memory.common.db.models.sources import team_members
    from memory.common.db.models import DiscordUser

    team = Team(
        name="Discord Sync Team",
        slug="discord-sync-remove",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=True,
    )
    person = Person(identifier="discord_user", display_name="Discord User")
    db_session.add_all([team, person])
    db_session.flush()

    # Add Discord account to person
    discord_user = DiscordUser(id=333, username="discorduser", person_id=person.id)
    db_session.add(discord_user)
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.remove_role_member", return_value={"success": True}),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_remove_member)(
            team="discord-sync-remove",
            person="discord_user",
            sync_external=True,
        )

    assert result["success"] is True
    assert "sync" in result


# =============================================================================
# team_list_members tests
# =============================================================================


@pytest.mark.asyncio
async def test_team_list_members_success(db_session, admin_session, qdrant):
    """Test team_list_members returns all members with roles."""
    from memory.api.MCP.servers.teams import team_list_members
    from memory.common.db.models.sources import team_members

    team = Team(name="List Members Test", slug="list-members-test")
    person1 = Person(identifier="member1", display_name="Member One")
    person2 = Person(identifier="member2", display_name="Member Two")
    db_session.add_all([team, person1, person2])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person1.id, role="admin")
    )
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person2.id, role="member")
    )
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_list_members)(team="list-members-test")

    assert result["team"] == "list-members-test"
    assert result["count"] == 2
    members_by_id = {m["identifier"]: m for m in result["members"]}
    assert members_by_id["member1"]["role"] == "admin"
    assert members_by_id["member2"]["role"] == "member"


@pytest.mark.asyncio
async def test_team_list_members_empty(db_session, admin_session, qdrant):
    """Test team_list_members returns empty list for team with no members."""
    from memory.api.MCP.servers.teams import team_list_members

    team = Team(name="Empty Team", slug="empty-team")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_list_members)(team="empty-team")

    assert result["count"] == 0
    assert result["members"] == []


# =============================================================================
# project_unassign_team tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_unassign_team_success(db_session, admin_session, qdrant):
    """Test project_unassign_team removes team from project."""
    from memory.api.MCP.servers.teams import project_unassign_team
    from memory.common.db.models.sources import project_teams

    team = Team(name="Unassign Test", slug="unassign-test")
    project = Project(id=-100, title="Unassign Project", state="open")
    db_session.add_all([team, project])
    db_session.flush()
    db_session.execute(
        project_teams.insert().values(project_id=project.id, team_id=team.id)
    )
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_unassign_team)(project=-100, team="unassign-test")

    assert result["success"] is True
    assert result["team"]["slug"] == "unassign-test"


@pytest.mark.asyncio
async def test_project_unassign_team_not_assigned(db_session, admin_session, qdrant):
    """Test project_unassign_team handles not-assigned gracefully."""
    from memory.api.MCP.servers.teams import project_unassign_team

    team = Team(name="Not Assigned", slug="not-assigned")
    project = Project(id=-101, title="Not Assigned Project", state="open")
    db_session.add_all([team, project])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_unassign_team)(project=-101, team="not-assigned")

    assert result["success"] is True
    assert result.get("was_not_assigned") is True


# =============================================================================
# project_list_access tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_list_access_success(db_session, admin_session, qdrant):
    """Test project_list_access returns all people with access."""
    from memory.api.MCP.servers.teams import project_list_access
    from memory.common.db.models.sources import team_members, project_teams

    team1 = Team(name="Access Team 1", slug="access-team-1")
    team2 = Team(name="Access Team 2", slug="access-team-2")
    person1 = Person(identifier="access_person1", display_name="Access Person 1")
    person2 = Person(identifier="access_person2", display_name="Access Person 2")
    person3 = Person(identifier="access_person3", display_name="Access Person 3")
    project = Project(id=-102, title="Access Project", state="open")
    db_session.add_all([team1, team2, person1, person2, person3, project])
    db_session.flush()

    # person1 in team1, person2 in team2, person3 in both
    db_session.execute(team_members.insert().values(team_id=team1.id, person_id=person1.id))
    db_session.execute(team_members.insert().values(team_id=team2.id, person_id=person2.id))
    db_session.execute(team_members.insert().values(team_id=team1.id, person_id=person3.id))
    db_session.execute(team_members.insert().values(team_id=team2.id, person_id=person3.id))
    db_session.execute(project_teams.insert().values(project_id=project.id, team_id=team1.id))
    db_session.execute(project_teams.insert().values(project_id=project.id, team_id=team2.id))
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_list_access)(project=-102)

    assert result["total_people_count"] == 3
    assert len(result["teams"]) == 2
    identifiers = {p["identifier"] for p in result["all_people"]}
    assert identifiers == {"access_person1", "access_person2", "access_person3"}


# =============================================================================
# projects_for_person tests
# =============================================================================


@pytest.mark.asyncio
async def test_projects_for_person_success(db_session, admin_session, qdrant):
    """Test projects_for_person returns all accessible projects."""
    from memory.api.MCP.servers.teams import projects_for_person
    from memory.common.db.models.sources import team_members, project_teams

    team = Team(name="Person Projects Team", slug="person-projects-team")
    person = Person(identifier="project_accessor", display_name="Project Accessor")
    project1 = Project(id=-103, title="Project 1", state="open")
    project2 = Project(id=-104, title="Project 2", state="open")
    db_session.add_all([team, person, project1, project2])
    db_session.flush()

    db_session.execute(team_members.insert().values(team_id=team.id, person_id=person.id))
    db_session.execute(project_teams.insert().values(project_id=project1.id, team_id=team.id))
    db_session.execute(project_teams.insert().values(project_id=project2.id, team_id=team.id))
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(projects_for_person)(person="project_accessor")

    assert result["person"] == "project_accessor"
    assert result["count"] == 2
    titles = {p["title"] for p in result["projects"]}
    assert titles == {"Project 1", "Project 2"}


@pytest.mark.asyncio
async def test_projects_for_person_no_projects(db_session, admin_session, qdrant):
    """Test projects_for_person returns empty for person with no projects."""
    from memory.api.MCP.servers.teams import projects_for_person

    person = Person(identifier="no_projects", display_name="No Projects")
    db_session.add(person)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(projects_for_person)(person="no_projects")

    assert result["count"] == 0
    assert result["projects"] == []


# =============================================================================
# check_project_access tests
# =============================================================================


@pytest.mark.asyncio
async def test_check_project_access_has_access(db_session, admin_session, qdrant):
    """Test check_project_access returns true when person has access."""
    from memory.api.MCP.servers.teams import check_project_access
    from memory.common.db.models.sources import team_members, project_teams

    team = Team(name="Check Access Team", slug="check-access-team")
    person = Person(identifier="has_access", display_name="Has Access")
    project = Project(id=-105, title="Check Access Project", state="open")
    db_session.add_all([team, person, project])
    db_session.flush()

    db_session.execute(team_members.insert().values(team_id=team.id, person_id=person.id))
    db_session.execute(project_teams.insert().values(project_id=project.id, team_id=team.id))
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(check_project_access)(person="has_access", project=-105)

    assert result["has_access"] is True
    assert len(result["granting_teams"]) == 1
    assert result["granting_teams"][0]["slug"] == "check-access-team"


@pytest.mark.asyncio
async def test_check_project_access_no_access(db_session, admin_session, qdrant):
    """Test check_project_access returns false when person lacks access."""
    from memory.api.MCP.servers.teams import check_project_access

    person = Person(identifier="no_access", display_name="No Access")
    project = Project(id=-106, title="No Access Project", state="open")
    db_session.add_all([person, project])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(check_project_access)(person="no_access", project=-106)

    assert result["has_access"] is False
    assert result["granting_teams"] == []


# =============================================================================
# teams_by_tag tests
# =============================================================================


@pytest.mark.asyncio
async def test_teams_by_tag_match_all(db_session, admin_session, qdrant):
    """Test teams_by_tag with match_all=True requires all tags."""
    from memory.api.MCP.servers.teams import teams_by_tag

    team1 = Team(name="Tag Team 1", slug="tag-team-1", tags=["engineering", "frontend"])
    team2 = Team(name="Tag Team 2", slug="tag-team-2", tags=["engineering", "backend"])
    team3 = Team(name="Tag Team 3", slug="tag-team-3", tags=["engineering", "frontend", "core"])
    db_session.add_all([team1, team2, team3])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(teams_by_tag)(tags=["engineering", "frontend"], match_all=True)

    slugs = {t["slug"] for t in result["teams"]}
    assert "tag-team-1" in slugs
    assert "tag-team-3" in slugs
    assert "tag-team-2" not in slugs  # doesn't have frontend


@pytest.mark.asyncio
async def test_teams_by_tag_match_any(db_session, admin_session, qdrant):
    """Test teams_by_tag with match_all=False matches any tag."""
    from memory.api.MCP.servers.teams import teams_by_tag

    team1 = Team(name="Any Tag 1", slug="any-tag-1", tags=["design"])
    team2 = Team(name="Any Tag 2", slug="any-tag-2", tags=["marketing"])
    team3 = Team(name="Any Tag 3", slug="any-tag-3", tags=["sales"])
    db_session.add_all([team1, team2, team3])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(teams_by_tag)(tags=["design", "marketing"], match_all=False)

    slugs = {t["slug"] for t in result["teams"]}
    assert "any-tag-1" in slugs
    assert "any-tag-2" in slugs
    assert "any-tag-3" not in slugs  # has neither design nor marketing


# =============================================================================
# person_teams tests
# =============================================================================


@pytest.mark.asyncio
async def test_person_teams_success(db_session, admin_session, qdrant):
    """Test person_teams returns all teams for a person."""
    from memory.api.MCP.servers.teams import person_teams
    from memory.common.db.models.sources import team_members

    person = Person(identifier="multi_team", display_name="Multi Team Person")
    team1 = Team(name="Person Team 1", slug="person-team-1")
    team2 = Team(name="Person Team 2", slug="person-team-2")
    db_session.add_all([person, team1, team2])
    db_session.flush()

    db_session.execute(team_members.insert().values(team_id=team1.id, person_id=person.id))
    db_session.execute(team_members.insert().values(team_id=team2.id, person_id=person.id))
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(person_teams)(person="multi_team")

    assert result["person"] == "multi_team"
    assert result["count"] == 2
    slugs = {t["slug"] for t in result["teams"]}
    assert slugs == {"person-team-1", "person-team-2"}


@pytest.mark.asyncio
async def test_person_teams_not_found(db_session, admin_session, qdrant):
    """Test person_teams returns error for non-existent person."""
    from memory.api.MCP.servers.teams import person_teams

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(person_teams)(person="nonexistent_person")

    assert "error" in result
    assert "Person not found" in result["error"]


# =============================================================================
# sync_from_discord tests
# =============================================================================


@pytest.mark.asyncio
async def test_sync_from_discord_imports_members(db_session, admin_session, qdrant):
    """Test sync_from_discord imports Discord role members to team."""
    from memory.api.MCP.servers.teams import sync_from_discord

    team = Team(name="Discord Import", slug="discord-import")
    db_session.add(team)
    db_session.commit()

    mock_members = {
        "members": [
            {"id": "111", "username": "user1", "display_name": "User One"},
            {"id": "222", "username": "user2", "display_name": "User Two"},
        ]
    }

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_role_members", return_value=mock_members),
    ):
        result = await sync_from_discord(
            db_session, "discord-import", guild_id=123, role_id=456
        )

    assert result["imported"] == 2
    assert len(result["created_people"]) == 2


@pytest.mark.asyncio
async def test_sync_from_discord_links_existing_discord_user(db_session, admin_session, qdrant):
    """Test sync_from_discord links existing DiscordUser to team."""
    from memory.api.MCP.servers.teams import sync_from_discord
    from memory.common.db.models import DiscordUser

    team = Team(name="Discord Link", slug="discord-link")
    person = Person(identifier="existing_discord", display_name="Existing Discord")
    db_session.add_all([team, person])
    db_session.flush()

    discord_user = DiscordUser(id=333, username="existing", person_id=person.id)
    db_session.add(discord_user)
    db_session.commit()

    mock_members = {
        "members": [
            {"id": "333", "username": "existing", "display_name": "Existing"},
        ]
    }

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_role_members", return_value=mock_members),
    ):
        result = await sync_from_discord(
            db_session, "discord-link", guild_id=123, role_id=456
        )

    assert result["imported"] == 1
    assert len(result["created_people"]) == 0  # No new person created


@pytest.mark.asyncio
async def test_sync_from_discord_handles_failure(db_session, admin_session, qdrant):
    """Test sync_from_discord handles API failure gracefully."""
    from memory.api.MCP.servers.teams import sync_from_discord

    team = Team(name="Discord Fail", slug="discord-fail")
    db_session.add(team)
    db_session.commit()

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_role_members", side_effect=Exception("API Error")),
    ):
        result = await sync_from_discord(
            db_session, "discord-fail", guild_id=123, role_id=456
        )

    assert result["imported"] == 0
    assert result["created_people"] == []


# =============================================================================
# sync_from_github tests
# =============================================================================


@pytest.mark.asyncio
async def test_sync_from_github_imports_members(db_session, admin_session, qdrant):
    """Test sync_from_github imports GitHub team members."""
    from memory.api.MCP.servers.teams import sync_from_github

    team = Team(name="GitHub Import", slug="github-import")
    db_session.add(team)
    db_session.commit()

    mock_members = [
        {"login": "ghuser1"},
        {"login": "ghuser2"},
    ]
    mock_client = MagicMock()
    mock_client.get_team_members.return_value = mock_members
    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        result = await sync_from_github(
            db_session, "github-import", org="myorg", github_team_slug="myteam"
        )

    assert result["imported"] == 2
    assert len(result["created_people"]) == 2


@pytest.mark.asyncio
async def test_sync_from_github_no_client(db_session, admin_session, qdrant):
    """Test sync_from_github returns empty when no GitHub client available."""
    from memory.api.MCP.servers.teams import sync_from_github

    team = Team(name="GitHub No Client", slug="github-no-client")
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=None),
    ):
        result = await sync_from_github(
            db_session, "github-no-client", org="myorg", github_team_slug="myteam"
        )

    assert result["imported"] == 0


# =============================================================================
# Discord role sync tests (_discord_add_role, _discord_remove_role)
# =============================================================================


@pytest.mark.asyncio
async def test_discord_add_role_success(db_session, qdrant):
    """Test _discord_add_role adds role to Discord accounts."""
    from memory.api.MCP.servers.teams import _discord_add_role, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=True,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=((333, "testuser"),),
        github_usernames=(),
    )

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.add_role_member", return_value={"success": True}),
    ):
        result = await _discord_add_role(team_info, person_info)

    assert result["success"] is True
    assert "testuser" in result["users_added"]


@pytest.mark.asyncio
async def test_discord_add_role_failure(db_session, qdrant):
    """Test _discord_add_role handles failure gracefully."""
    from memory.api.MCP.servers.teams import _discord_add_role, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=True,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=((333, "failuser"),),
        github_usernames=(),
    )

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.add_role_member", return_value={"error": "Permission denied"}),
    ):
        result = await _discord_add_role(team_info, person_info)

    assert result["success"] is False
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_discord_remove_role_success(db_session, qdrant):
    """Test _discord_remove_role removes role from Discord accounts."""
    from memory.api.MCP.servers.teams import _discord_remove_role, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=True,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=((333, "removeuser"),),
        github_usernames=(),
    )

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.remove_role_member", return_value={"success": True}),
    ):
        result = await _discord_remove_role(team_info, person_info)

    assert result["success"] is True
    assert "removeuser" in result["users_removed"]


@pytest.mark.asyncio
async def test_discord_remove_role_failure(db_session, qdrant):
    """Test _discord_remove_role handles failure gracefully."""
    from memory.api.MCP.servers.teams import _discord_remove_role, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=True,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=((333, "failuser"),),
        github_usernames=(),
    )

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.remove_role_member", return_value={"error": "Permission denied"}),
    ):
        result = await _discord_remove_role(team_info, person_info)

    assert result["success"] is False
    assert len(result["errors"]) == 1


# =============================================================================
# GitHub team sync tests (_github_add_member, _github_remove_member)
# =============================================================================


@pytest.mark.asyncio
async def test_github_add_member_success(db_session, admin_session, qdrant):
    """Test _github_add_member adds user to GitHub team."""
    from memory.api.MCP.servers.teams import _github_add_member, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug="myteam",
        github_team_id=123,
        auto_sync_github=True,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=(),
        github_usernames=("ghuser",),
    )

    mock_add = AsyncMock(return_value={"success": True})
    with patch("memory.api.MCP.servers.teams.add_team_member", mock_add):
        result = await _github_add_member(team_info, person_info)

    assert result["success"] is True
    assert "ghuser" in result["users_added"]


@pytest.mark.asyncio
async def test_github_add_member_missing_team_slug(db_session, qdrant):
    """Test _github_add_member returns error when team_slug is missing."""
    from memory.api.MCP.servers.teams import _github_add_member, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug=None,  # Missing
        github_team_id=123,
        auto_sync_github=True,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=(),
        github_usernames=("ghuser",),
    )

    result = await _github_add_member(team_info, person_info)

    assert result["success"] is False
    assert "missing github_org or github_team_slug" in result["errors"][0]


@pytest.mark.asyncio
async def test_github_remove_member_success(db_session, admin_session, qdrant):
    """Test _github_remove_member removes user from GitHub team."""
    from memory.api.MCP.servers.teams import _github_remove_member, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug="myteam",
        github_team_id=123,
        auto_sync_github=True,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=(),
        github_usernames=("ghuser",),
    )

    mock_remove = AsyncMock(return_value={"success": True})
    with patch("memory.api.MCP.servers.teams.remove_team_member", mock_remove):
        result = await _github_remove_member(team_info, person_info)

    assert result["success"] is True
    assert "ghuser" in result["users_removed"]


@pytest.mark.asyncio
async def test_github_remove_member_failure(db_session, qdrant):
    """Test _github_remove_member handles API failure gracefully."""
    from memory.api.MCP.servers.teams import _github_remove_member, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug="myteam",
        github_team_id=123,
        auto_sync_github=True,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=(),
        github_usernames=("ghuser",),
    )

    mock_remove = AsyncMock(return_value={"error": "Not a team member"})
    with patch("memory.api.MCP.servers.teams.remove_team_member", mock_remove):
        result = await _github_remove_member(team_info, person_info)

    assert result["success"] is False
    assert len(result["errors"]) == 1
    assert "ghuser" in result["errors"][0]


@pytest.mark.asyncio
async def test_github_remove_member_missing_team_slug(db_session, qdrant):
    """Test _github_remove_member returns error when team_slug is missing."""
    from memory.api.MCP.servers.teams import _github_remove_member, TeamSyncInfo, PersonSyncInfo

    team_info = TeamSyncInfo(
        slug="test-team",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug=None,  # Missing
        github_team_id=123,
        auto_sync_github=True,
    )
    person_info = PersonSyncInfo(
        identifier="test_person",
        discord_accounts=(),
        github_usernames=("ghuser",),
    )

    result = await _github_remove_member(team_info, person_info)

    assert result["success"] is False
    assert "missing github_org or github_team_slug" in result["errors"][0]


# =============================================================================
# Error handling tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_discord_role_resolution_failure(db_session, admin_session, qdrant):
    """Test upsert handles Discord role resolution failure gracefully."""
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=999, name="Fail Server")
    db_session.add(server)
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_roles", side_effect=Exception("Discord API Error")),
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            name="Error Team",
            guild=999,
            discord_role="will-fail",
        )

    assert result["success"] is True
    assert any("Discord role resolution failed" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_team_add_member_invalid_role(db_session, admin_session, qdrant):
    """Test team_add_member rejects invalid roles."""
    from memory.api.MCP.servers.teams import team_add_member

    team = Team(name="Role Test", slug="role-test")
    person = Person(identifier="role_person", display_name="Role Person")
    db_session.add_all([team, person])
    db_session.commit()

    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(team_add_member)(
            team="role-test",
            person="role_person",
            role="invalid_role",
        )

    assert "error" in result
    assert "Invalid role" in result["error"]


# =============================================================================
# TeamSyncInfo and PersonSyncInfo dataclass tests
# =============================================================================


def test_team_sync_info_should_sync_discord():
    """Test TeamSyncInfo.should_sync_discord property."""
    from memory.api.MCP.servers.teams import TeamSyncInfo

    # All conditions met
    info = TeamSyncInfo(
        slug="test",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=True,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    assert info.should_sync_discord is True

    # Missing role_id
    info2 = TeamSyncInfo(
        slug="test",
        discord_guild_id=111,
        discord_role_id=None,
        auto_sync_discord=True,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    assert info2.should_sync_discord is False

    # auto_sync disabled
    info3 = TeamSyncInfo(
        slug="test",
        discord_guild_id=111,
        discord_role_id=222,
        auto_sync_discord=False,
        github_org=None,
        github_team_slug=None,
        github_team_id=None,
        auto_sync_github=False,
    )
    assert info3.should_sync_discord is False


def test_team_sync_info_should_sync_github():
    """Test TeamSyncInfo.should_sync_github property."""
    from memory.api.MCP.servers.teams import TeamSyncInfo

    # All conditions met
    info = TeamSyncInfo(
        slug="test",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug="myteam",
        github_team_id=123,
        auto_sync_github=True,
    )
    assert info.should_sync_github is True

    # Missing team_id
    info2 = TeamSyncInfo(
        slug="test",
        discord_guild_id=None,
        discord_role_id=None,
        auto_sync_discord=False,
        github_org="myorg",
        github_team_slug="myteam",
        github_team_id=None,
        auto_sync_github=True,
    )
    assert info2.should_sync_github is False


def test_person_sync_info_from_person(db_session, qdrant):
    """Test PersonSyncInfo.from_person factory method."""
    from memory.api.MCP.servers.teams import PersonSyncInfo
    from memory.common.db.models import DiscordUser
    from memory.common.db.models.sources import GithubUser

    person = Person(identifier="sync_test", display_name="Sync Test")
    db_session.add(person)
    db_session.flush()

    discord_user = DiscordUser(id=111, username="discordname", person_id=person.id)
    github_user = GithubUser(id=222, username="githubname", person_id=person.id)
    db_session.add_all([discord_user, github_user])
    db_session.commit()

    db_session.refresh(person)

    info = PersonSyncInfo.from_person(person)

    assert info.identifier == "sync_test"
    assert (111, "discordname") in info.discord_accounts
    assert "githubname" in info.github_usernames


# =============================================================================
# Async session handling / DetachedInstanceError prevention tests
# =============================================================================


@pytest.mark.asyncio
async def test_sync_from_discord_requeires_team_after_await(db_session, admin_session, qdrant):
    """Test sync_from_discord re-queries team after async operation."""
    from memory.api.MCP.servers.teams import sync_from_discord

    team = Team(name="Requery Test", slug="requery-test")
    db_session.add(team)
    db_session.commit()

    # Clear the team from session to simulate detachment
    db_session.expunge(team)

    mock_members = {"members": [{"id": "111", "username": "user1", "display_name": "User One"}]}

    with (
        patch("memory.api.MCP.servers.teams.resolve_bot_id", return_value=1),
        patch("memory.common.discord.list_role_members", return_value=mock_members),
    ):
        # Should not raise DetachedInstanceError
        result = await sync_from_discord(
            db_session, "requery-test", guild_id=123, role_id=456
        )

    assert result["imported"] == 1


@pytest.mark.asyncio
async def test_sync_from_github_requeires_team_after_await(db_session, admin_session, qdrant):
    """Test sync_from_github re-queries team after async operation."""
    from memory.api.MCP.servers.teams import sync_from_github

    team = Team(name="GH Requery Test", slug="gh-requery-test")
    db_session.add(team)
    db_session.commit()

    # Clear the team from session to simulate detachment
    db_session.expunge(team)

    mock_members = [{"login": "ghuser1"}]
    mock_client = MagicMock()
    mock_client.get_team_members.return_value = mock_members
    mock_token = make_mock_access_token(admin_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        # Should not raise DetachedInstanceError
        result = await sync_from_github(
            db_session, "gh-requery-test", org="myorg", github_team_slug="myteam"
        )

    assert result["imported"] == 1
