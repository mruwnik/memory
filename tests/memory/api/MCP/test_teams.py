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
