"""Tests for Teams MCP tools with access control."""

from datetime import datetime, timedelta

import pytest

from memory.common.db.models import (
    Person,
    Team,
    HumanUser,
    UserSession,
)
from memory.common.db.models.discord import DiscordServer
from memory.common.db.models.sources import Project, team_members, project_teams
from memory.common.db import connection as db_connection
from memory.common import discord as discord_client
from memory.api.MCP.servers.teams import (
    fetch,
    list_all,
    make_slug,
    project_list_access,
    resolve_person,
    set_team_members,
    team_add_member,
    team_remove_member,
    upsert,
    upsert_team_record,
)
from tests.conftest import mcp_auth_context


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

    # No auth context set - simulates unauthenticated request
    result = await get_fn(list_all)()

    assert "error" in result
    assert "Not authenticated" in result["error"]


# =============================================================================
# fetch access control tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_admin,team_slug",
    [
        pytest.param(False, "team-alpha", id="regular_user_can_access_member_team"),
        pytest.param(True, "team-gamma", id="admin_can_access_any_team"),
    ],
)
async def test_fetch_access_control_success(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, team_slug
):
    """Test that fetch grants access to authorized teams."""

    session_id = admin_session.id if use_admin else user_session.id
    with mcp_auth_context(session_id):
        result = await get_fn(fetch)(team_slug)

    assert "team" in result, f"Expected team in result, got: {result}"
    assert result["team"]["slug"] == team_slug


@pytest.mark.asyncio
async def test_fetch_access_control_denied(
    db_session, user_session, teams_and_projects
):
    """Test that fetch denies access to teams the user is not a member of."""
    with mcp_auth_context(user_session.id):
        result = await get_fn(fetch)("team-gamma")

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
    "use_admin,team_slug",
    [
        # regular_user is `lead` of team-beta — leads can add members.
        pytest.param(False, "team-beta", id="regular_user_lead_can_add_to_team"),
        pytest.param(True, "team-gamma", id="admin_can_add_to_any_team"),
    ],
)
async def test_team_add_member_access_control_success(
    db_session, user_session, admin_session, teams_and_projects,
    use_admin, team_slug
):
    """Test that team_add_member grants access for authorized users."""

    session_id = admin_session.id if use_admin else user_session.id
    with mcp_auth_context(session_id):
        result = await get_fn(team_add_member)(team_slug, "person2")

    assert result.get("success") is True or result.get("already_member") is True


@pytest.mark.asyncio
async def test_team_add_member_access_control_denied(
    db_session, user_session, teams_and_projects
):
    """Test that team_add_member denies access for non-members."""
    with mcp_auth_context(user_session.id):
        result = await get_fn(team_add_member)("team-gamma", "person2")

    assert "error" in result
    assert "Team not found" in result["error"]


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

    assert make_slug(name) == expected_slug


def test_resolve_guild_with_int(db_session):
    """Test resolve_guild returns int directly."""

    assert discord_client.resolve_guild(123456789, db_session) == 123456789


def test_resolve_guild_with_numeric_string(db_session):
    """Test resolve_guild parses numeric string as ID."""

    assert discord_client.resolve_guild("123456789", db_session) == 123456789


def test_resolve_guild_with_name(db_session):
    """Test resolve_guild looks up server by name."""

    # Create a Discord server
    server = DiscordServer(id=987654321, name="Test Server")
    db_session.add(server)
    db_session.commit()

    assert discord_client.resolve_guild("Test Server", db_session) == 987654321


def test_resolve_guild_not_found(db_session):
    """Test resolve_guild raises for unknown server name."""

    with pytest.raises(ValueError, match="Discord server 'Unknown Server' not found"):
        discord_client.resolve_guild("Unknown Server", db_session)


def test_resolve_guild_none(db_session):
    """Test resolve_guild returns None for None input."""

    assert discord_client.resolve_guild(None, db_session) is None


# =============================================================================
# resolve_person() tests
# =============================================================================


def test_resolve_person_by_id(db_session):
    """resolve_person finds person by numeric ID."""
    person = Person(identifier="by_id", display_name="By ID")
    db_session.add(person)
    db_session.flush()

    assert resolve_person(db_session, person.id) == person
    assert resolve_person(db_session, str(person.id)) == person


def test_resolve_person_by_identifier(db_session):
    """resolve_person finds person by exact identifier slug."""
    person = Person(identifier="exact_slug", display_name="Exact Slug")
    db_session.add(person)
    db_session.flush()

    assert resolve_person(db_session, "exact_slug") == person


def test_resolve_person_by_display_name(db_session):
    """resolve_person finds person by display_name (case-insensitive)."""
    person = Person(identifier="display_match", display_name="Display Match")
    db_session.add(person)
    db_session.flush()

    assert resolve_person(db_session, "Display Match") == person
    assert resolve_person(db_session, "display match") == person


def test_resolve_person_by_alias(db_session):
    """resolve_person finds person by alias."""
    person = Person(
        identifier="alias_match", display_name="Full Name", aliases=["Nickname"]
    )
    db_session.add(person)
    db_session.flush()

    assert resolve_person(db_session, "Nickname") == person


def test_resolve_person_by_email(db_session):
    """resolve_person finds person by email address."""
    person = Person(
        identifier="email_match",
        display_name="Email Match",
        contact_info={"email": "test@example.com"},
    )
    db_session.add(person)
    db_session.flush()

    assert resolve_person(db_session, "test@example.com") == person


def test_resolve_person_not_found(db_session):
    """resolve_person returns None when no match."""
    assert resolve_person(db_session, "nonexistent") is None


# =============================================================================
# set_team_members() fuzzy matching tests
# =============================================================================


def test_set_team_members_finds_by_display_name(db_session, admin_session):
    """set_team_members matches members by display_name, not just identifier."""
    team = Team(name="Test Team", slug="test-team")
    db_session.add(team)
    person = Person(identifier="alice_chen", display_name="Alice Chen")
    db_session.add(person)
    db_session.flush()

    # Use display name (not identifier) - should match the existing person
    result = set_team_members(db_session, team, ["Alice Chen"])

    assert result["added"] == ["alice_chen"]
    assert result["created_people"] == []
    assert person in team.members


def test_set_team_members_finds_by_alias(db_session, admin_session):
    """set_team_members matches members by alias."""
    team = Team(name="Test Team", slug="test-team")
    db_session.add(team)
    person = Person(
        identifier="bob_smith", display_name="Robert Smith", aliases=["Bob Smith"]
    )
    db_session.add(person)
    db_session.flush()

    # Use alias - should match the existing person
    result = set_team_members(db_session, team, ["Bob Smith"])

    assert result["added"] == ["bob_smith"]
    assert result["created_people"] == []
    assert person in team.members


def test_set_team_members_finds_by_email(db_session, admin_session):
    """set_team_members matches members by email."""
    team = Team(name="Test Team", slug="test-team")
    db_session.add(team)
    person = Person(
        identifier="carol_jones",
        display_name="Carol Jones",
        contact_info={"email": "carol@example.com"},
    )
    db_session.add(person)
    db_session.flush()

    # Use email - should match the existing person
    result = set_team_members(db_session, team, ["carol@example.com"])

    assert result["added"] == ["carol_jones"]
    assert result["created_people"] == []
    assert person in team.members


def test_set_team_members_no_duplicate_from_display_name(db_session, admin_session):
    """set_team_members should not create a duplicate when given display_name of existing person."""
    team = Team(name="Dedup Team", slug="dedup-team")
    person = Person(identifier="dedup_person", display_name="Dedup Person")
    db_session.add_all([team, person])
    db_session.flush()

    # Pass display_name — should find existing, not create a new "dedup_person" + "Dedup Person"
    result = set_team_members(db_session, team, ["Dedup Person"])

    assert result["created_people"] == []
    assert len(team.members) == 1
    assert team.members[0].id == person.id

    # Verify no duplicate Person was created
    all_people = db_session.query(Person).filter(
        Person.display_name == "Dedup Person"
    ).all()
    assert len(all_people) == 1


def test_set_team_members_dedup_identifier_and_display_name(db_session, admin_session):
    """Both identifier and display_name in member list should resolve to same person."""
    team = Team(name="Dedup Team 2", slug="dedup-team-2")
    person = Person(identifier="same_person", display_name="Same Person")
    db_session.add_all([team, person])
    db_session.flush()

    result = set_team_members(db_session, team, ["same_person", "Same Person"])

    assert len(team.members) == 1
    assert team.members[0].id == person.id
    assert result["created_people"] == []


# =============================================================================
# upsert() tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_creates_new_team(db_session, admin_session):
    """Test upsert creates a new team."""

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

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(name="Auto Slug Team")

    assert result["success"] is True
    assert result["team"]["slug"] == "auto-slug-team"


@pytest.mark.asyncio
async def test_upsert_with_members_list(db_session, admin_session):
    """Test upsert with explicit members list."""

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


# =============================================================================
# upsert with is_active tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_archive_team(db_session, admin_session):
    """Test upsert can archive a team via is_active=False."""

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
    from datetime import timezone

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
        )

    assert result["success"] is True
    assert result["person"] == "removable"


@pytest.mark.asyncio
async def test_team_remove_member_not_a_member(db_session, admin_session):
    """Test team_remove_member handles non-member gracefully."""

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


# =============================================================================
# fetch with include_members tests
# =============================================================================


@pytest.mark.asyncio
async def test_fetch_with_include_members(db_session, admin_session):
    """Test fetch returns members with roles when include_members=True."""

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
# project_list_access tests
# =============================================================================


@pytest.mark.asyncio
async def test_project_list_access_success(db_session, admin_session):
    """Test project_list_access returns all people with access."""

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
# Error handling tests
# =============================================================================


@pytest.mark.asyncio
async def test_team_add_member_invalid_role(db_session, admin_session):
    """Test team_add_member rejects invalid roles."""

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


@pytest.mark.asyncio
async def test_team_add_member_by_display_name(db_session, admin_session):
    """team_add_member resolves person by display_name."""
    team = Team(name="Add Fuzzy", slug="add-fuzzy")
    person = Person(identifier="fuzzy_add", display_name="Fuzzy Add Person")
    db_session.add_all([team, person])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(team_add_member)(
            team="add-fuzzy",
            person="Fuzzy Add Person",
        )

    assert result["success"] is True
    assert result["person"] == "fuzzy_add"


@pytest.mark.asyncio
async def test_team_add_member_by_alias(db_session, admin_session):
    """team_add_member resolves person by alias."""
    team = Team(name="Add Alias", slug="add-alias")
    person = Person(
        identifier="alias_add", display_name="Full Name", aliases=["Ally"]
    )
    db_session.add_all([team, person])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(team_add_member)(
            team="add-alias",
            person="Ally",
        )

    assert result["success"] is True
    assert result["person"] == "alias_add"


@pytest.mark.asyncio
async def test_team_remove_member_by_display_name(db_session, admin_session):
    """team_remove_member resolves person by display_name."""
    team = Team(name="Remove Fuzzy", slug="remove-fuzzy")
    person = Person(identifier="fuzzy_rm", display_name="Fuzzy Remove Person")
    db_session.add_all([team, person])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(team_remove_member)(
            team="remove-fuzzy",
            person="Fuzzy Remove Person",
        )

    assert result["success"] is True
    assert result["person"] == "fuzzy_rm"


# =============================================================================
# Owner management tests
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_create_with_owner(db_session, admin_session):
    """Test creating a team with an owner set."""
    person = Person(identifier="team_owner", display_name="Team Owner")
    db_session.add(person)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Owned Team",
            slug="owned-team",
            owner="team_owner",
        )

    assert result["success"] is True
    assert result["action"] == "created"
    assert result["team"]["owner_id"] == person.id
    assert result["team"]["owner"]["identifier"] == "team_owner"


@pytest.mark.asyncio
async def test_upsert_create_with_owner_by_id(db_session, admin_session):
    """Test creating a team with owner specified by numeric ID."""
    person = Person(identifier="owner_by_id", display_name="Owner By ID")
    db_session.add(person)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Owned By ID Team",
            owner=person.id,
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] == person.id


@pytest.mark.asyncio
async def test_upsert_create_without_owner(db_session, admin_session):
    """Test creating a team without setting an owner."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="No Owner Team",
            slug="no-owner-team",
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] is None
    assert result["team"]["owner"] is None


@pytest.mark.asyncio
async def test_upsert_set_owner_on_existing_team(db_session, admin_session):
    """Test setting an owner on an existing team that had no owner."""
    team = Team(name="Ownerless Team", slug="ownerless-team")
    person = Person(identifier="new_owner", display_name="New Owner")
    db_session.add_all([team, person])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Ownerless Team",
            slug="ownerless-team",
            owner="new_owner",
        )

    assert result["success"] is True
    assert result["action"] == "updated"
    assert result["team"]["owner_id"] == person.id
    assert result["team"]["owner"]["identifier"] == "new_owner"


@pytest.mark.asyncio
async def test_upsert_clear_owner(db_session, admin_session):
    """Test clearing an owner by passing owner=None."""
    person = Person(identifier="soon_removed", display_name="Soon Removed")
    db_session.add(person)
    db_session.flush()
    team = Team(name="Has Owner", slug="has-owner", owner_id=person.id)
    db_session.add(team)
    db_session.commit()

    # Verify owner is set
    assert team.owner_id == person.id

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Has Owner",
            slug="has-owner",
            owner=None,
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] is None
    assert result["team"]["owner"] is None


@pytest.mark.asyncio
async def test_upsert_change_owner(db_session, admin_session):
    """Test changing owner from one person to another."""
    person1 = Person(identifier="old_owner", display_name="Old Owner")
    person2 = Person(identifier="replacement_owner", display_name="Replacement Owner")
    db_session.add_all([person1, person2])
    db_session.flush()
    team = Team(name="Change Owner", slug="change-owner", owner_id=person1.id)
    db_session.add(team)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Change Owner",
            slug="change-owner",
            owner="replacement_owner",
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] == person2.id
    assert result["team"]["owner"]["identifier"] == "replacement_owner"


@pytest.mark.asyncio
async def test_upsert_owner_not_provided_preserves_existing(db_session, admin_session):
    """Test that not providing owner preserves the existing owner (no change)."""
    person = Person(identifier="preserved_owner", display_name="Preserved Owner")
    db_session.add(person)
    db_session.flush()
    team = Team(name="Preserve Owner", slug="preserve-owner", owner_id=person.id)
    db_session.add(team)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Preserve Owner",
            slug="preserve-owner",
            description="Updated description only",
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] == person.id
    assert result["team"]["owner"]["identifier"] == "preserved_owner"


@pytest.mark.asyncio
async def test_upsert_owner_not_found_warns(db_session, admin_session):
    """Test that a non-existent owner identifier produces a warning."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Bad Owner Team",
            owner="nonexistent_person",
        )

    assert result["success"] is True
    assert any("Owner not found" in w for w in result["warnings"])
    assert result["team"]["owner_id"] is None


@pytest.mark.asyncio
async def test_upsert_owner_by_display_name(db_session, admin_session):
    """Test setting owner using display_name instead of identifier."""
    person = Person(identifier="display_owner", display_name="Display Owner")
    db_session.add(person)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Owner Display Test",
            owner="Display Owner",
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] == person.id
    assert result["team"]["owner"]["identifier"] == "display_owner"


@pytest.mark.asyncio
async def test_upsert_owner_by_alias(db_session, admin_session):
    """Test setting owner using an alias."""
    person = Person(
        identifier="alias_owner",
        display_name="Alias Owner Full",
        aliases=["AO"],
    )
    db_session.add(person)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await get_fn(upsert)(
            name="Owner Alias Test",
            owner="AO",
        )

    assert result["success"] is True
    assert result["team"]["owner_id"] == person.id


def test_upsert_team_record_owner_skip(db_session):
    """Test upsert_team_record with owner_id=0 (skip) on update."""
    person = Person(identifier="record_owner", display_name="Record Owner")
    db_session.add(person)
    db_session.flush()
    team = Team(name="Record Test", slug="record-test", owner_id=person.id)
    db_session.add(team)
    db_session.commit()

    # Update with owner_id=0 (default/skip) - should not change owner
    updated_team, action = upsert_team_record(
        db_session, "record-test", "Record Test", None, None, None
    )
    assert action == "updated"
    assert updated_team.owner_id == person.id


def test_upsert_team_record_owner_clear(db_session):
    """Test upsert_team_record with owner_id=None (clear)."""
    person = Person(identifier="clear_owner", display_name="Clear Owner")
    db_session.add(person)
    db_session.flush()
    team = Team(name="Clear Test", slug="clear-test", owner_id=person.id)
    db_session.add(team)
    db_session.commit()

    # Update with owner_id=None - should clear owner
    updated_team, action = upsert_team_record(
        db_session, "clear-test", "Clear Test", None, None, None, owner_id=None
    )
    assert action == "updated"
    assert updated_team.owner_id is None


def test_upsert_team_record_owner_set(db_session):
    """Test upsert_team_record with a positive int owner_id (set)."""
    person = Person(identifier="set_owner", display_name="Set Owner")
    db_session.add(person)
    db_session.flush()
    team = Team(name="Set Test", slug="set-test")
    db_session.add(team)
    db_session.commit()

    # Update with owner_id=person.id - should set owner
    updated_team, action = upsert_team_record(
        db_session, "set-test", "Set Test", None, None, None, owner_id=person.id
    )
    assert action == "updated"
    assert updated_team.owner_id == person.id


def test_upsert_team_record_create_with_owner(db_session):
    """Test upsert_team_record creates team with owner."""
    person = Person(identifier="create_owner", display_name="Create Owner")
    db_session.add(person)
    db_session.flush()

    new_team, action = upsert_team_record(
        db_session, "create-with-owner", "Create With Owner", None, None, None,
        owner_id=person.id,
    )
    assert action == "created"
    assert new_team.owner_id == person.id


def test_upsert_team_record_create_without_owner(db_session):
    """Test upsert_team_record creates team without owner (default)."""
    new_team, action = upsert_team_record(
        db_session, "create-no-owner", "Create No Owner", None, None, None
    )
    assert action == "created"
    assert new_team.owner_id is None

