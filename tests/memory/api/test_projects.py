"""Tests for Projects API endpoints with owner and due_on fields."""

import uuid
from datetime import datetime, timezone

import pytest

from memory.common.db.models import Person
from memory.common.db.models.sources import Project, Team, project_teams, team_members


def unique_id(prefix: str = "") -> str:
    """Generate a unique identifier for test data."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ====== Fixtures ======


@pytest.fixture
def person(db_session):
    """Create a test person."""
    p = Person(
        identifier=unique_id("alice"),
        display_name="Alice Chen",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def person2(db_session):
    """Create a second test person."""
    p = Person(
        identifier=unique_id("bob"),
        display_name="Bob Smith",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def team(db_session, user):
    """Create a test team with the user as a member."""
    t = Team(
        name="Test Team",
        slug=unique_id("team"),
        description="A test team",
    )
    db_session.add(t)
    db_session.flush()

    # Link user to person
    user_person = Person(
        identifier=unique_id(f"user_{user.id}"),
        display_name=user.name,
        user_id=user.id,
    )
    db_session.add(user_person)
    db_session.flush()

    # Add user's person to the team
    db_session.execute(
        team_members.insert().values(
            team_id=t.id,
            person_id=user_person.id,
            role="admin",
        )
    )
    db_session.commit()
    return t


@pytest.fixture
def standalone_project(db_session, team):
    """Create a standalone project (not GitHub-backed) with a team."""
    # Use unique negative ID to avoid collisions
    project_id = -(uuid.uuid4().int & ((1 << 62) - 1)) - 1
    project = Project(
        id=project_id,
        repo_id=None,
        github_id=None,
        number=None,
        title="Test Project",
        description="A test project",
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
    return project


# ====== POST /projects - Create with owner and due_on ======


def test_create_project_with_owner(client, db_session, user, team, person):
    """Create a project with an owner."""
    payload = {
        "title": "Project with Owner",
        "team_id": team.id,
        "owner_id": person.id,
    }

    response = client.post("/projects", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == person.id


def test_create_project_with_due_on(client, db_session, user, team):
    """Create a project with a due date."""
    due_date = "2026-03-15T12:00:00+00:00"
    payload = {
        "title": "Project with Due Date",
        "team_id": team.id,
        "due_on": due_date,
    }

    response = client.post("/projects", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["due_on"] == "2026-03-15T12:00:00+00:00"


def test_create_project_with_owner_and_due_on(client, db_session, user, team, person):
    """Create a project with both owner and due date."""
    due_date = "2026-06-01T00:00:00+00:00"
    payload = {
        "title": "Full Project",
        "team_id": team.id,
        "owner_id": person.id,
        "due_on": due_date,
    }

    response = client.post("/projects", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == person.id
    assert data["due_on"] == "2026-06-01T00:00:00+00:00"


def test_create_project_with_invalid_owner(client, db_session, user, team):
    """Create project with non-existent owner fails."""
    payload = {
        "title": "Bad Owner Project",
        "team_id": team.id,
        "owner_id": 99999,
    }

    response = client.post("/projects", json=payload)

    assert response.status_code == 400
    assert "Owner not found" in response.json()["detail"]


def test_create_project_with_invalid_due_on(client, db_session, user, team):
    """Create project with invalid due date format fails."""
    payload = {
        "title": "Bad Date Project",
        "team_id": team.id,
        "due_on": "not-a-date",
    }

    response = client.post("/projects", json=payload)

    assert response.status_code == 400
    assert "Invalid due_on format" in response.json()["detail"]


# ====== GET /projects/{id} - Get with owner ======


def test_get_project_includes_owner(client, db_session, user, standalone_project, person):
    """Get project includes owner details when include_owner=True."""
    standalone_project.owner_id = person.id
    db_session.commit()

    response = client.get(f"/projects/{standalone_project.id}?include_owner=true")

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == person.id
    assert data["owner"]["id"] == person.id
    assert data["owner"]["identifier"] == person.identifier
    assert data["owner"]["display_name"] == person.display_name


def test_get_project_without_owner(client, db_session, user, standalone_project):
    """Get project without owner returns null owner."""
    response = client.get(f"/projects/{standalone_project.id}?include_owner=true")

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] is None
    assert data["owner"] is None


# ====== GET /projects - List with owner ======


def test_list_projects_includes_owner(client, db_session, user, standalone_project, person):
    """List projects includes owner when include_owner=True."""
    standalone_project.owner_id = person.id
    db_session.commit()

    response = client.get("/projects?include_owner=true")

    assert response.status_code == 200
    data = response.json()
    # Find our project in the list (there may be other projects from previous tests)
    our_project = next((p for p in data if p["id"] == standalone_project.id), None)
    assert our_project is not None, f"Project {standalone_project.id} not found in response"
    assert our_project["owner_id"] == person.id
    assert our_project["owner"]["identifier"] == person.identifier


# ====== PATCH /projects/{id} - Update owner ======


def test_update_project_set_owner(client, db_session, user, standalone_project, person):
    """Update project to set owner."""
    payload = {"owner_id": person.id}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == person.id


def test_update_project_change_owner(client, db_session, user, standalone_project, person, person2):
    """Update project to change owner."""
    standalone_project.owner_id = person.id
    db_session.commit()

    payload = {"owner_id": person2.id}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == person2.id


def test_update_project_clear_owner(client, db_session, user, standalone_project, person):
    """Update project to clear owner via clear_owner=True."""
    standalone_project.owner_id = person.id
    db_session.commit()

    payload = {"clear_owner": True}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] is None


def test_update_project_invalid_owner(client, db_session, user, standalone_project):
    """Update project with non-existent owner fails."""
    payload = {"owner_id": 99999}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 400
    assert "Owner not found" in response.json()["detail"]


# ====== PATCH /projects/{id} - Update due_on ======


def test_update_project_set_due_on(client, db_session, user, standalone_project):
    """Update project to set due date."""
    due_date = "2026-04-01T09:00:00+00:00"
    payload = {"due_on": due_date}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["due_on"] == "2026-04-01T09:00:00+00:00"


def test_update_project_change_due_on(client, db_session, user, standalone_project):
    """Update project to change due date."""
    standalone_project.due_on = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    new_due_date = "2026-12-31T23:59:59+00:00"
    payload = {"due_on": new_due_date}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["due_on"] == "2026-12-31T23:59:59+00:00"


def test_update_project_clear_due_on(client, db_session, user, standalone_project):
    """Update project to clear due date via clear_due_on=True."""
    standalone_project.due_on = datetime(2026, 6, 15, tzinfo=timezone.utc)
    db_session.commit()

    payload = {"clear_due_on": True}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["due_on"] is None


def test_update_project_invalid_due_on(client, db_session, user, standalone_project):
    """Update project with invalid due date format fails."""
    payload = {"due_on": "invalid-date"}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 400
    assert "Invalid due_on format" in response.json()["detail"]


# ====== Combined owner and due_on updates ======


def test_update_project_owner_and_due_on(client, db_session, user, standalone_project, person):
    """Update project to set both owner and due date."""
    due_date = "2026-09-01T00:00:00+00:00"
    payload = {
        "owner_id": person.id,
        "due_on": due_date,
    }

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == person.id
    assert data["due_on"] == "2026-09-01T00:00:00+00:00"


def test_update_project_clear_both(client, db_session, user, standalone_project, person):
    """Update project to clear both owner and due date."""
    standalone_project.owner_id = person.id
    standalone_project.due_on = datetime(2026, 6, 15, tzinfo=timezone.utc)
    db_session.commit()

    payload = {
        "clear_owner": True,
        "clear_due_on": True,
    }

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] is None
    assert data["due_on"] is None
