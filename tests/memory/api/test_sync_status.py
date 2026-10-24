"""Tests for the per-tab failing-sync counts behind the Sources tab labels."""

from datetime import datetime, timezone

import pytest

from memory.common.db.models import HumanUser
from memory.common.db.models.sources import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    GithubAccount,
    GithubRepo,
    GoogleAccount,
    GoogleFolder,
    TranscriptAccount,
)

FAILING = {
    "sync_error": "boom",
    "sync_failing_since": datetime(2026, 10, 20, tzinfo=timezone.utc),
}

NO_FAILURES = {
    "accounts": 0,
    "email": 0,
    "feeds": 0,
    "github": 0,
    "drive": 0,
    "calendar": 0,
    "transcripts": 0,
    "slack": 0,
}


def email_account(user_id, address, **status):
    return EmailAccount(
        user_id=user_id,
        name=address,
        email_address=address,
        account_type="imap",
        imap_server="imap.example.com",
        username="u",
        password="p",
        **status,
    )


def seed_failures(db_session, user_id):
    """Two failing email accounts and one healthy; one failing source on each other tab."""
    github = GithubAccount(user_id=user_id, name="gh", auth_type="pat", access_token="t")
    github.repos.append(GithubRepo(owner="o", name="r", **FAILING))
    github.repos.append(GithubRepo(owner="o", name="ok"))
    google = GoogleAccount(user_id=user_id, name="g", email=f"g{user_id}@example.com", **FAILING)
    google.folders.append(GoogleFolder(folder_id="f", folder_name="F", **FAILING))
    db_session.add_all(
        [
            email_account(user_id, f"a{user_id}@example.com", **FAILING),
            email_account(user_id, f"b{user_id}@example.com", **FAILING),
            email_account(user_id, f"c{user_id}@example.com"),
            ArticleFeed(user_id=user_id, url=f"https://e.com/{user_id}.xml", **FAILING),
            github,
            google,
            CalendarAccount(user_id=user_id, name="cal", calendar_type="google", **FAILING),
            TranscriptAccount(user_id=user_id, name="t", provider="fireflies", **FAILING),
        ]
    )
    db_session.commit()


def test_counts_failing_sources_per_tab(client, user, db_session):
    seed_failures(db_session, user.id)

    response = client.get("/sync-failures")

    assert response.status_code == 200
    assert response.json() == {
        **NO_FAILURES,
        "accounts": 1,
        "email": 2,
        "feeds": 1,
        "github": 1,
        "drive": 1,
        "calendar": 1,
        "transcripts": 1,
    }


def test_healthy_sources_count_zero(client, user, db_session):
    db_session.add(email_account(user.id, "ok@example.com"))
    db_session.commit()

    assert client.get("/sync-failures").json() == NO_FAILURES


def test_admin_can_scope_to_one_user(client, user, db_session):
    other = HumanUser(id=999, email="other@example.com", name="O", password_hash="x")
    db_session.add(other)
    db_session.flush()
    seed_failures(db_session, user.id)
    db_session.add(email_account(other.id, "theirs@example.com", **FAILING))
    db_session.commit()

    assert client.get(f"/sync-failures?user_id={other.id}").json() == {
        **NO_FAILURES,
        "email": 1,
        "feeds": 1,  # admins see every feed in the tab whichever user is selected
    }


@pytest.mark.parametrize("query", ["", "?user_id=999"])
def test_non_admin_sees_only_their_own(regular_client, user, db_session, query):
    other = HumanUser(id=999, email="other@example.com", name="O", password_hash="x")
    db_session.add(other)
    db_session.flush()
    db_session.add(email_account(user.id, "mine@example.com", **FAILING))
    db_session.add(email_account(other.id, "theirs@example.com", **FAILING))
    db_session.commit()

    assert regular_client.get(f"/sync-failures{query}").json() == {**NO_FAILURES, "email": 1}
