"""Tests for Projects MCP tools with access control."""

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
    """Create a regular user with projects scope."""
    user = HumanUser(
        name="Regular User",
        email="regular@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["projects"],  # Projects scope
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
    db_session.add(person1)
    db_session.flush()

    # Link regular_user to person1
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

    # Create projects (standalone projects use negative IDs)
    project_one = Project(id=-1, title="Project One", description="First project", state="open")
    project_two = Project(id=-2, title="Project Two", description="Second project", state="open")
    project_three = Project(id=-3, title="Project Three", description="Third project", state="open")
    project_child = Project(id=-4, title="Child Project", description="Child of project one", state="open", parent_id=-1)
    db_session.add_all([project_one, project_two, project_three, project_child])
    db_session.flush()

    # Assign teams to projects
    db_session.execute(project_teams.insert().values(project_id=-1, team_id=team_alpha.id))
    db_session.execute(project_teams.insert().values(project_id=-2, team_id=team_beta.id))
    db_session.execute(project_teams.insert().values(project_id=-3, team_id=team_gamma.id))
    db_session.execute(project_teams.insert().values(project_id=-4, team_id=team_alpha.id))

    db_session.commit()

    return {
        "person1": person1,
        "team_alpha": team_alpha,
        "team_beta": team_beta,
        "team_gamma": team_gamma,
        "project_one": project_one,
        "project_two": project_two,
        "project_three": project_three,
        "project_child": project_child,
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
# project_list access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,expected_min_count,must_include,must_exclude",
    [
        pytest.param(False, 3, {"Project One", "Project Two", "Child Project"}, {"Project Three"}, id="regular_user_sees_accessible_projects"),
        pytest.param(True, 4, {"Project One", "Project Two", "Project Three", "Child Project"}, set(), id="admin_sees_all_projects"),
    ],
)
async def test_project_list_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, expected_min_count, must_include, must_exclude
):
    """Test that project_list respects access control."""
    from memory.api.MCP.servers.projects import list_all

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(list_all)()

    assert "projects" in result, f"Expected 'projects' in result, got: {result}"
    project_titles = {p["title"] for p in result["projects"]}
    assert must_include <= project_titles, f"Expected {must_include} to be subset of {project_titles}"
    for excluded in must_exclude:
        assert excluded not in project_titles, f"Expected {excluded} to not be in {project_titles}"
    assert result["count"] >= expected_min_count


@pytest.mark.asyncio
async def test_project_list_filter_by_state(db_session, user_session, teams_and_projects):
    """Test filtering projects by state."""
    from memory.api.MCP.servers.projects import list_all

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(list_all)(state="open")

    assert "projects" in result
    # All our test projects are open
    for p in result["projects"]:
        assert p["state"] == "open"


@pytest.mark.asyncio
async def test_project_list_filter_by_parent(db_session, user_session, teams_and_projects):
    """Test filtering projects by parent_id."""
    from memory.api.MCP.servers.projects import list_all

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        # Get root projects only
        result = await get_fn(list_all)(parent_id=0)

    assert "projects" in result
    for p in result["projects"]:
        assert p["parent_id"] is None


@pytest.mark.asyncio
async def test_project_list_unauthenticated(db_session):
    """Unauthenticated requests should return an error."""
    from memory.api.MCP.servers.projects import list_all

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=None),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(list_all)()

    assert "error" in result
    assert "Not authenticated" in result["error"]


# =============================================================================
# project_get access control tests
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
async def test_project_get_access_control(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, project_id, expect_success
):
    """Test that project_get respects access control."""
    from memory.api.MCP.servers.projects import fetch

    session_id = admin_session.id if use_admin else user_session.id
    mock_token = make_mock_access_token(session_id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(fetch)(project_id)

    if expect_success:
        assert "project" in result, f"Expected project in result, got: {result}"
        assert result["project"]["id"] == project_id
    else:
        assert "error" in result
        assert "Project not found" in result["error"]


@pytest.mark.asyncio
async def test_project_get_includes_teams(db_session, user_session, teams_and_projects):
    """project_get should include teams when include_teams=True."""
    from memory.api.MCP.servers.projects import fetch

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(fetch)(-1, include_teams=True)

    assert "project" in result
    assert "teams" in result["project"]
    team_slugs = {t["slug"] for t in result["project"]["teams"]}
    assert "team-alpha" in team_slugs


@pytest.mark.asyncio
async def test_project_get_children_count(db_session, user_session, teams_and_projects):
    """project_get should return children_count."""
    from memory.api.MCP.servers.projects import fetch

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(fetch)(-1)

    assert "project" in result
    assert result["project"]["children_count"] == 1


# =============================================================================
# project_create tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_create_requires_non_empty_team_ids(db_session, user_session, teams_and_projects):
    """project_create should require a non-empty team_ids list."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)("New Project", team_ids=[])

    assert "error" in result
    assert "non-empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_project_create_validates_team_ids(db_session, user_session, teams_and_projects):
    """project_create should validate that team_ids exist."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)("New Project", team_ids=[99999])

    assert "error" in result
    assert "invalid" in result["error"].lower() or "do not exist" in result["error"].lower()


@pytest.mark.asyncio
async def test_project_create_requires_team_access_for_non_admin(
    db_session, user_session, teams_and_projects
):
    """Non-admin users can only create projects with teams they belong to."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(user_session.id)
    team_gamma_id = teams_and_projects["team_gamma"].id

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)("New Project", team_ids=[team_gamma_id])

    assert "error" in result
    assert "access" in result["error"].lower()


@pytest.mark.asyncio
async def test_project_create_success(db_session, user_session, teams_and_projects):
    """Successful project creation with valid team_ids."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id
    team_beta_id = teams_and_projects["team_beta"].id

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            "New Project",
            team_ids=[team_alpha_id, team_beta_id],
            description="Test description",
            state="open",
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert "project" in result
    assert result["project"]["title"] == "New Project"
    assert result["project"]["description"] == "Test description"
    assert len(result["project"]["teams"]) == 2


@pytest.mark.asyncio
async def test_project_create_admin_can_use_any_team(db_session, admin_session, teams_and_projects):
    """Admin can create projects with any team."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(admin_session.id)
    team_gamma_id = teams_and_projects["team_gamma"].id

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(upsert)(
            "Admin Project",
            team_ids=[team_gamma_id],
        )

    assert result.get("success") is True, f"Expected success, got: {result}"


# =============================================================================
# project_update tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_update_access_control(
    db_session, user_session, teams_and_projects
):
    """Test that project_update respects access control."""
    from memory.api.MCP.servers.projects import upsert as project_upsert

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        # Try to update inaccessible project
        result = await get_fn(project_upsert)(title="Updated Title", project_id=-3)

    assert "error" in result
    assert "Project not found" in result["error"]


@pytest.mark.asyncio
async def test_project_update_success(db_session, user_session, teams_and_projects):
    """Successful project update."""
    from memory.api.MCP.servers.projects import upsert as project_upsert

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_upsert)(title="Updated Title", project_id=-1, description="Updated desc")

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["project"]["title"] == "Updated Title"
    assert result["project"]["description"] == "Updated desc"


@pytest.mark.asyncio
async def test_project_update_clear_parent(db_session, user_session, teams_and_projects):
    """project_update with clear_parent=True removes the parent."""
    from memory.api.MCP.servers.projects import upsert as project_upsert

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_upsert)(title="Child Project", project_id=-4, clear_parent=True)

    assert result.get("success") is True
    assert result["project"]["parent_id"] is None


@pytest.mark.asyncio
async def test_project_update_prevents_circular_parent(db_session, user_session, teams_and_projects):
    """project_update should prevent circular parent references."""
    from memory.api.MCP.servers.projects import upsert as project_upsert

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        # Try to make parent (-1) a child of its child (-4)
        result = await get_fn(project_upsert)(title="Project One", project_id=-1, parent_id=-4)

    assert "error" in result
    assert "circular" in result["error"].lower()


# =============================================================================
# project_delete tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_delete_access_control(db_session, user_session, teams_and_projects):
    """Test that project_delete respects access control."""
    from memory.api.MCP.servers.projects import delete

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(delete)(-3)

    assert "error" in result
    assert "Project not found" in result["error"]


@pytest.mark.asyncio
async def test_project_delete_success(db_session, user_session, teams_and_projects):
    """Successful project deletion."""
    from memory.api.MCP.servers.projects import delete

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(delete)(-2)

    assert result.get("success") is True
    assert result["deleted_id"] == -2


# =============================================================================
# project_tree tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_tree_access_control(
    db_session, user_session, admin_session, teams_and_projects
):
    """Test that project_tree respects access control."""
    from memory.api.MCP.servers.projects import list_all as project_list_all

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_list_all)(as_tree=True)

    assert "tree" in result
    # Regular user should not see Project Three
    project_titles = []

    def collect_titles(nodes):
        for node in nodes:
            project_titles.append(node["title"])
            collect_titles(node.get("children", []))

    collect_titles(result["tree"])
    assert "Project Three" not in project_titles
    assert "Project One" in project_titles
    assert "Child Project" in project_titles


@pytest.mark.asyncio
async def test_project_tree_nesting(db_session, user_session, teams_and_projects):
    """Test that project_tree correctly nests children."""
    from memory.api.MCP.servers.projects import list_all as project_list_all

    mock_token = make_mock_access_token(user_session.id)

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        result = await get_fn(project_list_all)(as_tree=True)

    assert "tree" in result
    # Find Project One and verify Child Project is nested under it
    project_one = next((p for p in result["tree"] if p["title"] == "Project One"), None)
    assert project_one is not None, "Project One should be in the tree"
    assert len(project_one["children"]) == 1
    assert project_one["children"][0]["title"] == "Child Project"
