"""Tests for the GitHub person-linking helpers in memory.common.people."""

import pytest

from memory.common.db.models import Person
from memory.common.db.models.sources import GithubUser
from memory.common.people import find_person_by_github, link_github_user_to_person


@pytest.fixture
def person_with_github_contact(db_session):
    person = Person(
        identifier="github:octocat",
        display_name="octocat",
        aliases=["octocat"],
        contact_info={"github": "octocat"},
    )
    db_session.add(person)
    db_session.commit()
    return person


def test_find_person_by_github_via_contact_info(db_session, person_with_github_contact):
    assert find_person_by_github(db_session, "octocat") == person_with_github_contact


def test_find_person_by_github_via_github_user(db_session):
    person = Person(identifier="p", display_name="P")
    db_session.add(person)
    db_session.flush()
    db_session.add(GithubUser(id=583231, username="octo", person_id=person.id))
    db_session.commit()

    assert find_person_by_github(db_session, "octo") == person


@pytest.mark.parametrize("login", [None, "", "nobody"])
def test_find_person_by_github_no_match(db_session, login):
    assert find_person_by_github(db_session, login) is None


def test_find_person_by_github_contact_info_is_deterministic(db_session):
    p1 = Person(identifier="dup_a", display_name="A", contact_info={"github": "dup"})
    p2 = Person(identifier="dup_b", display_name="B", contact_info={"github": "dup"})
    db_session.add_all([p1, p2])
    db_session.commit()

    # Lowest id wins, repeatably.
    match = find_person_by_github(db_session, "dup")
    assert match is not None
    assert match.id == min(p1.id, p2.id)


def test_link_github_user_creates_row_and_sets_contact_info(db_session):
    person = Person(identifier="p2", display_name="P2", contact_info={})
    db_session.add(person)
    db_session.flush()

    github_user = link_github_user_to_person(
        db_session,
        person,
        123,
        "octo",
        display_name="Octo",
        avatar_url="http://example/a.png",
        email="o@example.com",
    )

    assert github_user.id == 123
    assert github_user.username == "octo"
    assert github_user.person_id == person.id
    assert github_user.display_name == "Octo"
    assert person.contact_info["github"] == "octo"


def test_link_github_user_reconciles_recycled_username(db_session):
    person = Person(identifier="p4", display_name="P4", contact_info={})
    db_session.add(person)
    db_session.flush()
    # A stale row holds the login under a different numeric id.
    db_session.add(GithubUser(id=5, username="recycled", person_id=person.id))
    db_session.flush()

    github_user = link_github_user_to_person(db_session, person, 99, "recycled")

    assert github_user.id == 99
    assert github_user.username == "recycled"
    # The stale id=5 row was dropped so the unique username didn't collide.
    assert db_session.get(GithubUser, 5) is None
    assert (
        db_session.query(GithubUser).filter_by(username="recycled").count() == 1
    )


def test_link_github_user_upserts_existing_row(db_session):
    person = Person(identifier="p3", display_name="P3", contact_info={})
    db_session.add(person)
    db_session.flush()

    link_github_user_to_person(db_session, person, 555, "old")
    github_user = link_github_user_to_person(
        db_session, person, 555, "new", display_name="New"
    )

    rows = db_session.query(GithubUser).filter(GithubUser.id == 555).all()
    assert len(rows) == 1
    assert github_user.username == "new"
    assert github_user.display_name == "New"
