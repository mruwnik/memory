"""Tests for the Team model and team-based access control."""

import pytest

from memory.common.db.models import Person, Project


# ============== Team Model Tests ==============


@pytest.fixture
def team_data():
    """Standard team test data."""
    return {
        "name": "Engineering Core",
        "slug": "engineering-core",
        "description": "Core engineering team",
        "tags": ["engineering", "core"],
    }


@pytest.fixture
def minimal_team_data():
    """Minimal team test data."""
    return {
        "name": "Widgets Team",
        "slug": "widgets",
    }


def test_team_creation(team_data):
    """Test creating a Team with all fields."""
    from memory.common.db.models import Team

    team = Team(**team_data)

    assert team.name == "Engineering Core"
    assert team.slug == "engineering-core"
    assert team.description == "Core engineering team"
    assert team.tags == ["engineering", "core"]


def test_team_creation_minimal(minimal_team_data):
    """Test creating a Team with minimal fields."""
    from memory.common.db.models import Team

    team = Team(**minimal_team_data)

    assert team.name == "Widgets Team"
    assert team.slug == "widgets"
    assert team.description is None
    assert team.tags == [] or team.tags is None


def test_team_discord_integration_fields():
    """Test Team Discord integration fields."""
    from memory.common.db.models import Team

    team = Team(
        name="Discord Team",
        slug="discord-team",
        discord_role_id=123456789,
        discord_guild_id=987654321,
        auto_sync_discord=True,
    )

    assert team.discord_role_id == 123456789
    assert team.discord_guild_id == 987654321
    assert team.auto_sync_discord is True


def test_team_github_integration_fields():
    """Test Team GitHub integration fields."""
    from memory.common.db.models import Team

    team = Team(
        name="GitHub Team",
        slug="github-team",
        github_team_id=42,
        github_org="myorg",
        auto_sync_github=True,
    )

    assert team.github_team_id == 42
    assert team.github_org == "myorg"
    assert team.auto_sync_github is True


def test_team_lifecycle_fields():
    """Test Team lifecycle fields."""
    from memory.common.db.models import Team

    team = Team(
        name="Active Team",
        slug="active-team",
        is_active=True,
    )

    assert team.is_active is True
    assert team.archived_at is None


def test_team_in_db(db_session, qdrant):
    """Test Team persistence in database."""
    from memory.common.db.models import Team

    team = Team(
        name="DB Test Team",
        slug="db-test-team",
        description="A test team",
        tags=["test", "db"],
    )

    db_session.add(team)
    db_session.commit()

    # Query it back
    retrieved = db_session.query(Team).filter_by(slug="db-test-team").first()

    assert retrieved is not None
    assert retrieved.name == "DB Test Team"
    assert retrieved.description == "A test team"
    assert retrieved.tags == ["test", "db"]
    assert retrieved.created_at is not None


def test_team_unique_slug(db_session, qdrant):
    """Test that slug must be unique."""
    from memory.common.db.models import Team

    team1 = Team(name="Team 1", slug="unique-slug")
    db_session.add(team1)
    db_session.commit()

    team2 = Team(name="Team 2", slug="unique-slug")
    db_session.add(team2)

    with pytest.raises(Exception):  # Should raise IntegrityError
        db_session.commit()


# ============== Team Membership Tests ==============


def test_team_add_member(db_session, qdrant):
    """Test adding a person to a team."""
    from memory.common.db.models import Team

    team = Team(name="Membership Test", slug="membership-test")
    person = Person(identifier="member_test", display_name="Member Test")

    db_session.add(team)
    db_session.add(person)
    db_session.flush()

    team.members.append(person)
    db_session.commit()

    # Query back
    retrieved = db_session.query(Team).filter_by(slug="membership-test").first()
    assert len(retrieved.members) == 1
    assert retrieved.members[0].identifier == "member_test"


def test_team_multiple_members(db_session, qdrant):
    """Test team with multiple members."""
    from memory.common.db.models import Team

    team = Team(name="Multi Member", slug="multi-member")
    people = [
        Person(identifier=f"member_{i}", display_name=f"Member {i}")
        for i in range(3)
    ]

    db_session.add(team)
    for person in people:
        db_session.add(person)
    db_session.flush()

    for person in people:
        team.members.append(person)
    db_session.commit()

    retrieved = db_session.query(Team).filter_by(slug="multi-member").first()
    assert len(retrieved.members) == 3


def test_person_multiple_teams(db_session, qdrant):
    """Test person can be in multiple teams."""
    from memory.common.db.models import Team

    teams = [
        Team(name=f"Team {i}", slug=f"team-{i}")
        for i in range(3)
    ]
    person = Person(identifier="multi_team", display_name="Multi Team Person")

    for team in teams:
        db_session.add(team)
    db_session.add(person)
    db_session.flush()

    for team in teams:
        team.members.append(person)
    db_session.commit()

    retrieved = db_session.query(Person).filter_by(identifier="multi_team").first()
    assert len(retrieved.teams) == 3


def test_team_remove_member(db_session, qdrant):
    """Test removing a person from a team."""
    from memory.common.db.models import Team

    team = Team(name="Remove Test", slug="remove-test")
    person = Person(identifier="remove_test", display_name="Remove Test")

    db_session.add(team)
    db_session.add(person)
    db_session.flush()

    team.members.append(person)
    db_session.commit()
    assert len(team.members) == 1

    team.members.remove(person)
    db_session.commit()

    retrieved = db_session.query(Team).filter_by(slug="remove-test").first()
    assert len(retrieved.members) == 0


# ============== Project-Team Assignment Tests ==============


def test_project_assign_team(db_session, qdrant):
    """Test assigning a team to a project."""
    from memory.common.db.models import Team

    team = Team(name="Project Team", slug="project-team")
    project = Project(title="Test Project", state="open")

    db_session.add(team)
    db_session.add(project)
    db_session.flush()

    project.teams.append(team)
    db_session.commit()

    retrieved = db_session.query(Project).filter_by(title="Test Project").first()
    assert len(retrieved.teams) == 1
    assert retrieved.teams[0].slug == "project-team"


def test_project_multiple_teams(db_session, qdrant):
    """Test project with multiple assigned teams."""
    from memory.common.db.models import Team

    teams = [
        Team(name=f"Assigned Team {i}", slug=f"assigned-team-{i}")
        for i in range(2)
    ]
    project = Project(title="Multi Team Project", state="open")

    for team in teams:
        db_session.add(team)
    db_session.add(project)
    db_session.flush()

    for team in teams:
        project.teams.append(team)
    db_session.commit()

    retrieved = db_session.query(Project).filter_by(title="Multi Team Project").first()
    assert len(retrieved.teams) == 2


def test_team_multiple_projects(db_session, qdrant):
    """Test team assigned to multiple projects."""
    from memory.common.db.models import Team

    team = Team(name="Multi Project Team", slug="multi-project-team")
    projects = [
        Project(title=f"Project {i}", state="open")
        for i in range(3)
    ]

    db_session.add(team)
    for project in projects:
        db_session.add(project)
    db_session.flush()

    for project in projects:
        project.teams.append(team)
    db_session.commit()

    retrieved = db_session.query(Team).filter_by(slug="multi-project-team").first()
    assert len(retrieved.projects) == 3


# ============== Access Control Tests ==============


def test_can_access_project_via_team(db_session, qdrant):
    """Test that person can access project via team membership."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import can_access_project

    team = Team(name="Access Team", slug="access-team")
    person = Person(identifier="access_test", display_name="Access Test")
    project = Project(title="Access Project", state="open")

    db_session.add(team)
    db_session.add(person)
    db_session.add(project)
    db_session.flush()

    team.members.append(person)
    project.teams.append(team)
    db_session.commit()

    assert can_access_project(person, project) is True


def test_cannot_access_project_without_team(db_session, qdrant):
    """Test that person cannot access project without team membership."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import can_access_project

    team = Team(name="Other Team", slug="other-team")
    person = Person(identifier="no_access", display_name="No Access")
    project = Project(title="Restricted Project", state="open")

    db_session.add(team)
    db_session.add(person)
    db_session.add(project)
    db_session.flush()

    # Team assigned to project, but person not in team
    project.teams.append(team)
    db_session.commit()

    assert can_access_project(person, project) is False


def test_can_access_project_via_any_team(db_session, qdrant):
    """Test that person can access project if in ANY assigned team."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import can_access_project

    team1 = Team(name="Team A", slug="team-a")
    team2 = Team(name="Team B", slug="team-b")
    person = Person(identifier="any_team", display_name="Any Team")
    project = Project(title="Any Team Project", state="open")

    db_session.add_all([team1, team2, person, project])
    db_session.flush()

    # Person in team2, project assigned to both teams
    team2.members.append(person)
    project.teams.append(team1)
    project.teams.append(team2)
    db_session.commit()

    # Person should have access (in team2)
    assert can_access_project(person, project) is True


# ============== Person Contributor Status Tests ==============


def test_person_contributor_status_default(db_session, qdrant):
    """Test that Person has default contributor status."""
    person = Person(identifier="status_default", display_name="Status Default")

    db_session.add(person)
    db_session.commit()

    retrieved = db_session.query(Person).filter_by(identifier="status_default").first()
    assert retrieved.contributor_status == "contractor"


def test_person_contributor_status_internal(db_session, qdrant):
    """Test setting Person contributor status to internal."""
    person = Person(
        identifier="status_internal",
        display_name="Status Internal",
        contributor_status="internal",
    )

    db_session.add(person)
    db_session.commit()

    retrieved = db_session.query(Person).filter_by(identifier="status_internal").first()
    assert retrieved.contributor_status == "internal"


@pytest.mark.parametrize(
    "status",
    ["internal", "contractor", "observer", "inactive"],
)
def test_person_contributor_status_values(db_session, qdrant, status):
    """Test valid contributor status values."""
    person = Person(
        identifier=f"status_{status}",
        display_name=f"Status {status}",
        contributor_status=status,
    )

    db_session.add(person)
    db_session.commit()

    retrieved = db_session.query(Person).filter_by(identifier=f"status_{status}").first()
    assert retrieved.contributor_status == status


# ============== Tags Query Tests ==============


def test_teams_by_tag(db_session, qdrant):
    """Test querying teams by tag."""
    from memory.common.db.models import Team
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    from sqlalchemy import Text

    teams = [
        Team(name="Eng Core", slug="eng-core", tags=["engineering", "core"]),
        Team(name="Eng Frontend", slug="eng-frontend", tags=["engineering", "frontend"]),
        Team(name="Design", slug="design", tags=["design"]),
    ]

    for team in teams:
        db_session.add(team)
    db_session.commit()

    # Query for engineering teams using PostgreSQL array contains
    engineering_teams = (
        db_session.query(Team)
        .filter(Team.tags.op("@>")(cast(["engineering"], PG_ARRAY(Text))))
        .all()
    )

    assert len(engineering_teams) == 2
    slugs = {t.slug for t in engineering_teams}
    assert slugs == {"eng-core", "eng-frontend"}


def test_teams_by_multiple_tags(db_session, qdrant):
    """Test querying teams by multiple tags (AND logic)."""
    from memory.common.db.models import Team
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    from sqlalchemy import Text

    teams = [
        Team(name="Eng Core", slug="eng-core", tags=["engineering", "core"]),
        Team(name="Eng Frontend", slug="eng-frontend", tags=["engineering", "frontend"]),
        Team(name="Core Infra", slug="core-infra", tags=["infrastructure", "core"]),
    ]

    for team in teams:
        db_session.add(team)
    db_session.commit()

    # Query for teams with both engineering AND core
    eng_core_teams = (
        db_session.query(Team)
        .filter(Team.tags.op("@>")(cast(["engineering", "core"], PG_ARRAY(Text))))
        .all()
    )

    assert len(eng_core_teams) == 1
    assert eng_core_teams[0].slug == "eng-core"


# ============== Team Member Role Tests ==============


def test_team_member_role_via_junction_table(db_session, qdrant):
    """Test that team member roles are properly set via junction table insert."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import team_members

    team = Team(name="Role Test Team", slug="role-test")
    person = Person(identifier="role_test_person", display_name="Role Test Person")

    db_session.add(team)
    db_session.add(person)
    db_session.flush()

    # Add member with explicit role via junction table
    db_session.execute(
        team_members.insert().values(
            team_id=team.id,
            person_id=person.id,
            role="lead",
        )
    )
    db_session.commit()

    # Query the junction table to verify role
    result = db_session.execute(
        team_members.select().where(
            team_members.c.team_id == team.id,
            team_members.c.person_id == person.id,
        )
    ).fetchone()

    assert result is not None
    assert result.role == "lead"


@pytest.mark.parametrize(
    "team_role,expected_project_role",
    [
        ("member", "contributor"),
        ("lead", "manager"),
        ("admin", "admin"),
    ],
)
def test_team_role_maps_to_project_role(db_session, qdrant, team_role, expected_project_role):
    """Test that team roles correctly map to project roles."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import team_members
    from memory.common.db.models.users import HumanUser
    from memory.common.access_control import get_user_project_roles

    team = Team(name=f"Role Map Test {team_role}", slug=f"role-map-{team_role}")
    person = Person(identifier=f"role_map_{team_role}", display_name=f"Role Map {team_role}")
    project = Project(title=f"Role Map Project {team_role}", state="open")

    db_session.add_all([team, person, project])
    db_session.flush()

    # Create user linked to person
    user = HumanUser(
        email=f"roletest_{team_role}@example.com",
        password_hash="test",
        name=f"Role Test {team_role}",
        person_id=person.id,
    )
    db_session.add(user)

    # Add person to team with specific role
    db_session.execute(
        team_members.insert().values(
            team_id=team.id,
            person_id=person.id,
            role=team_role,
        )
    )

    # Assign team to project
    project.teams.append(team)
    db_session.commit()

    # Get project roles for user
    db_session.refresh(user)
    project_roles = get_user_project_roles(db_session, user)

    assert project.id in project_roles
    assert project_roles[project.id] == expected_project_role


def test_get_user_project_roles_multiple_teams_highest_wins(db_session, qdrant):
    """Test that when user is in multiple teams for same project, highest privilege wins."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import team_members
    from memory.common.db.models.users import HumanUser
    from memory.common.access_control import get_user_project_roles

    team1 = Team(name="Basic Team", slug="basic-team")
    team2 = Team(name="Admin Team", slug="admin-team")
    person = Person(identifier="multi_team_person", display_name="Multi Team Person")
    project = Project(title="Multi Team Project", state="open")

    db_session.add_all([team1, team2, person, project])
    db_session.flush()

    # Create user linked to person
    user = HumanUser(
        email="multiteam@example.com",
        password_hash="test",
        name="Multi Team User",
        person_id=person.id,
    )
    db_session.add(user)

    # Add person to both teams with different roles
    db_session.execute(
        team_members.insert().values(
            team_id=team1.id,
            person_id=person.id,
            role="member",  # -> contributor
        )
    )
    db_session.execute(
        team_members.insert().values(
            team_id=team2.id,
            person_id=person.id,
            role="admin",  # -> admin (higher)
        )
    )

    # Assign both teams to project
    project.teams.append(team1)
    project.teams.append(team2)
    db_session.commit()

    # Get project roles - should get highest privilege
    db_session.refresh(user)
    project_roles = get_user_project_roles(db_session, user)

    assert project.id in project_roles
    assert project_roles[project.id] == "admin"  # Highest privilege wins


def test_get_user_project_roles_inactive_team_still_grants_access(db_session, qdrant):
    """Test that inactive teams still grant access (filter at query level, not access control)."""
    from memory.common.db.models import Team
    from memory.common.db.models.sources import team_members
    from memory.common.db.models.users import HumanUser
    from memory.common.access_control import get_user_project_roles

    team = Team(name="Inactive Team", slug="inactive-team", is_active=False)
    person = Person(identifier="inactive_team_person", display_name="Inactive Team Person")
    project = Project(title="Inactive Team Project", state="open")

    db_session.add_all([team, person, project])
    db_session.flush()

    user = HumanUser(
        email="inactiveteam@example.com",
        password_hash="test",
        name="Inactive Team User",
        person_id=person.id,
    )
    db_session.add(user)

    # Add person to team
    db_session.execute(
        team_members.insert().values(
            team_id=team.id,
            person_id=person.id,
            role="member",
        )
    )

    project.teams.append(team)
    db_session.commit()

    # Access control still grants access (filtering inactive teams is a separate concern)
    db_session.refresh(user)
    project_roles = get_user_project_roles(db_session, user)

    # Note: get_user_project_roles does not filter by is_active - that's intentional
    # The team relationship still exists, so access is granted
    assert project.id in project_roles
    assert project_roles[project.id] == "contributor"


def test_get_user_project_roles_no_person_returns_empty(db_session, qdrant):
    """Test that user without linked person gets no project roles."""
    from memory.common.db.models.users import HumanUser
    from memory.common.access_control import get_user_project_roles

    user = HumanUser(
        email="noperson@example.com",
        password_hash="test",
        name="No Person User",
        person_id=None,
    )
    db_session.add(user)
    db_session.commit()

    project_roles = get_user_project_roles(db_session, user)

    assert project_roles == {}


def test_get_user_project_roles_person_not_in_any_team(db_session, qdrant):
    """Test that user with person but not in any team gets no project roles."""
    from memory.common.db.models.users import HumanUser
    from memory.common.access_control import get_user_project_roles

    person = Person(identifier="lonely_person", display_name="Lonely Person")
    db_session.add(person)
    db_session.flush()

    user = HumanUser(
        email="lonely@example.com",
        password_hash="test",
        name="Lonely User",
        person_id=person.id,
    )
    db_session.add(user)
    db_session.commit()

    db_session.refresh(user)
    project_roles = get_user_project_roles(db_session, user)

    assert project_roles == {}
