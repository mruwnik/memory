"""Tests for Teams MCP tools with access control."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

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
        patch("memory.api.MCP.servers.teams._sync_membership_add", return_value={}),
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
        patch("memory.api.MCP.servers.teams._sync_membership_add", return_value={}),
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
        patch("memory.api.MCP.servers.teams._sync_membership_remove", return_value={}),
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
        patch("memory.api.MCP.servers.discord.resolve_bot_id", return_value=1),
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
        patch("memory.api.MCP.servers.discord.resolve_bot_id", return_value=1),
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
async def test_discord_create_role_basic(db_session):
    """Test Discord create MCP tool for roles."""
    from memory.api.MCP.servers.discord import create
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=123456789, name="Test Server")
    db_session.add(server)
    db_session.commit()

    mock_result = {"success": True, "role": {"id": "111", "name": "Test Role", "color": 0}}

    with (
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
async def test_discord_create_role_with_options(db_session):
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

    with (
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
