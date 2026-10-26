"""Tests for the per-tab counts and failing-sync counts behind the Sources tab labels."""

import json
from datetime import datetime, timezone

import pytest
import redis

from memory.common import settings
from memory.common.db.models import Book, BookSection, HumanUser, Note, Person, Team
from memory.common.db.models.discord import DiscordBot
from memory.common.db.models.secrets import Secret
from memory.common.db.models.slack import SlackApp, SlackUserCredentials, SlackWorkspace
from memory.common.db.models.source_items import Photo
from memory.common.db.models.sources import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    GithubAccount,
    GithubRepo,
    GoogleAccount,
    GoogleFolder,
    Project,
    TranscriptAccount,
    project_teams,
    team_members,
)
from memory.common.notes_git import GIT_SYNC_STATUS_KEY

FAILING = {
    "sync_error": "boom",
    "sync_failing_since": datetime(2026, 10, 20, tzinfo=timezone.utc),
}

TABS = [
    "accounts",
    "email",
    "feeds",
    "github",
    "drive",
    "calendar",
    "transcripts",
    "books",
    "photos",
    "notes",
    "discord",
    "slack",
    "secrets",
]


def tabs(**overrides: tuple[int, int]) -> dict:
    """The expected response: every tab empty unless given as (count, failing)."""
    counts = {tab: (0, 0) for tab in TABS} | overrides
    return {tab: {"count": count, "failing": failing} for tab, (count, failing) in counts.items()}


@pytest.fixture
def other(db_session):
    other = HumanUser(id=999, email="other@example.com", name="O", password_hash="x")
    db_session.add(other)
    db_session.flush()
    return other


@pytest.fixture
def member_client(client, user, db_session):
    """The client, signed in as a real non-admin user: access checks need real project roles."""
    user.scopes = ["read", "write"]
    db_session.flush()
    return client


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


def seed_sources(db_session, user_id):
    """Three email accounts (two failing), and a mix of failing and healthy sources on the other tabs."""
    github = GithubAccount(user_id=user_id, name="gh", auth_type="pat", access_token="t")
    github.repos.append(GithubRepo(owner=f"o{user_id}", name="r", **FAILING))
    github.repos.append(GithubRepo(owner=f"o{user_id}", name="ok"))
    google = GoogleAccount(user_id=user_id, name="g", email=f"g{user_id}@example.com", **FAILING)
    google.folders.append(GoogleFolder(folder_id="f", folder_name="F", **FAILING))
    google.folders.append(GoogleFolder(folder_id="ok", folder_name="OK"))
    bot = DiscordBot(id=user_id, name="bot")
    bot.created_by_user_id = user_id
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
            bot,
            Secret(user_id=user_id, name="s", encrypted_value=b"x"),
        ]
    )
    db_session.commit()


SEEDED = tabs(
    accounts=(2, 1),
    email=(3, 2),
    feeds=(1, 1),
    github=(2, 1),
    drive=(2, 2),  # the failing Google account is shown, and flagged, on the Drive tab too
    calendar=(1, 1),
    transcripts=(1, 1),
    discord=(1, 0),
    secrets=(1, 0),
)


def test_counts_sources_and_failures_per_tab(client, user, db_session):
    seed_sources(db_session, user.id)

    response = client.get("/source-tabs")

    assert response.status_code == 200
    assert response.json() == SEEDED


def test_admin_can_scope_to_one_user(client, user, other, db_session):
    seed_sources(db_session, user.id)
    db_session.add(email_account(other.id, "theirs@example.com", **FAILING))
    db_session.commit()

    assert client.get(f"/source-tabs?user_id={other.id}").json() == tabs(
        email=(1, 1),
        feeds=(1, 1),  # admins see every feed in the tab whichever user is selected
        secrets=(1, 0),  # the Secrets tab always lists the viewer's own
    )


def test_admin_sees_everyone_without_a_user_filter(client, user, other, db_session):
    seed_sources(db_session, user.id)
    seed_sources(db_session, other.id)

    assert client.get("/source-tabs").json() == {
        tab: {"count": 2 * c["count"], "failing": 2 * c["failing"]} for tab, c in SEEDED.items()
    } | {"secrets": {"count": 1, "failing": 0}}


@pytest.mark.parametrize("query", ["", "?user_id=999"])
def test_non_admin_sees_only_their_own(member_client, user, other, db_session, query):
    seed_sources(db_session, user.id)
    seed_sources(db_session, other.id)

    assert member_client.get(f"/source-tabs{query}").json() == SEEDED


def book_with_section(db_session, title: str, **access) -> Book:
    book = Book(title=title)
    db_session.add(book)
    db_session.flush()
    db_session.add_all(
        BookSection(book_id=book.id, section_title=f"{title} {n}", modality="book", sha256=f"{title}-{n}".encode(), **access)
        for n in range(2)
    )
    return book


@pytest.mark.parametrize("scopes, books", [(["read", "write"], 1), (["*"], 2)])
def test_books_count_only_those_with_a_readable_section(client, user, other, db_session, scopes, books):
    book_with_section(db_session, "Mine", creator_id=user.id)
    book_with_section(db_session, "Theirs", creator_id=other.id)
    user.scopes = scopes
    db_session.commit()

    assert client.get("/source-tabs").json()["books"] == {"count": books, "failing": 0}


def photo(n: int, **access) -> Photo:
    return Photo(sha256=f"photo-{n}".encode(), modality="photo", filename=f"{n}.jpg", **access)


def test_photos_count_only_those_the_viewer_can_see(member_client, user, other, db_session):
    db_session.add_all(
        [
            photo(1, creator_id=user.id),
            photo(2, creator_id=other.id, sensitivity="public"),
            photo(3, creator_id=other.id),
            photo(4, creator_id=user.id, sensitivity="hidden"),
        ]
    )
    db_session.commit()

    assert member_client.get("/source-tabs").json()["photos"] == {"count": 2, "failing": 0}


def test_admin_counts_every_photo_but_hidden(client, user, other, db_session):
    db_session.add_all(
        [
            photo(1, creator_id=user.id),
            photo(2, creator_id=other.id),
            photo(3, creator_id=other.id, sensitivity="hidden"),
        ]
    )
    db_session.commit()

    assert client.get("/source-tabs").json()["photos"] == {"count": 2, "failing": 0}


def test_photos_visible_through_a_project_role(member_client, user, other, db_session):
    project = Project(title="P", state="open")
    team = Team(name="T", slug="t", is_active=True)
    user.person = Person(identifier="me", display_name="Me")
    db_session.add_all([project, team])
    db_session.flush()
    db_session.execute(team_members.insert().values(team_id=team.id, person_id=user.person.id, role="member"))
    db_session.execute(project_teams.insert().values(project_id=project.id, team_id=team.id))
    db_session.add_all(
        [
            photo(1, creator_id=other.id, project_id=project.id, sensitivity="basic"),
            photo(2, creator_id=other.id, project_id=project.id, sensitivity="confidential"),
        ]
    )
    db_session.commit()

    assert member_client.get("/source-tabs").json()["photos"] == {"count": 1, "failing": 0}


@pytest.mark.parametrize("query, discord, slack", [("", 2, (2, 1)), ("?user_id=1", 1, (1, 0))])
def test_admin_sees_every_bot_and_workspace_without_a_user_filter(
    client, user, db_session, query, discord, slack
):
    """Bots and workspaces with nobody connected only show in the admin's unfiltered view."""
    mine = DiscordBot(id=1, name="mine")
    mine.created_by_user_id = user.id
    app = SlackApp(client_id="c", name="app", setup_state="live")
    connected = SlackWorkspace(id="T1", name="connected")
    orphan = SlackWorkspace(id="T2", name="orphan")
    orphan.record_sync("boom")
    db_session.add_all([mine, DiscordBot(id=2, name="orphan"), app, connected, orphan])
    db_session.flush()
    db_session.add(SlackUserCredentials(slack_app_id=app.id, workspace_id="T1", user_id=user.id))
    db_session.commit()

    tabs_seen = client.get(f"/source-tabs{query}").json()

    assert tabs_seen["discord"] == {"count": discord, "failing": 0}
    assert tabs_seen["slack"] == {"count": slack[0], "failing": slack[1]}


def note(n: int, **access) -> Note:
    return Note(sha256=f"note-{n}".encode(), modality="text", filename=f"notes/{n}.md", **access)


def record_sync(ok: bool):
    status = {"ok": ok, "failed_step": None if ok else "push", "hint": None, "at": "2026-10-25T10:00:00+00:00"}
    redis.from_url(settings.REDIS_URL).set(GIT_SYNC_STATUS_KEY, json.dumps(status))


def test_notes_count_only_those_the_viewer_can_see(member_client, user, other, db_session):
    db_session.add_all([note(1, creator_id=user.id), note(2, creator_id=other.id)])
    db_session.commit()

    assert member_client.get("/source-tabs").json()["notes"] == {"count": 1, "failing": 0}


@pytest.mark.parametrize("ok, failing", [(True, 0), (False, 1)])
def test_a_failed_notes_sync_flags_the_tab_for_admins(client, ok, failing):
    record_sync(ok)

    assert client.get("/source-tabs").json()["notes"] == {"count": 0, "failing": failing}


def test_members_cant_fix_the_vault_so_its_failure_isnt_flagged_for_them(member_client):
    record_sync(False)

    assert member_client.get("/source-tabs").json()["notes"] == {"count": 0, "failing": 0}
