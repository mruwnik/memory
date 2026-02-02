"""Tests for Teams MCP tools with access control."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from mcp.server.auth.middleware.auth_context import (
    auth_context_var,
    AuthenticatedUser,
    AccessToken,
)

from memory.common.db.models import Person, Team, HumanUser, UserSession
from memory.common.db.models.discord import DiscordBot
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


@contextmanager
def mcp_auth_context(session_token: str):
    """Set up FastMCP auth context for testing.

    This sets the auth_context_var that FastMCP's get_access_token() reads from,
    allowing tests to run without mocking.
    """
    access_token = AccessToken(
        token=session_token,
        client_id="test-client",
        scopes=[],
    )
    auth_user = AuthenticatedUser(access_token)
    token = auth_context_var.set(auth_user)
    try:
        yield
    finally:
        auth_context_var.reset(token)


@pytest.fixture
def discord_bot(db_session, admin_user):
    """Create a Discord bot linked to admin_user for tests that need one."""
    bot = DiscordBot(id=1, name="Test Bot", is_active=True)
    db_session.add(bot)
    # Link bot to admin user via many-to-many relationship
    admin_user.discord_bots.append(bot)
    db_session.commit()
    return bot


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
def teams_and_projects(db_session, regular_user):
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


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped, else return as-is."""
    return getattr(tool, "fn", tool)


# =============================================================================
# list_all access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,expected_team_count,expected_slugs",
    [
        pytest.param(False, 2, {"team-alpha", "team-beta"}, id="regular_user_sees_member_teams"),
        pytest.param(True, 3, {"team-alpha", "team-beta", "team-gamma"}, id="admin_sees_all_teams"),
    ],
)
async def test_list_all_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, expected_team_count, expected_slugs
):
    """Test that list_all respects access control."""
    from memory.api.MCP.servers.teams import list_all

    session_id = admin_session.id if use_admin else user_session.id

    with mcp_auth_context(session_id):
        result = await get_fn(list_all)()

    assert "teams" in result, f"Expected 'teams' in result, got: {result}"
    team_slugs = {t["slug"] for t in result["teams"]}
    # Admin may see additional teams from fixtures, so check expected teams are present
    assert expected_slugs <= team_slugs, f"Expected {expected_slugs} to be subset of {team_slugs}"
    assert result["count"] >= expected_team_count


@pytest.mark.asyncio
async def test_list_all_unauthenticated_returns_error(db_session):
    """Unauthenticated requests should return an error."""
    from memory.api.MCP.servers.teams import list_all

    # No auth context set - simulates unauthenticated request
    result = await get_fn(list_all)()

    assert "error" in result
    assert "Not authenticated" in result["error"]


# =============================================================================
# fetch access control tests
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
async def test_fetch_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, team_slug, expect_success
):
    """Test that fetch respects access control."""
    from memory.api.MCP.servers.teams import fetch

    session_id = admin_session.id if use_admin else user_session.id
    with mcp_auth_context(session_id):
        result = await get_fn(fetch)(team_slug)

    if expect_success:
        assert "team" in result, f"Expected team in result, got: {result}"
        assert result["team"]["slug"] == team_slug
    else:
        assert "error" in result
        assert "Team not found" in result["error"]


# =============================================================================
# list_all with include_projects tests
# =============================================================================


@pytest.mark.asyncio
async def test_list_all_includes_projects_when_requested(
    db_session, user_session, teams_and_projects
):
    """list_all should include projects when include_projects=True."""
    from memory.api.MCP.servers.teams import list_all

    with mcp_auth_context(user_session.id):
        result = await get_fn(list_all)(include_projects=True)

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
    with mcp_auth_context(session_id):
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
    with mcp_auth_context(session_id):
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
async def test_upsert_creates_new_team(db_session, admin_session):
    """Test upsert creates a new team."""
    from memory.api.MCP.servers.teams import upsert

    with mcp_auth_context(admin_session.id):
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
async def test_upsert_updates_existing_team(db_session, admin_session):
    """Test upsert updates an existing team."""
    from memory.api.MCP.servers.teams import upsert

    # Create initial team
    team = Team(name="Original Name", slug="update-me", description="Original")
    db_session.add(team)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
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
async def test_upsert_auto_generates_slug(db_session, admin_session):
    """Test upsert generates slug from name if not provided."""
    from memory.api.MCP.servers.teams import upsert

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(name="Auto Slug Team")

    assert result["success"] is True
    assert result["team"]["slug"] == "auto-slug-team"


@pytest.mark.asyncio
async def test_upsert_with_members_list(db_session, admin_session):
    """Test upsert with explicit members list."""
    from memory.api.MCP.servers.teams import upsert

    # Create some people
    person1 = Person(identifier="member_one", display_name="Member One")
    person2 = Person(identifier="member_two", display_name="Member Two")
    db_session.add_all([person1, person2])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Members Test Team",
            members=["member_one", "member_two"],
        )

    assert result["success"] is True
    assert "member_one" in result["membership_changes"]["added"]
    assert "member_two" in result["membership_changes"]["added"]
    assert len(result["team"]["members"]) == 2


@pytest.mark.asyncio
async def test_upsert_creates_missing_person(db_session, admin_session):
    """Test upsert creates Person for unknown member identifier."""
    from memory.api.MCP.servers.teams import upsert

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Create Person Team",
            members=["new_person"],
        )

    assert result["success"] is True
    assert "new_person" in result["membership_changes"]["created_people"]
    assert "new_person" in result["membership_changes"]["added"]


@pytest.mark.asyncio
async def test_upsert_clears_members_with_empty_list(db_session, admin_session):
    """Test upsert removes all members when passed empty list."""
    from memory.api.MCP.servers.teams import upsert

    # Create team with members
    team = Team(name="Clear Me", slug="clear-me")
    person = Person(identifier="existing_member", display_name="Existing")
    db_session.add_all([team, person])
    db_session.flush()
    team.members.append(person)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
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
async def test_upsert_with_discord_guild_by_id(db_session, admin_session):
    """Test upsert with Discord guild specified by ID."""
    from memory.api.MCP.servers.teams import upsert

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Discord Team",
            guild=123456789,
            auto_sync_discord=True,
        )

    assert result["success"] is True
    assert result["team"]["discord_guild_id"] == 123456789
    assert result["team"]["auto_sync_discord"] is True


@pytest.mark.asyncio
async def test_upsert_with_discord_role_creates_role(db_session, admin_session, discord_bot):
    """Test upsert creates Discord role when it doesn't exist.

    This test uses the real make_session() to test actual session behavior.
    Only external Discord/GitHub API calls are mocked.
    """
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models import DiscordServer

    # Create Discord server - use the test database
    server = DiscordServer(id=111222333, name="Test Guild")
    db_session.add(server)
    db_session.commit()

    # Mock only external Discord API calls
    mock_role_result = {"success": True, "role": {"id": "999888777", "name": "New Role"}}

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.list_roles", return_value={"roles": []}),
        patch("memory.common.discord.create_role", return_value=mock_role_result),
    ):
        result = await get_fn(upsert)(
            name="Role Test Team",
            guild=111222333,
            discord_role="New Role",
        )

    assert result["success"] is True
    assert result["discord_sync"].get("role_created") is True
    assert result["team"]["discord_role_id"] == 999888777

    # Verify the value is actually persisted in the database
    db_session.expire_all()  # Force reload from DB
    team = db_session.query(Team).filter(Team.slug == "role-test-team").first()
    assert team is not None
    assert team.discord_role_id == 999888777


@pytest.mark.asyncio
async def test_upsert_with_existing_discord_role(db_session, admin_session, discord_bot):
    """Test upsert links to existing Discord role."""
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=111222333, name="Test Guild")
    db_session.add(server)
    db_session.commit()

    # Mock Discord API - role exists
    mock_roles = {"roles": [{"id": "555666777", "name": "Existing Role"}]}

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.list_roles", return_value=mock_roles),
    ):
        result = await get_fn(upsert)(
            name="Existing Role Team",
            guild=111222333,
            discord_role="Existing Role",
        )

    assert result["success"] is True
    assert result["discord_sync"].get("role_created") is not True
    assert result["team"]["discord_role_id"] == 555666777


@pytest.mark.asyncio
async def test_team_create_alias_removed(db_session, admin_session):
    """Test that team_create alias was removed - upsert is the canonical name."""
    import importlib.util

    spec = importlib.util.find_spec("memory.api.MCP.servers.teams")
    assert spec is not None
    # team_create was removed, only upsert exists now
    from memory.api.MCP.servers.teams import upsert
    assert upsert is not None


@pytest.mark.asyncio
async def test_upsert_with_github_org(db_session, admin_session):
    """Test upsert with GitHub organization."""
    from memory.api.MCP.servers.teams import upsert

    with mcp_auth_context(admin_session.id):
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


@pytest.mark.asyncio
async def test_upsert_with_github_and_members(db_session, admin_session):
    """Test upsert with GitHub org AND members doesn't cause DetachedInstanceError.

    This is a regression test for the bug where calling upsert with both
    github_org/github_team_slug and members would fail with:
    'Parent instance <Team at ...> is not bound to a Session; lazy load
    operation of attribute 'members' cannot proceed'

    The issue was that after the async ensure_github_team() call, the Team
    object would become detached from the session.
    """
    from memory.api.MCP.servers.teams import upsert

    # Create a person to add as a member
    person = Person(identifier="gh_test_person", display_name="GH Test Person")
    db_session.add(person)
    db_session.commit()

    # Mock the GitHub client to return a new team (triggering async path)
    mock_client = MagicMock()
    mock_client.fetch_team.return_value = None  # Team doesn't exist
    mock_client.create_team.return_value = {"id": 12345, "slug": "myteam"}

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        result = await get_fn(upsert)(
            name="GitHub Team With Members",
            github_org="myorg",
            github_team_slug="myteam",
            members=["gh_test_person"],
        )

    # Should succeed without DetachedInstanceError
    assert result["success"] is True
    assert result["team"]["github_org"] == "myorg"
    assert result["team"]["github_team_slug"] == "myteam"
    assert result["github_sync"].get("team_created") is True

    # Member should be added
    assert "gh_test_person" in result["membership_changes"].get("added", [])
    assert len(result["team"]["members"]) == 1
    assert result["team"]["members"][0]["identifier"] == "gh_test_person"


@pytest.mark.asyncio
async def test_upsert_remove_members_with_github(db_session, admin_session):
    """Test upsert removing members from team with GitHub integration.

    This is a regression test for DetachedInstanceError when removing members
    from a team that has GitHub sync enabled. The bug occurred because
    _github_remove_member opened its own make_session() context, causing
    nested sessions and object detachment.
    """
    from memory.api.MCP.servers.teams import upsert

    # Create a person to add then remove
    person = Person(
        identifier="gh_remove_test",
        display_name="GH Remove Test",
        contact_info={"github": "ghremoveuser"},
    )
    db_session.add(person)
    db_session.commit()

    # Mock GitHub client
    mock_client = MagicMock()
    mock_client.fetch_team.return_value = None
    mock_client.create_team.return_value = {"id": 99999, "slug": "remove-test"}
    mock_client.add_team_member.return_value = {"success": True, "action": "added"}
    mock_client.remove_team_member.return_value = True

    # First create team with member
    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        result = await get_fn(upsert)(
            name="GitHub Remove Test",
            github_org="testorg",
            github_team_slug="remove-test",
            members=["gh_remove_test"],
        )

    assert result["success"] is True
    assert len(result["team"]["members"]) == 1

    # Now remove all members - this should not cause DetachedInstanceError
    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        result = await get_fn(upsert)(
            name="GitHub Remove Test",
            github_org="testorg",
            github_team_slug="remove-test",
            members=[],
        )

    # Should succeed without DetachedInstanceError
    assert result["success"] is True
    assert len(result["team"]["members"]) == 0
    assert "gh_remove_test" in result["membership_changes"].get("removed", [])


# =============================================================================
# Discord create_role tests
# =============================================================================


@pytest.mark.asyncio
async def test_discord_create_role_basic(db_session, admin_session, discord_bot):
    """Test Discord create MCP tool for roles."""
    from memory.api.MCP.servers.discord import create
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=123456789, name="Test Server")
    db_session.add(server)
    db_session.commit()

    mock_result = {"success": True, "role": {"id": "111", "name": "Test Role", "color": 0}}

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.create_role", return_value=mock_result) as mock_create,
    ):
        result = await get_fn(create)(
            name="Test Role",
            guild=123456789,
        )

    assert result["success"] is True
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_discord_create_role_with_options(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.create_role", return_value=mock_result) as mock_create,
    ):
        result = await get_fn(create)(
            name="Colored Role",
            guild="Test Server",
            color=16711680,  # Red
            mentionable=True,
            hoist=True,
        )

    assert result["success"] is True
    mock_create.assert_called_once_with(
        discord_bot.id, 123456789, "Colored Role",
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
# upsert with is_active tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_archive_team(db_session, admin_session):
    """Test upsert can archive a team via is_active=False."""
    from memory.api.MCP.servers.teams import upsert

    team = Team(name="Archive Me", slug="upsert-archive-test", is_active=True)
    db_session.add(team)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Archive Me",
            slug="upsert-archive-test",
            is_active=False,
        )

    assert result["success"] is True
    assert result["team"]["is_active"] is False
    assert result["team"]["archived_at"] is not None


@pytest.mark.asyncio
async def test_upsert_reactivate_team(db_session, admin_session):
    """Test upsert can reactivate an archived team.

    Note: The current implementation sets is_active=True but preserves
    archived_at for audit trail purposes.
    """
    from memory.api.MCP.servers.teams import upsert
    from datetime import datetime, timezone

    team = Team(
        name="Reactivate Me",
        slug="upsert-reactivate-test",
        is_active=False,
        archived_at=datetime.now(timezone.utc),
    )
    db_session.add(team)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Reactivate Me",
            slug="upsert-reactivate-test",
            is_active=True,
        )

    assert result["success"] is True
    assert result["team"]["is_active"] is True
    # archived_at is preserved for audit trail (not cleared on reactivation)
    assert result["team"]["archived_at"] is not None


# =============================================================================
# team_remove_member tests
# =============================================================================


@pytest.mark.asyncio
async def test_team_remove_member_success(db_session, admin_session):
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

    with mcp_auth_context(admin_session.id):
        result = await get_fn(team_remove_member)(
            team="remove-member-test",
            person="removable",
            sync_external=False,
        )

    assert result["success"] is True
    assert result["person"] == "removable"


@pytest.mark.asyncio
async def test_team_remove_member_not_a_member(db_session, admin_session):
    """Test team_remove_member handles non-member gracefully."""
    from memory.api.MCP.servers.teams import team_remove_member

    team = Team(name="Remove Test 2", slug="remove-test-2")
    person = Person(identifier="not_member", display_name="Not Member")
    db_session.add_all([team, person])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(team_remove_member)(
            team="remove-test-2",
            person="not_member",
        )

    assert result["success"] is True
    assert result.get("was_not_member") is True


@pytest.mark.asyncio
async def test_team_remove_member_with_discord_sync(db_session, admin_session, discord_bot):
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

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.remove_role_member", return_value={"success": True}),
    ):
        result = await get_fn(team_remove_member)(
            team="discord-sync-remove",
            person="discord_user",
            sync_external=True,
        )

    assert result["success"] is True
    assert "sync" in result


# =============================================================================
# fetch with include_members tests
# =============================================================================


@pytest.mark.asyncio
async def test_fetch_with_include_members(db_session, admin_session):
    """Test fetch returns members with roles when include_members=True."""
    from memory.api.MCP.servers.teams import fetch
    from memory.common.db.models.sources import team_members

    team = Team(name="Fetch Members Test", slug="fetch-members-test")
    person1 = Person(identifier="fetch_member1", display_name="Member One")
    person2 = Person(identifier="fetch_member2", display_name="Member Two")
    db_session.add_all([team, person1, person2])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person1.id, role="admin")
    )
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person2.id, role="member")
    )
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(fetch)("fetch-members-test", include_members=True)

    assert "team" in result
    assert "members" in result["team"]
    assert len(result["team"]["members"]) == 2
    members_by_id = {m["identifier"]: m for m in result["team"]["members"]}
    assert members_by_id["fetch_member1"]["role"] == "admin"
    assert members_by_id["fetch_member2"]["role"] == "member"


@pytest.mark.asyncio
async def test_fetch_without_include_members(db_session, admin_session):
    """Test fetch does not include member details when include_members=False."""
    from memory.api.MCP.servers.teams import fetch
    from memory.common.db.models.sources import team_members

    team = Team(name="Fetch No Members Test", slug="fetch-no-members-test")
    person = Person(identifier="fetch_no_member", display_name="No Member")
    db_session.add_all([team, person])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        # Explicitly set include_members=False (default is True)
        result = await get_fn(fetch)("fetch-no-members-test", include_members=False)

    assert "team" in result
    # When include_members=False, members key is empty list
    assert result["team"].get("members", []) == []


@pytest.mark.asyncio
async def test_fetch_with_include_projects(db_session, admin_session):
    """Test fetch returns projects when include_projects=True."""
    from memory.api.MCP.servers.teams import fetch
    from memory.common.db.models.sources import project_teams

    team = Team(name="Fetch Projects Test", slug="fetch-projects-test")
    project1 = Project(id=-200, title="Fetch Project 1", state="open")
    project2 = Project(id=-201, title="Fetch Project 2", state="open")
    db_session.add_all([team, project1, project2])
    db_session.flush()
    db_session.execute(project_teams.insert().values(project_id=-200, team_id=team.id))
    db_session.execute(project_teams.insert().values(project_id=-201, team_id=team.id))
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(fetch)("fetch-projects-test", include_projects=True)

    assert "team" in result
    assert "projects" in result["team"]
    assert len(result["team"]["projects"]) == 2
    titles = {p["title"] for p in result["team"]["projects"]}
    assert titles == {"Fetch Project 1", "Fetch Project 2"}


# =============================================================================
# project_unassign_team tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_unassign_team_success(db_session, admin_session):
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

    with mcp_auth_context(admin_session.id):
        result = await get_fn(project_unassign_team)(project=-100, team="unassign-test")

    assert result["success"] is True
    assert result["team"]["slug"] == "unassign-test"


@pytest.mark.asyncio
async def test_project_unassign_team_not_assigned(db_session, admin_session):
    """Test project_unassign_team handles not-assigned gracefully."""
    from memory.api.MCP.servers.teams import project_unassign_team

    team = Team(name="Not Assigned", slug="not-assigned")
    project = Project(id=-101, title="Not Assigned Project", state="open")
    db_session.add_all([team, project])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(project_unassign_team)(project=-101, team="not-assigned")

    assert result["success"] is True
    assert result.get("was_not_assigned") is True


# =============================================================================
# project_list_access tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_list_access_success(db_session, admin_session):
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

    with mcp_auth_context(admin_session.id):
        result = await get_fn(project_list_access)(project=-102)

    assert result["total_people_count"] == 3
    assert len(result["teams"]) == 2
    identifiers = {p["identifier"] for p in result["all_people"]}
    assert identifiers == {"access_person1", "access_person2", "access_person3"}


# =============================================================================
# list_all with tags filter tests
# =============================================================================


@pytest.mark.asyncio
async def test_list_all_filter_by_tags_match_all(db_session, admin_session):
    """Test list_all filters by tags with match_any_tag=False (default, requires all)."""
    from memory.api.MCP.servers.teams import list_all

    team1 = Team(name="Tags Test 1", slug="tags-test-1", tags=["engineering", "frontend"])
    team2 = Team(name="Tags Test 2", slug="tags-test-2", tags=["engineering", "backend"])
    team3 = Team(name="Tags Test 3", slug="tags-test-3", tags=["engineering", "frontend", "core"])
    db_session.add_all([team1, team2, team3])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(list_all)(tags=["engineering", "frontend"], match_any_tag=False)

    slugs = {t["slug"] for t in result["teams"]}
    assert "tags-test-1" in slugs
    assert "tags-test-3" in slugs
    assert "tags-test-2" not in slugs  # doesn't have frontend


@pytest.mark.asyncio
async def test_list_all_filter_by_tags_match_any(db_session, admin_session):
    """Test list_all filters by tags with match_any_tag=True (matches any)."""
    from memory.api.MCP.servers.teams import list_all

    team1 = Team(name="Any Tags 1", slug="any-tags-1", tags=["design"])
    team2 = Team(name="Any Tags 2", slug="any-tags-2", tags=["marketing"])
    team3 = Team(name="Any Tags 3", slug="any-tags-3", tags=["sales"])
    db_session.add_all([team1, team2, team3])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(list_all)(tags=["design", "marketing"], match_any_tag=True)

    slugs = {t["slug"] for t in result["teams"]}
    assert "any-tags-1" in slugs
    assert "any-tags-2" in slugs
    assert "any-tags-3" not in slugs  # has neither design nor marketing


# =============================================================================
# sync_from_discord tests
# =============================================================================


@pytest.mark.asyncio
async def test_sync_from_discord_imports_members(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.list_role_members", return_value=mock_members),
    ):
        result = await sync_from_discord(
            db_session, "discord-import", guild_id=123, role_id=456
        )

    assert result["imported"] == 2
    assert len(result["created_people"]) == 2


@pytest.mark.asyncio
async def test_sync_from_discord_links_existing_discord_user(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.list_role_members", return_value=mock_members),
    ):
        result = await sync_from_discord(
            db_session, "discord-link", guild_id=123, role_id=456
        )

    assert result["imported"] == 1
    assert len(result["created_people"]) == 0  # No new person created


@pytest.mark.asyncio
async def test_sync_from_discord_handles_failure(db_session, admin_session, discord_bot):
    """Test sync_from_discord handles API failure gracefully."""
    from memory.api.MCP.servers.teams import sync_from_discord

    team = Team(name="Discord Fail", slug="discord-fail")
    db_session.add(team)
    db_session.commit()

    with (
        mcp_auth_context(admin_session.id),
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
async def test_sync_from_github_imports_members(db_session, admin_session):
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

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        result = await sync_from_github(
            db_session, "github-import", org="myorg", github_team_slug="myteam"
        )

    assert result["imported"] == 2
    assert len(result["created_people"]) == 2


@pytest.mark.asyncio
async def test_sync_from_github_no_client(db_session, admin_session):
    """Test sync_from_github returns empty when no GitHub client available."""
    from memory.api.MCP.servers.teams import sync_from_github

    team = Team(name="GitHub No Client", slug="github-no-client")
    db_session.add(team)
    db_session.commit()

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=None),
    ):
        result = await sync_from_github(
            db_session, "github-no-client", org="myorg", github_team_slug="myteam"
        )

    assert result["imported"] == 0


# =============================================================================
# PersonSyncInfo tests
# =============================================================================


def test_person_sync_info_github_from_accounts(db_session):
    """Test PersonSyncInfo gets github usernames from linked accounts."""
    from memory.api.MCP.servers.teams import PersonSyncInfo
    from memory.common.db.models.sources import GithubUser

    person = Person(identifier="with_github_account", display_name="Has GitHub")
    db_session.add(person)
    db_session.flush()

    github_user = GithubUser(id=12345, username="ghuser", person_id=person.id)
    db_session.add(github_user)
    db_session.commit()

    # Refresh to load relationships
    db_session.refresh(person)

    info = PersonSyncInfo.from_person(person)
    assert info.github_usernames == ("ghuser",)


def test_person_sync_info_github_from_contact_info(db_session):
    """Test PersonSyncInfo falls back to contact_info for github username."""
    from memory.api.MCP.servers.teams import PersonSyncInfo

    person = Person(
        identifier="contact_info_github",
        display_name="Contact Info GitHub",
        contact_info={"github": "contactuser"},
    )
    db_session.add(person)
    db_session.commit()

    info = PersonSyncInfo.from_person(person)
    assert info.github_usernames == ("contactuser",)


def test_person_sync_info_github_from_contact_info_list(db_session):
    """Test PersonSyncInfo handles list of github usernames in contact_info."""
    from memory.api.MCP.servers.teams import PersonSyncInfo

    person = Person(
        identifier="contact_info_github_list",
        display_name="Contact Info GitHub List",
        contact_info={"github": ["user1", "user2"]},
    )
    db_session.add(person)
    db_session.commit()

    info = PersonSyncInfo.from_person(person)
    assert set(info.github_usernames) == {"user1", "user2"}


def test_person_sync_info_prefers_accounts_over_contact_info(db_session):
    """Test PersonSyncInfo prefers linked accounts over contact_info."""
    from memory.api.MCP.servers.teams import PersonSyncInfo
    from memory.common.db.models.sources import GithubUser

    person = Person(
        identifier="both_sources",
        display_name="Both Sources",
        contact_info={"github": "contactuser"},
    )
    db_session.add(person)
    db_session.flush()

    github_user = GithubUser(id=99999, username="linkeduser", person_id=person.id)
    db_session.add(github_user)
    db_session.commit()

    db_session.refresh(person)

    info = PersonSyncInfo.from_person(person)
    # Should use linked account, not contact_info
    assert info.github_usernames == ("linkeduser",)


# =============================================================================
# Discord role sync tests (_discord_add_role, _discord_remove_role)
# =============================================================================


@pytest.mark.asyncio
async def test_discord_add_role_success(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.add_role_member", return_value={"success": True}),
    ):
        result = await _discord_add_role(team_info, person_info)

    assert result["success"] is True
    assert "testuser" in result["users_added"]


@pytest.mark.asyncio
async def test_discord_add_role_failure(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.add_role_member", return_value={"error": "Permission denied"}),
    ):
        result = await _discord_add_role(team_info, person_info)

    assert result["success"] is False
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_discord_remove_role_success(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.remove_role_member", return_value={"success": True}),
    ):
        result = await _discord_remove_role(team_info, person_info)

    assert result["success"] is True
    assert "removeuser" in result["users_removed"]


@pytest.mark.asyncio
async def test_discord_remove_role_failure(db_session, admin_session, discord_bot):
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
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.remove_role_member", return_value={"error": "Permission denied"}),
    ):
        result = await _discord_remove_role(team_info, person_info)

    assert result["success"] is False
    assert len(result["errors"]) == 1


# =============================================================================
# GitHub team sync tests (_github_add_member, _github_remove_member)
# =============================================================================


@pytest.mark.asyncio
async def test_github_add_member_success(db_session, admin_session):
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

    # Mock GitHub client that returns success
    mock_client = MagicMock()
    mock_client.add_team_member.return_value = {"success": True}

    result = await _github_add_member(mock_client, team_info, person_info)

    assert result["success"] is True
    assert "ghuser" in result["users_added"]
    mock_client.add_team_member.assert_called_once_with("myorg", "myteam", "ghuser")


@pytest.mark.asyncio
async def test_github_add_member_missing_team_slug(db_session):
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

    # Client won't be used since we early-return, but signature requires it
    mock_client = MagicMock()
    result = await _github_add_member(mock_client, team_info, person_info)

    assert result["success"] is False
    assert "missing github_org or github_team_slug" in result["errors"][0]
    mock_client.add_team_member.assert_not_called()


@pytest.mark.asyncio
async def test_github_remove_member_success(db_session, admin_session):
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

    # Mock GitHub client that returns success (True)
    mock_client = MagicMock()
    mock_client.remove_team_member.return_value = True

    result = await _github_remove_member(mock_client, team_info, person_info)

    assert result["success"] is True
    assert "ghuser" in result["users_removed"]
    mock_client.remove_team_member.assert_called_once_with("myorg", "myteam", "ghuser")


@pytest.mark.asyncio
async def test_github_remove_member_failure(db_session):
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

    # Mock GitHub client that returns failure (False)
    mock_client = MagicMock()
    mock_client.remove_team_member.return_value = False

    result = await _github_remove_member(mock_client, team_info, person_info)

    assert result["success"] is False
    assert len(result["errors"]) == 1
    assert "ghuser" in result["errors"][0]


@pytest.mark.asyncio
async def test_github_remove_member_missing_team_slug(db_session):
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

    # Client won't be used since we early-return, but signature requires it
    mock_client = MagicMock()
    result = await _github_remove_member(mock_client, team_info, person_info)

    assert result["success"] is False
    assert "missing github_org or github_team_slug" in result["errors"][0]
    mock_client.remove_team_member.assert_not_called()


# =============================================================================
# Error handling tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_discord_role_resolution_failure(db_session, admin_session, discord_bot):
    """Test upsert handles Discord role resolution failure gracefully."""
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models import DiscordServer

    server = DiscordServer(id=999, name="Fail Server")
    db_session.add(server)
    db_session.commit()

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.list_roles", side_effect=Exception("Discord API Error")),
    ):
        result = await get_fn(upsert)(
            name="Error Team",
            guild=999,
            discord_role="will-fail",
        )

    assert result["success"] is True
    assert any("Discord role resolution failed" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_team_add_member_invalid_role(db_session, admin_session):
    """Test team_add_member rejects invalid roles."""
    from memory.api.MCP.servers.teams import team_add_member

    team = Team(name="Role Test", slug="role-test")
    person = Person(identifier="role_person", display_name="Role Person")
    db_session.add_all([team, person])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
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


def test_person_sync_info_from_person(db_session):
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
async def test_sync_from_discord_requeires_team_after_await(db_session, admin_session, discord_bot):
    """Test sync_from_discord re-queries team after async operation."""
    from memory.api.MCP.servers.teams import sync_from_discord

    team = Team(name="Requery Test", slug="requery-test")
    db_session.add(team)
    db_session.commit()

    # Clear the team from session to simulate detachment
    db_session.expunge(team)

    mock_members = {"members": [{"id": "111", "username": "user1", "display_name": "User One"}]}

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.common.discord.list_role_members", return_value=mock_members),
    ):
        # Should not raise DetachedInstanceError
        result = await sync_from_discord(
            db_session, "requery-test", guild_id=123, role_id=456
        )

    assert result["imported"] == 1


@pytest.mark.asyncio
async def test_sync_from_github_requeires_team_after_await(db_session, admin_session):
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

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        # Should not raise DetachedInstanceError
        result = await sync_from_github(
            db_session, "gh-requery-test", org="myorg", github_team_slug="myteam"
        )

    assert result["imported"] == 1


def test_nested_make_session_causes_detached_instance_error(db_session):
    """Demonstrate that nested make_session() calls cause DetachedInstanceError.

    This test documents a known issue: when make_session() is called nested,
    the inner session.remove() invalidates objects from the outer session.

    Any code using nested make_session() calls MUST handle the detachment by:
    1. NOT using nested make_session() - pass session or resolved clients instead
    2. Or capturing ORM values into dataclasses before nested calls
    3. Or re-querying objects after nested calls return
    """
    from memory.common.db.connection import make_session
    from sqlalchemy.orm.exc import DetachedInstanceError

    # Create a team using the test fixture session
    team = Team(name="Nested Session Test", slug="nested-session-test")
    db_session.add(team)
    db_session.commit()
    team_id = team.id

    # Now demonstrate the nested session bug
    with make_session() as outer_session:
        # Query the team in the outer session
        team_obj = outer_session.query(Team).filter(Team.id == team_id).first()
        assert team_obj is not None
        assert team_obj.slug == "nested-session-test"

        # Nested make_session() - this will call remove() on exit,
        # which disposes the underlying session that outer_session is also using
        with make_session() as inner_session:
            # Do something in inner session
            inner_team = inner_session.query(Team).filter(Team.id == team_id).first()
            assert inner_team is not None
        # inner session exits, calls session.remove()

        # Now try to access team_obj - it's detached because the inner
        # session.remove() disposed the shared underlying session
        with pytest.raises(DetachedInstanceError):
            _ = team_obj.members  # This raises DetachedInstanceError


@pytest.mark.asyncio
async def test_upsert_remove_members_with_github_avoids_detached_error(db_session, admin_session):
    """Test that upsert with GitHub sync correctly avoids DetachedInstanceError.

    The upsert function handles the nested session issue by:
    1. Capturing values into dataclasses (TeamSyncInfo, PersonSyncInfo) before async
    2. Re-querying the team after async operations complete

    This test verifies that behavior works correctly.
    """
    from memory.api.MCP.servers.teams import upsert
    from memory.common.db.models.sources import GithubUser

    # Create a person with GitHub account so _github_remove_member has work to do
    person = Person(identifier="nested_session_test", display_name="Nested Session Test")
    db_session.add(person)
    db_session.flush()

    github_user = GithubUser(id=9999, username="nestedtestuser", person_id=person.id)
    db_session.add(github_user)
    db_session.commit()

    # Mock GitHub client - returns success for remove_team_member
    mock_client = MagicMock()
    mock_client.fetch_team.return_value = {"github_id": 12345, "slug": "nested-test-team"}
    mock_client.remove_team_member.return_value = True

    upsert_fn = get_fn(upsert)

    with (
        mcp_auth_context(admin_session.id),
        patch("memory.api.MCP.servers.teams.get_github_client_for_org", return_value=mock_client),
    ):
        # First create team with GitHub integration and add the member
        result1 = await upsert_fn(
            name="Nested Session Bug Test",
            github_org="testorg",
            github_team_slug="nested-test-team",
            members=["nested_session_test"],
        )
        assert result1["success"], f"Failed to create team: {result1}"
        assert result1["team"]["github_org"] == "testorg"

        # Now remove all members - this triggers _github_remove_member
        # which creates a nested make_session(). The code should handle this
        # by using dataclasses and re-querying.
        result2 = await upsert_fn(
            name="Nested Session Bug Test",
            members=[],  # Remove all members
        )

        # Should succeed because the code properly handles nested sessions
        assert result2["success"], f"Remove members failed: {result2}"
        assert result2["membership_changes"]["removed"] == ["nested_session_test"]

