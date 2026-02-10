"""Tests for Projects MCP tools with access control."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import uuid

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


# =============================================================================
# Repo-level project tests (new functionality)
# =============================================================================


@pytest.fixture
def github_user(db_session):
    """Create a user specifically for GitHub tests to avoid conflicts."""
    unique_id = uuid.uuid4().hex[:8]
    user = HumanUser(
        name="GitHub Test User",
        email=f"github-test-{unique_id}@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["*"],
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def github_user_session(db_session, github_user):
    """Create a session for the GitHub test user."""
    unique_id = uuid.uuid4().hex[:8]
    session = UserSession(
        id=f"github-test-session-{unique_id}",
        user_id=github_user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def github_account(db_session, github_user):
    """Create a GitHub account for testing repo-level projects."""
    from memory.common.db.models.sources import GithubAccount

    account = GithubAccount(
        user_id=github_user.id,
        name="Test GitHub Account",
        auth_type="pat",
        active=True,
    )
    account.access_token = "ghp_test_token_12345"  # Uses setter to encrypt
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def github_repo(db_session, github_account):
    """Create a GithubRepo for testing."""
    from memory.common.db.models.sources import GithubRepo

    repo = GithubRepo(
        account_id=github_account.id,
        github_id=12345,
        owner="testorg",
        name="testrepo",
        track_issues=True,
        track_prs=True,
        active=True,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.mark.asyncio
async def test_repo_project_create_with_existing_repo(
    db_session, github_user_session, teams_and_projects, github_repo
):
    """Creating a repo-level project with an existing tracked repo."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(github_user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id

    mock_client = MagicMock()

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, github_repo)

        result = await get_fn(upsert)(
            repo="testorg/testrepo",
            team_ids=[team_alpha_id],
            description="Repo-level project",
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["created"] is True
    assert result["project"]["title"] == "testrepo"  # Defaults to repo name
    assert result["project"]["repo_path"] == "testorg/testrepo"
    assert result["project"]["number"] is None  # No milestone


@pytest.mark.asyncio
async def test_repo_project_create_with_title_override(
    db_session, github_user_session, teams_and_projects, github_repo
):
    """Creating a repo-level project with a custom title."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(github_user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id

    mock_client = MagicMock()

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, github_repo)

        result = await get_fn(upsert)(
            title="Custom Product Name",
            repo="testorg/testrepo",
            team_ids=[team_alpha_id],
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["project"]["title"] == "Custom Product Name"


@pytest.mark.asyncio
async def test_repo_project_requires_create_repo_flag_for_untracked(
    db_session, github_user_session, teams_and_projects, github_account
):
    """Repo must exist or create_repo=True must be set."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(github_user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id

    mock_client = MagicMock()
    # Repo doesn't exist on GitHub either
    mock_client.fetch_repository_info.return_value = None

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, None)  # No tracked repo in DB

        result = await get_fn(upsert)(
            repo="neworg/newrepo",
            team_ids=[team_alpha_id],
            create_repo=False,  # Don't create
        )

    assert "error" in result
    assert "not found" in result["error"].lower()
    assert "create_repo=True" in result["error"]


@pytest.mark.asyncio
async def test_repo_project_with_create_repo_flag(
    db_session, github_user_session, teams_and_projects, github_account
):
    """Creating a repo-level project with create_repo=True creates the repo on GitHub."""
    from memory.api.MCP.servers.projects import upsert
    from memory.common.db.models.sources import GithubRepo

    mock_token = make_mock_access_token(github_user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id

    mock_client = MagicMock()
    # Repo doesn't exist initially, then gets created via ensure_repository
    mock_client.fetch_repository_info.return_value = None
    mock_client.ensure_repository.return_value = (
        {"github_id": 99999, "owner": "neworg", "name": "newrepo"},
        True,  # was_created
    )

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, None)  # No tracked repo initially

        result = await get_fn(upsert)(
            repo="neworg/newrepo",
            team_ids=[team_alpha_id],
            create_repo=True,
            private=True,
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["github_repo_created"] is True
    assert result["tracking_created"] is True

    # Verify the repo was actually created in the database
    created_repo = db_session.query(GithubRepo).filter_by(owner="neworg", name="newrepo").first()
    assert created_repo is not None
    assert created_repo.github_id == 99999


@pytest.mark.asyncio
async def test_repo_project_idempotent_update(
    db_session, github_user_session, teams_and_projects, github_repo
):
    """Calling upsert on existing repo-level project updates it."""
    from memory.api.MCP.servers.projects import upsert
    from memory.common.db.models.sources import Project, project_teams

    mock_token = make_mock_access_token(github_user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id
    team_beta_id = teams_and_projects["team_beta"].id

    # Create an existing repo-level project
    existing_project = Project(
        id=-100,
        repo_id=github_repo.id,
        github_id=github_repo.github_id,
        number=None,  # Repo-level, no milestone
        title="testrepo",
        state="open",
    )
    db_session.add(existing_project)
    db_session.flush()  # Ensure project exists before adding team relationship
    db_session.execute(project_teams.insert().values(project_id=-100, team_id=team_alpha_id))
    db_session.commit()

    mock_client = MagicMock()

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, github_repo)

        # Upsert should find existing and update teams
        # Note: description update is not allowed for GitHub-backed projects
        result = await get_fn(upsert)(
            repo="testorg/testrepo",
            team_ids=[team_alpha_id, team_beta_id],
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["created"] is False  # Found existing
    assert result["project"]["id"] == -100
    assert len(result["project"]["teams"]) == 2


# =============================================================================
# Milestone project with create_repo tests
# =============================================================================


@pytest.mark.asyncio
async def test_milestone_project_with_create_repo(
    db_session, github_user_session, teams_and_projects, github_account
):
    """Creating milestone project with create_repo=True creates repo first."""
    from memory.api.MCP.servers.projects import upsert
    from memory.common.db.models.sources import GithubRepo

    mock_token = make_mock_access_token(github_user_session.id)
    team_alpha_id = teams_and_projects["team_alpha"].id

    mock_client = MagicMock()
    # Repo doesn't exist initially, then gets created via ensure_repository
    mock_client.fetch_repository_info.return_value = None
    mock_client.ensure_repository.return_value = (
        {"github_id": 88888, "owner": "neworg", "name": "newrepo"},
        True,  # was_created
    )
    mock_client.ensure_milestone.return_value = (
        {
            "number": 1,
            "title": "v1.0",
            "description": None,
            "github_id": 111111,
            "state": "open",
            "due_on": None,
        },
        True,  # was_created
    )

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, None)  # No tracked repo

        result = await get_fn(upsert)(
            repo="neworg/newrepo",
            milestone="v1.0",
            team_ids=[team_alpha_id],
            create_repo=True,
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["github_repo_created"] is True
    assert result["milestone_created"] is True
    assert result["project"]["title"] == "v1.0"

    # Verify repo was created in database
    created_repo = db_session.query(GithubRepo).filter_by(owner="neworg", name="newrepo").first()
    assert created_repo is not None
    assert created_repo.github_id == 88888


# =============================================================================
# ensure_github_repo helper tests
# =============================================================================


def test_ensure_github_repo_finds_existing(db_session, github_account, github_repo):
    """ensure_github_repo returns existing tracking entry."""
    from memory.api.MCP.servers.github_helpers import ensure_github_repo

    mock_client = MagicMock()

    repo_obj, github_created, tracking_created = ensure_github_repo(
        db_session,
        mock_client,
        github_account.id,
        "testorg",
        "testrepo",
    )

    assert repo_obj is not None
    assert repo_obj.id == github_repo.id
    assert github_created is False
    assert tracking_created is False
    # Client should not have been called
    mock_client.fetch_repository_info.assert_not_called()


def test_ensure_github_repo_creates_tracking_for_existing_github_repo(
    db_session, github_account
):
    """ensure_github_repo creates tracking entry for repo that exists on GitHub."""
    from memory.api.MCP.servers.github_helpers import ensure_github_repo

    mock_client = MagicMock()
    mock_client.fetch_repository_info.return_value = {
        "github_id": 77777,
        "name": "existingrepo",
        "owner": "testorg",
        "description": "Existing repo",
    }

    repo_obj, github_created, tracking_created = ensure_github_repo(
        db_session,
        mock_client,
        github_account.id,
        "testorg",
        "existingrepo",
    )

    assert repo_obj is not None
    assert repo_obj.owner == "testorg"
    assert repo_obj.name == "existingrepo"
    assert github_created is False  # Already existed on GitHub
    assert tracking_created is True  # New tracking entry


def test_ensure_github_repo_returns_none_when_not_found_and_no_create(
    db_session, github_account
):
    """ensure_github_repo returns None if repo doesn't exist and create_if_missing=False."""
    from memory.api.MCP.servers.github_helpers import ensure_github_repo

    mock_client = MagicMock()
    mock_client.fetch_repository_info.return_value = None  # Not found

    repo_obj, github_created, tracking_created = ensure_github_repo(
        db_session,
        mock_client,
        github_account.id,
        "testorg",
        "nonexistent",
        create_if_missing=False,
    )

    assert repo_obj is None
    assert github_created is False
    assert tracking_created is False


def test_ensure_github_repo_creates_repo_when_missing(db_session, github_account):
    """ensure_github_repo creates repo on GitHub when create_if_missing=True."""
    from memory.api.MCP.servers.github_helpers import ensure_github_repo

    mock_client = MagicMock()
    mock_client.fetch_repository_info.return_value = None  # Not found initially
    mock_client.ensure_repository.return_value = (
        {
            "github_id": 55555,
            "name": "newrepo",
            "owner": "testorg",
            "description": "New repo",
        },
        True,  # was_created
    )

    repo_obj, github_created, tracking_created = ensure_github_repo(
        db_session,
        mock_client,
        github_account.id,
        "testorg",
        "newrepo",
        description="New repo",
        create_if_missing=True,
        private=True,
    )

    assert repo_obj is not None
    assert repo_obj.owner == "testorg"
    assert repo_obj.name == "newrepo"
    assert github_created is True
    assert tracking_created is True
    mock_client.ensure_repository.assert_called_once_with(
        "testorg", "newrepo", description="New repo", private=True
    )


# =============================================================================
# GithubClient repository methods tests
# =============================================================================


def test_github_client_fetch_repository_info():
    """Test GithubClient.fetch_repository_info."""
    from memory.common.github import GithubClient, GithubCredentials

    mock_response = {
        "repository": {
            "id": "R_123",
            "databaseId": 12345,
            "name": "testrepo",
            "owner": {"login": "testorg"},
            "description": "Test repo",
            "isPrivate": True,
            "isFork": False,
            "isArchived": False,
            "defaultBranchRef": {"name": "main"},
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-02T00:00:00Z",
        }
    }

    with patch.object(GithubClient, "_graphql", return_value=(mock_response, None)):
        credentials = GithubCredentials(auth_type="pat", access_token="fake")
        client = GithubClient(credentials)
        result = client.fetch_repository_info("testorg", "testrepo")

    assert result is not None
    assert result["github_id"] == 12345
    assert result["name"] == "testrepo"
    assert result["owner"] == "testorg"
    assert result["is_private"] is True


def test_github_client_fetch_repository_info_not_found():
    """Test GithubClient.fetch_repository_info when repo doesn't exist."""
    from memory.common.github import GithubClient, GithubCredentials

    with patch.object(GithubClient, "_graphql", return_value=({"repository": None}, None)):
        credentials = GithubCredentials(auth_type="pat", access_token="fake")
        client = GithubClient(credentials)
        result = client.fetch_repository_info("testorg", "nonexistent")

    assert result is None


def test_github_client_create_repository():
    """Test GithubClient.create_repository."""
    from memory.common.github import GithubClient, GithubCredentials
    from unittest.mock import Mock

    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {
        "id": 12345,
        "node_id": "R_123",
        "name": "newrepo",
        "owner": {"login": "testorg"},
        "description": "New repo",
        "private": True,
        "default_branch": "main",
        "html_url": "https://github.com/testorg/newrepo",
    }

    credentials = GithubCredentials(auth_type="pat", access_token="fake")
    client = GithubClient(credentials)

    with patch.object(client.session, "post", return_value=mock_response):
        result = client.create_repository(
            name="newrepo",
            description="New repo",
            private=True,
            org="testorg",
        )

    assert result is not None
    assert result["github_id"] == 12345
    assert result["name"] == "newrepo"
    assert result["owner"] == "testorg"
    assert result["is_private"] is True


def test_github_client_ensure_repository_finds_existing():
    """Test GithubClient.ensure_repository when repo exists."""
    from memory.common.github import GithubClient, GithubCredentials

    mock_repo_info = {
        "github_id": 12345,
        "name": "testrepo",
        "owner": "testorg",
    }

    credentials = GithubCredentials(auth_type="pat", access_token="fake")
    client = GithubClient(credentials)

    with patch.object(client, "fetch_repository_info", return_value=mock_repo_info):
        result, was_created = client.ensure_repository("testorg", "testrepo")

    assert result == mock_repo_info
    assert was_created is False


def test_github_client_ensure_repository_creates_when_missing():
    """Test GithubClient.ensure_repository creates repo when missing."""
    from memory.common.github import GithubClient, GithubCredentials

    mock_created_repo = {
        "github_id": 99999,
        "name": "newrepo",
        "owner": "testorg",
    }

    credentials = GithubCredentials(auth_type="pat", access_token="fake")
    client = GithubClient(credentials)

    with (
        patch.object(client, "fetch_repository_info", return_value=None),
        patch.object(client, "create_repository", return_value=mock_created_repo),
    ):
        result, was_created = client.ensure_repository(
            "testorg", "newrepo", description="New", private=True
        )

    assert result == mock_created_repo
    assert was_created is True


# =============================================================================
# TeamsMixin team-repo methods tests
# =============================================================================


def test_github_client_get_repo_teams():
    """Test GithubClient.get_repo_teams fetches teams with repo access."""
    from memory.common.github import GithubClient, GithubCredentials

    teams_data = [
        {
            "id": 1001,
            "node_id": "T_1001",
            "slug": "engineering",
            "name": "Engineering",
            "description": "The eng team",
            "permission": "push",
            "privacy": "closed",
        },
        {
            "id": 1002,
            "node_id": "T_1002",
            "slug": "devops",
            "name": "DevOps",
            "description": "Infrastructure team",
            "permission": "admin",
            "privacy": "secret",
        },
    ]

    credentials = GithubCredentials(auth_type="pat", access_token="fake")
    client = GithubClient(credentials)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = teams_data
    mock_response.headers = {"X-RateLimit-Remaining": "100"}

    with patch.object(client.session, "get", return_value=mock_response) as mock_get:
        result = client.get_repo_teams("testorg", "testrepo")

    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert "repos/testorg/testrepo/teams" in call_args[0][0]
    assert len(result) == 2
    assert result[0]["slug"] == "engineering"
    assert result[0]["permission"] == "push"
    assert result[1]["slug"] == "devops"
    assert result[1]["permission"] == "admin"


def test_github_client_add_team_to_repo():
    """Test GithubClient.add_team_to_repo grants team access."""
    from memory.common.github import GithubClient, GithubCredentials

    credentials = GithubCredentials(auth_type="pat", access_token="fake")
    client = GithubClient(credentials)

    mock_response = MagicMock()
    mock_response.status_code = 204  # Success response for PUT
    mock_response.headers = {"X-RateLimit-Remaining": "100"}

    with patch.object(client.session, "put", return_value=mock_response) as mock_put:
        result = client.add_team_to_repo(
            org="testorg",
            team_slug="engineering",
            owner="testorg",
            repo="testrepo",
            permission="push",
        )

    assert result is True
    mock_put.assert_called_once()
    call_args = mock_put.call_args
    assert "orgs/testorg/teams/engineering/repos/testorg/testrepo" in call_args[0][0]
    assert call_args[1]["json"] == {"permission": "push"}


def test_github_client_remove_team_from_repo():
    """Test GithubClient.remove_team_from_repo revokes team access."""
    from memory.common.github import GithubClient, GithubCredentials

    credentials = GithubCredentials(auth_type="pat", access_token="fake")
    client = GithubClient(credentials)

    mock_response = MagicMock()
    mock_response.status_code = 204  # Success response for DELETE
    mock_response.headers = {"X-RateLimit-Remaining": "100"}

    with patch.object(client.session, "delete", return_value=mock_response) as mock_delete:
        result = client.remove_team_from_repo(
            org="testorg",
            team_slug="engineering",
            owner="testorg",
            repo="testrepo",
        )

    assert result is True
    mock_delete.assert_called_once()
    call_args = mock_delete.call_args
    assert "orgs/testorg/teams/engineering/repos/testorg/testrepo" in call_args[0][0]


# =============================================================================
# sync_repo_teams_outbound tests
# =============================================================================


def test_sync_repo_teams_outbound_grants_access(db_session):
    """Test sync_repo_teams_outbound grants repo access to teams with GitHub integration."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_outbound

    # Create teams with GitHub integration
    team_with_github = Team(
        name="Engineering",
        slug="engineering-sync-test",
        github_team_id=1001,
        github_team_slug="engineering",
        github_org="testorg",
    )
    team_without_github = Team(
        name="Marketing",
        slug="marketing-sync-test",
        # No GitHub integration
    )
    db_session.add_all([team_with_github, team_without_github])
    db_session.commit()

    mock_client = MagicMock()
    mock_client.add_team_to_repo.return_value = True

    result = sync_repo_teams_outbound(
        client=mock_client,
        repo_owner="testorg",
        repo_name="testrepo",
        teams=[team_with_github, team_without_github],
        permission="push",
    )

    assert "engineering" in result["synced"]
    assert "Marketing" in result["skipped"]  # No GitHub integration
    assert result["failed"] == []

    mock_client.add_team_to_repo.assert_called_once_with(
        org="testorg",
        team_slug="engineering",
        owner="testorg",
        repo="testrepo",
        permission="push",
    )


def test_sync_repo_teams_outbound_skips_different_org(db_session):
    """Test sync_repo_teams_outbound skips teams from different org."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_outbound

    team_different_org = Team(
        name="Other Org Team",
        slug="other-team-sync-test",
        github_team_id=2001,
        github_team_slug="other-team",
        github_org="differentorg",  # Different from repo owner
    )
    db_session.add(team_different_org)
    db_session.commit()

    mock_client = MagicMock()

    result = sync_repo_teams_outbound(
        client=mock_client,
        repo_owner="testorg",  # Different org
        repo_name="testrepo",
        teams=[team_different_org],
    )

    assert "Other Org Team" in result["skipped"]
    assert result["synced"] == []
    mock_client.add_team_to_repo.assert_not_called()


def test_sync_repo_teams_outbound_handles_failures(db_session):
    """Test sync_repo_teams_outbound tracks failed syncs."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_outbound

    team = Team(
        name="Engineering",
        slug="engineering-fail-test",
        github_team_id=1001,
        github_team_slug="engineering",
        github_org="testorg",
    )
    db_session.add(team)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.add_team_to_repo.return_value = False  # Simulate failure

    result = sync_repo_teams_outbound(
        client=mock_client,
        repo_owner="testorg",
        repo_name="testrepo",
        teams=[team],
    )

    assert result["synced"] == []
    assert "engineering" in result["failed"]


# =============================================================================
# sync_repo_teams_inbound tests
# =============================================================================


def test_sync_repo_teams_inbound_finds_matching_teams(db_session):
    """Test sync_repo_teams_inbound returns teams matching GitHub teams with repo access."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_inbound

    # Use a unique github_team_id to avoid collisions with other tests
    unique_github_id = 90001

    # Create a team with matching github_team_id
    team = Team(
        name="Engineering Inbound",
        slug="engineering-inbound-test",
        github_team_id=unique_github_id,
        github_team_slug="engineering",
        github_org="testorg",
    )
    db_session.add(team)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.get_repo_teams.return_value = [
        {"id": unique_github_id, "slug": "engineering", "permission": "push"},
        {"id": 99999, "slug": "untracked-team", "permission": "admin"},  # No matching Team
    ]

    result = sync_repo_teams_inbound(
        session=db_session,
        client=mock_client,
        repo_owner="testorg",
        repo_name="testrepo",
    )

    assert len(result) == 1
    assert result[0].slug == "engineering-inbound-test"
    assert result[0].github_team_id == unique_github_id


def test_sync_repo_teams_inbound_returns_empty_for_no_teams(db_session):
    """Test sync_repo_teams_inbound returns empty list when repo has no teams."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_inbound

    mock_client = MagicMock()
    mock_client.get_repo_teams.return_value = []

    result = sync_repo_teams_inbound(
        session=db_session,
        client=mock_client,
        repo_owner="testorg",
        repo_name="testrepo",
    )

    assert result == []


def test_sync_repo_teams_inbound_returns_empty_for_no_matches(db_session):
    """Test sync_repo_teams_inbound returns empty when no Team records match."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_inbound

    mock_client = MagicMock()
    mock_client.get_repo_teams.return_value = [
        {"id": 9999, "slug": "untracked-team", "permission": "push"},
    ]

    result = sync_repo_teams_inbound(
        session=db_session,
        client=mock_client,
        repo_owner="testorg",
        repo_name="testrepo",
    )

    assert result == []


def test_sync_repo_teams_inbound_handles_exception(db_session):
    """Test sync_repo_teams_inbound handles GitHub API exceptions gracefully."""
    from memory.api.MCP.servers.github_helpers import sync_repo_teams_inbound

    mock_client = MagicMock()
    mock_client.get_repo_teams.side_effect = Exception("API error")

    result = sync_repo_teams_inbound(
        session=db_session,
        client=mock_client,
        repo_owner="testorg",
        repo_name="testrepo",
    )

    assert result == []
    mock_client.get_repo_teams.assert_called_once_with("testorg", "testrepo")


# =============================================================================
# Project upsert with team sync integration tests
# =============================================================================


@pytest.mark.asyncio
async def test_repo_project_outbound_sync_on_create(
    db_session, github_user_session, github_repo
):
    """Test that creating a repo project syncs teams to GitHub."""
    from memory.api.MCP.servers.projects import upsert

    # Create a team with GitHub integration
    team = Team(
        name="Engineering",
        slug="engineering-outbound-create",
        github_team_id=1001,
        github_team_slug="engineering",
        github_org="testorg",
    )
    db_session.add(team)
    db_session.commit()

    mock_token = make_mock_access_token(github_user_session.id)
    mock_client = MagicMock()
    mock_client.add_team_to_repo.return_value = True
    mock_client.get_repo_teams.return_value = []  # No existing teams on repo

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, github_repo)

        result = await get_fn(upsert)(
            repo="testorg/testrepo",
            team_ids=[team.id],
        )

    assert result.get("success") is True
    assert "github_team_sync" in result
    assert "engineering" in result["github_team_sync"]["synced"]

    mock_client.add_team_to_repo.assert_called_once_with(
        org="testorg",
        team_slug="engineering",
        owner="testorg",
        repo="testrepo",
        permission="push",
    )


@pytest.mark.asyncio
async def test_repo_project_inbound_sync_on_create(
    db_session, github_user_session, github_repo
):
    """Test that creating a repo project for existing repo adds GitHub teams to project."""
    from memory.api.MCP.servers.projects import upsert

    # Create a team that exists in our DB and has repo access on GitHub
    existing_team = Team(
        name="DevOps",
        slug="devops-inbound-create",
        github_team_id=2001,
        github_team_slug="devops",
        github_org="testorg",
    )
    # Create another team to add via team_ids
    new_team = Team(
        name="Engineering",
        slug="engineering-inbound-create",
    )
    db_session.add_all([existing_team, new_team])
    db_session.commit()

    mock_token = make_mock_access_token(github_user_session.id)
    mock_client = MagicMock()
    mock_client.add_team_to_repo.return_value = True
    # Simulate that DevOps team already has access on GitHub
    mock_client.get_repo_teams.return_value = [
        {"id": 2001, "slug": "devops", "permission": "admin"},
    ]

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, github_repo)

        result = await get_fn(upsert)(
            repo="testorg/testrepo",
            team_ids=[new_team.id],  # Only specify new_team
        )

    assert result.get("success") is True
    # DevOps should be added via inbound sync
    assert "teams_from_github" in result
    assert "DevOps" in result["teams_from_github"]

    # Project should have both teams
    project_team_ids = [t["id"] for t in result["project"]["teams"]]
    assert existing_team.id in project_team_ids
    assert new_team.id in project_team_ids

    # Verify get_repo_teams was called with correct parameters
    mock_client.get_repo_teams.assert_called_once_with("testorg", "testrepo")


@pytest.mark.asyncio
async def test_update_project_outbound_sync_for_new_teams(
    db_session, github_user_session, github_repo
):
    """Test that updating project teams syncs newly added teams to GitHub."""
    from memory.api.MCP.servers.projects import upsert
    from memory.common.db.models.sources import project_teams

    # Create teams
    team_alpha = Team(
        name="Alpha",
        slug="alpha-update-sync",
        github_team_id=1001,
        github_team_slug="alpha",
        github_org="testorg",
    )
    team_beta = Team(
        name="Beta",
        slug="beta-update-sync",
        github_team_id=1002,
        github_team_slug="beta",
        github_org="testorg",
    )
    db_session.add_all([team_alpha, team_beta])
    db_session.flush()

    # Create existing project with only team_alpha
    existing_project = Project(
        id=-200,
        repo_id=github_repo.id,
        github_id=github_repo.github_id,
        number=None,
        title="testrepo",
        state="open",
    )
    db_session.add(existing_project)
    db_session.flush()
    db_session.execute(project_teams.insert().values(project_id=-200, team_id=team_alpha.id))
    db_session.commit()

    mock_token = make_mock_access_token(github_user_session.id)
    mock_client = MagicMock()
    mock_client.add_team_to_repo.return_value = True

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.make_session") as mock_make_session,
        patch("memory.api.MCP.servers.projects.get_github_client") as mock_get_client,
    ):
        mock_make_session.return_value.__enter__.return_value = db_session
        mock_get_client.return_value = (mock_client, github_repo)

        # Update to add team_beta
        result = await get_fn(upsert)(
            project_id=-200,
            team_ids=[team_alpha.id, team_beta.id],
        )

    assert result.get("success") is True
    # Only team_beta should be synced (team_alpha was already there)
    assert "github_team_sync" in result
    assert "beta" in result["github_team_sync"]["synced"]
    assert "alpha" not in result["github_team_sync"]["synced"]

    # Verify only one call was made (for team_beta)
    mock_client.add_team_to_repo.assert_called_once_with(
        org="testorg",
        team_slug="beta",
        owner="testorg",
        repo="testrepo",
        permission="push",
    )


# =============================================================================
# GitHub milestone due_on sync tests
# =============================================================================


@pytest.fixture
def github_milestone_project(db_session):
    """Create a GitHub-backed milestone project for testing due_on sync.

    This fixture creates its own isolated user, account, repo, and session to avoid
    test isolation issues with shared fixtures (ObjectDeletedError when running
    tests in sequence).
    """
    from memory.common.db.models.sources import GithubAccount, GithubRepo, project_teams

    unique_id = uuid.uuid4().hex[:8]

    # Create isolated user for this fixture
    user = HumanUser(
        name="Milestone Test User",
        email=f"milestone-test-{unique_id}@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["*"],
    )
    db_session.add(user)
    db_session.flush()

    # Create isolated session for this fixture
    session = UserSession(
        id=f"milestone-test-session-{unique_id}",
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.flush()

    # Create isolated GitHub account
    account = GithubAccount(
        user_id=user.id,
        name="Milestone Test GitHub Account",
        auth_type="pat",
        active=True,
    )
    account.access_token = "ghp_milestone_test_token"
    db_session.add(account)
    db_session.flush()

    # Create isolated GitHub repo
    repo = GithubRepo(
        account_id=account.id,
        github_id=99999,
        owner="testorg",
        name="testrepo",
        track_issues=True,
        track_prs=True,
        active=True,
    )
    db_session.add(repo)
    db_session.flush()

    # Create a team for the project
    team = Team(
        name="Milestone Team",
        slug=f"milestone-team-{unique_id}",
        description="Team for milestone project",
        is_active=True,
    )
    db_session.add(team)
    db_session.flush()

    # Create milestone project (uses positive ID to mimic GitHub milestone)
    project = Project(
        id=uuid.uuid4().int & ((1 << 62) - 1),  # Positive ID for GitHub-backed
        repo_id=repo.id,
        github_id=111,
        number=5,  # Milestone number on GitHub
        title="v1.0 Release",
        description="First major release",
        state="open",
    )
    db_session.add(project)
    db_session.flush()

    # Assign team to project
    db_session.execute(
        project_teams.insert().values(
            project_id=project.id,
            team_id=team.id,
        )
    )
    db_session.commit()

    # Attach session_id to the project so tests can use it for auth
    project._test_session_id = session.id  # type: ignore[attr-defined]
    project._test_repo = repo  # type: ignore[attr-defined]

    return project


@pytest.mark.asyncio
async def test_update_github_project_due_on_syncs_to_milestone(
    db_session, github_milestone_project
):
    """Updating due_on on a GitHub-backed milestone project syncs to GitHub."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(github_milestone_project._test_session_id)
    due_date = "2026-06-15T12:00:00+00:00"

    mock_github_client = MagicMock()
    mock_github_client.update_milestone.return_value = {"number": 5, "due_on": due_date}

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.GithubClient") as mock_client_class,
    ):
        mock_client_class.return_value = mock_github_client

        result = await get_fn(upsert)(
            project_id=github_milestone_project.id,
            due_on=due_date,
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["project"]["due_on"] == "2026-06-15T12:00:00+00:00"

    # Verify update_milestone was called with correct args
    mock_github_client.update_milestone.assert_called_once_with(
        owner="testorg",
        repo="testrepo",
        milestone_number=5,
        due_on="2026-06-15T12:00:00Z",
    )


@pytest.mark.asyncio
async def test_update_github_project_clear_due_on_syncs_to_milestone(
    db_session, github_milestone_project
):
    """Clearing due_on on a GitHub-backed milestone project syncs null to GitHub."""
    from datetime import timezone as tz
    from memory.api.MCP.servers.projects import upsert

    # Set initial due date on the project
    github_milestone_project.due_on = datetime(2026, 3, 1, tzinfo=tz.utc)
    db_session.commit()

    mock_token = make_mock_access_token(github_milestone_project._test_session_id)

    mock_github_client = MagicMock()
    mock_github_client.update_milestone.return_value = {"number": 5, "due_on": None}

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.GithubClient") as mock_client_class,
    ):
        mock_client_class.return_value = mock_github_client

        result = await get_fn(upsert)(
            project_id=github_milestone_project.id,
            clear_due_on=True,
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result["project"]["due_on"] is None

    # Verify update_milestone was called with None to clear due_on
    mock_github_client.update_milestone.assert_called_once_with(
        owner="testorg",
        repo="testrepo",
        milestone_number=5,
        due_on=None,
    )


@pytest.mark.asyncio
async def test_update_github_project_due_on_fails_gracefully(
    db_session, github_milestone_project
):
    """When GitHub API fails to update milestone, the operation returns an error."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(github_milestone_project._test_session_id)
    due_date = "2026-06-15T12:00:00+00:00"

    mock_github_client = MagicMock()
    mock_github_client.update_milestone.return_value = None  # Simulate failure

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.GithubClient") as mock_client_class,
    ):
        mock_client_class.return_value = mock_github_client

        result = await get_fn(upsert)(
            project_id=github_milestone_project.id,
            due_on=due_date,
        )

    assert "error" in result
    assert "Failed to update GitHub milestone" in result["error"]


@pytest.mark.asyncio
async def test_update_github_project_owner_does_not_call_github(
    db_session, github_milestone_project, teams_and_projects
):
    """Updating owner on a GitHub-backed project does NOT call GitHub API."""
    from memory.api.MCP.servers.projects import upsert

    mock_token = make_mock_access_token(github_milestone_project._test_session_id)
    person = teams_and_projects["person1"]

    mock_github_client = MagicMock()

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.GithubClient") as mock_client_class,
    ):
        mock_client_class.return_value = mock_github_client

        result = await get_fn(upsert)(
            project_id=github_milestone_project.id,
            owner_id=person.id,
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    # Verify update_milestone was NOT called (owner is local-only)
    mock_github_client.update_milestone.assert_not_called()


@pytest.mark.asyncio
async def test_update_github_project_same_due_on_does_not_call_github(
    db_session, github_milestone_project
):
    """Setting due_on to its current value does NOT call GitHub API."""
    from datetime import timezone as tz
    from memory.api.MCP.servers.projects import upsert

    # Set initial due date on the project
    github_milestone_project.due_on = datetime(2026, 6, 15, 12, 0, 0, tzinfo=tz.utc)
    db_session.commit()

    mock_token = make_mock_access_token(github_milestone_project._test_session_id)

    mock_github_client = MagicMock()

    with (
        patch("memory.api.MCP.access.get_access_token", return_value=mock_token),
        patch("memory.api.MCP.servers.projects.GithubClient") as mock_client_class,
    ):
        mock_client_class.return_value = mock_github_client

        result = await get_fn(upsert)(
            project_id=github_milestone_project.id,
            due_on="2026-06-15T12:00:00+00:00",
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    # Verify update_milestone was NOT called (no change in due_on)
    mock_github_client.update_milestone.assert_not_called()
