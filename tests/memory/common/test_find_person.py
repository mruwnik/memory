"""Tests for memory.common.people lookup helpers."""

import pytest

from memory.common.db.models import Person
from memory.common.people import (
    find_person_by_email,
    find_person_by_name,
    find_or_create_person,
)


@pytest.fixture
def alice_person(db_session):
    person = Person(
        identifier="alice",
        display_name="Alice",
        aliases=["Alice"],
        contact_info={"email": "alice@company.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


def test_find_person_by_name_email_uses_exact_match(db_session, alice_person):
    """Regression: an email-shaped `name` argument must NOT be treated
    as a substring pattern. Previously, find_person_by_name wrapped the
    input in `%...%` wildcards, which meant a query for
    `alice@company.com.attacker.tld` would match Alice's record. That
    breaks user signup auto-linking — an attacker who registers with a
    crafted email could inherit an existing Person's identity / team
    memberships.
    """
    # The attacker's email contains alice@company.com as a substring.
    found = find_person_by_name(db_session, "alice@company.com.attacker.tld")
    # Must NOT match Alice's record.
    assert found is None


def test_find_person_by_name_email_exact_still_matches(db_session, alice_person):
    """Exact (case-insensitive) email lookups via find_person_by_name
    must still resolve. Casing is normalised to lower."""
    found = find_person_by_name(db_session, "ALICE@company.com")
    assert found is not None
    assert found.id == alice_person.id


def test_find_or_create_person_does_not_inherit_via_substring(db_session, alice_person):
    """End-to-end regression for the auto-link flow:
    find_or_create_person must NOT match an existing Person by email
    substring when called with a similarly-shaped input."""
    # find_or_create_person without explicit email; only name=email-shaped.
    person, created = find_or_create_person(
        db_session,
        name="alice@company.com.attacker.tld",
        create_if_missing=False,
    )
    # Must not have inherited Alice's record.
    assert person is None
    assert created is False


def test_find_person_by_email_exact_match_baseline(db_session, alice_person):
    """Sanity-check find_person_by_email already uses exact-match
    semantics (no wildcard wrapping) so the redirect from
    find_person_by_name preserves the right behaviour."""
    assert find_person_by_email(db_session, "alice@company.com") is alice_person
    # A substring-like attacker query must miss.
    assert find_person_by_email(db_session, "alice@company.com.attacker.tld") is None
