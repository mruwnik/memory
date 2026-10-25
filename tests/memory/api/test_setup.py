import logging
from datetime import datetime, timedelta, timezone

import pytest

from memory.api import setup
from memory.api.auth import create_user_session
from memory.api.users import check_single_user
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import (
    ArticleFeed,
    BotUser,
    EmailAccount,
    HumanUser,
    Note,
    OAuthClientInformation,
    OAuthRefreshToken,
    Person,
    UserSession,
)
from memory.common.db.models.users import verify_password
from tests.conftest import unique_sha256

ACCOUNT = {"name": "Owner", "email": "owner@example.com", "password": "longenough1"}


@pytest.fixture
def setup_client(app_client, db_session, monkeypatch):
    """The API with no signed-in user and a fresh setup token."""
    test_client, app = app_client
    monkeypatch.setattr(setup, "_setup_token", None)
    app.dependency_overrides[get_session] = lambda: db_session
    yield test_client
    app.dependency_overrides.pop(get_session, None)


def claim(client, **overrides):
    return client.post("/setup/account", json=ACCOUNT | {"token": setup.setup_token()} | overrides)


def bearer(session_id: str) -> dict:
    return {"Authorization": f"Bearer {session_id}"}


def test_status_reveals_only_whether_an_account_is_needed(setup_client, db_session):
    db_session.add(BotUser.create_with_api_key(name="bot", email="bot@example.com"))
    db_session.commit()
    assert setup_client.get("/setup/status").json() == {"needs_account": True}


def test_status_for_a_stranger_once_an_account_exists(setup_client, admin_user):
    assert setup_client.get("/setup/status").json() == {"needs_account": False}


def test_claiming_creates_a_signed_in_admin(setup_client, db_session):
    response = claim(setup_client)

    assert response.status_code == 200
    user = db_session.query(HumanUser).filter_by(email=ACCOUNT["email"]).one()
    assert user.scopes == ["*"]
    assert verify_password(ACCOUNT["password"], user.password_hash or "")
    assert db_session.query(Person).filter_by(user_id=user.id).count() == 1
    session = db_session.get(UserSession, response.json()["session_id"])
    assert session is not None and session.user_id == user.id


def test_claiming_makes_the_one_human_a_single_user_install_allows(
    setup_client, db_session, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "ENABLED_MODULES", frozenset())
    session_id = claim(setup_client).json()["session_id"]

    with caplog.at_level(logging.WARNING, logger="memory.api.users"):
        check_single_user(db_session, frozenset())
    assert not [r for r in caplog.records if r.name == "memory.api.users"]
    second = setup_client.post(
        "/users",
        json={"name": "Two", "email": "two@example.com", "password": "longenough2", "user_type": "human"},
        headers=bearer(session_id),
    )
    assert second.status_code == 403


@pytest.mark.parametrize("token", ["wrong", "x" * 32])
def test_claiming_needs_the_logged_token(setup_client, db_session, token):
    response = claim(setup_client, token=token)

    assert response.status_code == 403
    assert db_session.query(HumanUser).count() == 0


def test_claiming_is_gone_once_an_account_exists(setup_client, db_session, admin_user):
    response = claim(setup_client)

    assert response.status_code == 404
    assert db_session.query(HumanUser).count() == 1


def test_a_token_claims_only_once(setup_client, db_session):
    token = setup.setup_token()
    assert claim(setup_client, token=token).status_code == 200

    second = claim(setup_client, token=token, email="intruder@example.com")

    assert second.status_code == 404
    assert db_session.query(HumanUser).count() == 1


def test_claiming_checks_password_strength(setup_client, db_session):
    response = claim(setup_client, password="short")

    assert response.status_code == 400
    assert db_session.query(HumanUser).count() == 0


def test_claiming_is_rate_limited(setup_client, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_AUTH", "2/minute")
    statuses = [claim(setup_client, token="wrong").status_code for _ in range(3)]
    assert statuses == [403, 403, 429]


def test_startup_logs_a_token_while_no_account_exists(db_session, monkeypatch, caplog):
    monkeypatch.setattr(setup, "_setup_token", None)
    with caplog.at_level(logging.WARNING, logger=setup.__name__):
        setup.announce_setup_token()
    assert setup.setup_token() in caplog.text


def test_startup_logs_nothing_once_an_account_exists(db_session, admin_user, monkeypatch, caplog):
    monkeypatch.setattr(setup, "_setup_token", None)
    with caplog.at_level(logging.WARNING, logger=setup.__name__):
        setup.announce_setup_token()
    assert caplog.text == ""


def oauth_client(db_session, client_id: str, name: str, redirect_uri: str) -> OAuthClientInformation:
    client = OAuthClientInformation(
        client_id=client_id,
        client_id_issued_at=datetime.now().timestamp(),
        redirect_uris=[redirect_uri],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="read write",
        client_name=name,
    )
    db_session.add(client)
    return client


def grant(db_session, client_id: str, user_id: int, revoked: bool = False, expires_in: timedelta = timedelta(hours=1)) -> None:
    db_session.add(
        OAuthRefreshToken(
            token=f"rt_{unique_sha256().hex()}",
            client_id=client_id,
            user_id=user_id,
            scopes=["read"],
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + expires_in,
            revoked=revoked,
        )
    )


def test_status_for_the_signed_in_user(setup_client, db_session, admin_user, regular_user):
    session_id = str(create_user_session(admin_user.id, db_session))
    db_session.add_all([
        EmailAccount(user_id=admin_user.id, name="Mail", email_address="a@example.com", imap_server="imap.example.com"),
        ArticleFeed(user_id=admin_user.id, url="https://example.com/feed"),
        ArticleFeed(user_id=regular_user.id, url="https://example.com/other"),
        Note(content="hi", modality="note", sha256=unique_sha256("note"), creator_id=admin_user.id),
    ])
    oauth_client(db_session, "ui", "React Memory App", "https://memory.example.com/ui")
    oauth_client(db_session, "cc", "Claude Code", "http://localhost:4321/callback")
    oauth_client(db_session, "old", "Old client", "http://localhost:1/callback")
    oauth_client(db_session, "lapsed", "Lapsed client", "http://localhost:3/callback")
    oauth_client(db_session, "theirs", "Their client", "http://localhost:2/callback")
    db_session.flush()
    grant(db_session, "ui", admin_user.id)
    grant(db_session, "cc", admin_user.id)
    grant(db_session, "cc", admin_user.id)
    grant(db_session, "old", admin_user.id, revoked=True)
    grant(db_session, "lapsed", admin_user.id, expires_in=-timedelta(minutes=1))
    grant(db_session, "theirs", regular_user.id)
    db_session.commit()

    status = setup_client.get("/setup/status", headers=bearer(session_id)).json()

    assert status == {
        "needs_account": False,
        "sources": {
            "email": 1, "google": 0, "github": 0, "slack": 0,
            "discord": 0, "feeds": 1, "notes": 1, "transcripts": 0,
        },
        "mcp_clients": ["Claude Code"],
        "mcp_url": f"{settings.SERVER_URL}/mcp",
    }
