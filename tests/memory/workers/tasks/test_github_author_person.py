"""Tests for ingest-time GitHub author -> Person resolution."""

import pytest

from memory.common.db.models import Person
from memory.common.db.models.sources import GithubUser
from memory.workers.tasks.github import _resolve_author_person


@pytest.mark.parametrize(
    "login", [None, "", "ghost", "dependabot[bot]", "github-actions[bot]"]
)
def test_resolve_author_person_skips_bots_ghost_and_empty(db_session, login):
    assert _resolve_author_person(db_session, login, 123) is None
    # "ghost" must not become a shared Person.
    if login == "ghost":
        assert (
            db_session.query(GithubUser).filter_by(username="ghost").one_or_none()
            is None
        )


def test_resolve_author_person_reuses_existing_github_user(db_session):
    person = Person(identifier="x", display_name="X")
    db_session.add(person)
    db_session.flush()
    db_session.add(GithubUser(id=10, username="ghx", person_id=person.id))
    db_session.commit()

    # A different id is supplied, but the known login short-circuits to the
    # existing link without creating a second row.
    assert _resolve_author_person(db_session, "ghx", 999) == person.id
    assert db_session.query(GithubUser).filter_by(username="ghx").count() == 1


def test_resolve_author_person_relinks_orphaned_github_user(db_session):
    # GithubUser whose Person was deleted (person_id went NULL via SET NULL).
    db_session.add(GithubUser(id=5, username="orphan", person_id=None))
    db_session.commit()

    person_id = _resolve_author_person(db_session, "orphan", None)

    assert person_id is not None
    github_user = db_session.get(GithubUser, 5)
    assert github_user.person_id == person_id  # re-linked, not stuck at None


def test_resolve_author_person_handles_recycled_login(db_session):
    # Orphaned row holds the login; the login is now a different numeric id.
    db_session.add(GithubUser(id=5, username="recycled", person_id=None))
    db_session.commit()

    person_id = _resolve_author_person(db_session, "recycled", 99)

    assert person_id is not None
    assert db_session.get(GithubUser, 5) is None  # stale row reconciled away
    github_user = db_session.query(GithubUser).filter_by(username="recycled").one()
    assert github_user.id == 99
    assert github_user.person_id == person_id


def test_resolve_author_person_creates_person_and_link(db_session):
    person_id = _resolve_author_person(db_session, "newdev", 42)

    assert person_id is not None
    github_user = db_session.query(GithubUser).filter_by(username="newdev").one()
    assert github_user.id == 42
    assert github_user.person_id == person_id
    person = db_session.get(Person, person_id)
    assert person.contact_info["github"] == "newdev"


def test_resolve_author_person_without_numeric_id_creates_person_only(db_session):
    person_id = _resolve_author_person(db_session, "noid", None)

    assert person_id is not None
    assert (
        db_session.query(GithubUser).filter_by(username="noid").one_or_none() is None
    )
    # contact_info carries the handle so a later sync matches the same Person.
    assert db_session.get(Person, person_id).contact_info["github"] == "noid"


def test_resolve_author_person_links_existing_person_by_contact_info(db_session):
    person = Person(
        identifier="existing",
        display_name="Existing",
        contact_info={"github": "existinglogin"},
    )
    db_session.add(person)
    db_session.commit()

    person_id = _resolve_author_person(db_session, "existinglogin", 77)

    assert person_id == person.id  # linked to existing Person, not a duplicate
    github_user = db_session.query(GithubUser).filter_by(username="existinglogin").one()
    assert github_user.person_id == person.id
