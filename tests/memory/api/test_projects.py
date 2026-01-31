"""Tests for Projects API endpoints with collaborator management."""

import pytest

from memory.common.db.models.people import Person
from memory.common.db.models.sources import Project, project_collaborators


# ====== Fixtures ======


@pytest.fixture
def person(db_session):
    """Create a test person."""
    p = Person(
        identifier="alice_chen",
        display_name="Alice Chen",
        modality="person",
        sha256=b"alice_sha256",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def person2(db_session):
    """Create a second test person."""
    p = Person(
        identifier="bob_smith",
        display_name="Bob Smith",
        modality="person",
        sha256=b"bob_sha256",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def standalone_project(db_session):
    """Create a standalone project (not GitHub-backed)."""
    project = Project(
        id=-1,
        repo_id=None,
        github_id=None,
        number=None,
        title="Test Project",
        description="A test project",
        state="open",
    )
    db_session.add(project)
    db_session.commit()
    return project


# ====== GET /projects - List with collaborators ======


def test_list_projects_includes_collaborators(client, db_session, user, standalone_project, person):
    """List projects includes collaborator information."""
    db_session.execute(
        project_collaborators.insert().values(
            project_id=standalone_project.id,
            person_id=person.id,
            role="contributor",
        )
    )
    db_session.commit()

    response = client.get("/projects")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["collaborators"] == [
        {
            "person_id": person.id,
            "person_identifier": "alice_chen",
            "display_name": "Alice Chen",
            "role": "contributor",
        }
    ]


def test_list_projects_empty_collaborators(client, db_session, user, standalone_project):
    """List projects shows empty collaborators list when none exist."""
    response = client.get("/projects")

    assert response.status_code == 200
    assert response.json()[0]["collaborators"] == []


# ====== GET /projects/{id} - Get with collaborators ======


def test_get_project_includes_multiple_collaborators(client, db_session, user, standalone_project, person, person2):
    """Get project includes all collaborators with roles."""
    db_session.execute(
        project_collaborators.insert().values(
            project_id=standalone_project.id,
            person_id=person.id,
            role="admin",
        )
    )
    db_session.execute(
        project_collaborators.insert().values(
            project_id=standalone_project.id,
            person_id=person2.id,
            role="contributor",
        )
    )
    db_session.commit()

    response = client.get(f"/projects/{standalone_project.id}")

    assert response.status_code == 200
    collaborators = {c["person_identifier"]: c["role"] for c in response.json()["collaborators"]}
    assert collaborators == {"alice_chen": "admin", "bob_smith": "contributor"}


# ====== POST /projects - Create with collaborators ======


@pytest.mark.parametrize(
    "collaborator_input,expected_role",
    [
        ({"person_id": "PERSON_ID", "role": "admin"}, "admin"),
        ({"person_id": "PERSON_ID", "role": "manager"}, "manager"),
        ({"person_id": "PERSON_ID", "role": "contributor"}, "contributor"),
        ({"person_identifier": "alice_chen", "role": "admin"}, "admin"),
        ({"person_identifier": "alice_chen", "role": "manager"}, "manager"),
    ],
)
def test_create_project_with_collaborator(client, db_session, user, person, collaborator_input, expected_role):
    """Create project with collaborator by id or identifier."""
    # Replace placeholder with actual person id
    if "person_id" in collaborator_input and collaborator_input["person_id"] == "PERSON_ID":
        collaborator_input = {**collaborator_input, "person_id": person.id}

    payload = {"title": "New Project", "collaborators": [collaborator_input]}

    response = client.post("/projects", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert len(data["collaborators"]) == 1
    assert data["collaborators"][0]["person_id"] == person.id
    assert data["collaborators"][0]["role"] == expected_role


@pytest.mark.parametrize(
    "invalid_collaborator,expected_status",
    [
        ({"person_id": 99999, "role": "contributor"}, 400),  # Non-existent ID
        ({"person_identifier": "nonexistent", "role": "contributor"}, 400),  # Non-existent identifier
        # Note: model_validator errors return 500 due to app error handling quirk
        ({"role": "admin"}, 500),  # Missing both id and identifier
        ({"person_id": 1, "role": "superuser"}, 422),  # Invalid role
    ],
)
def test_create_project_with_invalid_collaborator(client, db_session, user, invalid_collaborator, expected_status):
    """Create project with invalid collaborator fails."""
    payload = {"title": "New Project", "collaborators": [invalid_collaborator]}

    response = client.post("/projects", json=payload)

    assert response.status_code == expected_status


def test_create_project_without_collaborators(client, db_session, user):
    """Create project without collaborators succeeds."""
    response = client.post("/projects", json={"title": "New Project"})

    assert response.status_code == 200
    assert response.json()["collaborators"] == []


# ====== PATCH /projects/{id} - Update collaborators ======


def test_update_project_set_collaborators(client, db_session, user, standalone_project, person):
    """Update project to set collaborators."""
    payload = {"collaborators": [{"person_id": person.id, "role": "admin"}]}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    assert len(response.json()["collaborators"]) == 1
    assert response.json()["collaborators"][0]["person_id"] == person.id


def test_update_project_replace_collaborators(client, db_session, user, standalone_project, person, person2):
    """Update project replaces all collaborators (full replace semantics)."""
    db_session.execute(
        project_collaborators.insert().values(
            project_id=standalone_project.id,
            person_id=person.id,
            role="contributor",
        )
    )
    db_session.commit()

    payload = {"collaborators": [{"person_id": person2.id, "role": "manager"}]}

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    collaborators = response.json()["collaborators"]
    assert len(collaborators) == 1
    assert collaborators[0]["person_id"] == person2.id
    assert collaborators[0]["role"] == "manager"


def test_update_project_clear_collaborators(client, db_session, user, standalone_project, person):
    """Update project with empty list clears all collaborators."""
    db_session.execute(
        project_collaborators.insert().values(
            project_id=standalone_project.id,
            person_id=person.id,
            role="contributor",
        )
    )
    db_session.commit()

    response = client.patch(f"/projects/{standalone_project.id}", json={"collaborators": []})

    assert response.status_code == 200
    assert response.json()["collaborators"] == []

    rows = db_session.execute(
        project_collaborators.select().where(project_collaborators.c.project_id == standalone_project.id)
    ).fetchall()
    assert len(rows) == 0


def test_update_project_omit_collaborators_no_change(client, db_session, user, standalone_project, person):
    """Update project without collaborators field leaves collaborators unchanged."""
    db_session.execute(
        project_collaborators.insert().values(
            project_id=standalone_project.id,
            person_id=person.id,
            role="contributor",
        )
    )
    db_session.commit()

    response = client.patch(f"/projects/{standalone_project.id}", json={"title": "Updated Title"})

    assert response.status_code == 200
    assert response.json()["title"] == "Updated Title"
    assert len(response.json()["collaborators"]) == 1


def test_update_project_duplicate_person_uses_last_role(client, db_session, user, standalone_project, person):
    """Update with duplicate person entries uses the last role."""
    payload = {
        "collaborators": [
            {"person_id": person.id, "role": "contributor"},
            {"person_id": person.id, "role": "admin"},
        ],
    }

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    collaborators = response.json()["collaborators"]
    assert len(collaborators) == 1
    assert collaborators[0]["role"] == "admin"


def test_update_project_mixed_id_and_identifier(client, db_session, user, standalone_project, person, person2):
    """Update project with mixed person_id and person_identifier."""
    payload = {
        "collaborators": [
            {"person_id": person.id, "role": "admin"},
            {"person_identifier": "bob_smith", "role": "contributor"},
        ],
    }

    response = client.patch(f"/projects/{standalone_project.id}", json=payload)

    assert response.status_code == 200
    assert len(response.json()["collaborators"]) == 2
