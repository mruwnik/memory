from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from starlette.requests import Request

from memory.api import signed_tokens
from memory.api.MCP.base import login_form
from memory.api.password_reset import TOKEN_DOMAIN, make_reset_token
from memory.common import settings
from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.db.models import EmailAccount, HumanUser, UserSession
from memory.common.db.models.users import hash_password, verify_password

SENDER = "noreply@example.com"


@pytest.fixture
def mail_configured(db_session, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "PASSWORD_RESET_FROM_EMAIL", SENDER)
    monkeypatch.setattr(settings, "TRANSFER_TOKEN_SECRET", "s" * 32)
    account = EmailAccount(
        user_id=admin_user.id,
        name="Sender",
        email_address=SENDER,
        imap_server="imap.example.com",
        send_enabled=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def reset_user(db_session):
    target = HumanUser.create_with_password("forgetful@example.com", "Forgetful", "oldpass123")
    db_session.add(target)
    db_session.commit()
    return target


def reset_requests(no_celery_dispatch):
    return [c for c in no_celery_dispatch.call_args_list if c.args[0] == SEND_NOTIFICATION]


def test_available_when_a_send_enabled_sender_exists(client, mail_configured):
    assert client.get("/auth/password-reset").json() == {"available": True}


@pytest.mark.parametrize(
    "setting, send_enabled, secret",
    [
        ("", True, "s" * 32),
        ("other@example.com", True, "s" * 32),
        (SENDER, False, "s" * 32),
        (SENDER, True, None),
    ],
)
def test_unavailable_without_a_usable_sender(
    client, db_session, mail_configured, monkeypatch, setting, send_enabled, secret
):
    monkeypatch.setattr(settings, "PASSWORD_RESET_FROM_EMAIL", setting)
    monkeypatch.setattr(settings, "TRANSFER_TOKEN_SECRET", secret)
    mail_configured.send_enabled = send_enabled
    db_session.commit()

    assert client.get("/auth/password-reset").json() == {"available": False}
    response = client.post("/auth/password-reset/request", json={"email": "forgetful@example.com"})
    assert response.status_code == 404


def test_unavailable_when_the_sender_is_not_admin_owned(
    client, db_session, mail_configured, regular_user, caplog
):
    mail_configured.user_id = regular_user.id
    db_session.commit()

    assert client.get("/auth/password-reset").json() == {"available": False}
    response = client.post("/auth/password-reset/request", json={"email": "forgetful@example.com"})
    assert response.status_code == 404
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(f"account_id={mail_configured.id}" in w for w in warnings)
    assert not any(SENDER in w for w in warnings)


def test_request_emails_a_fragment_link_from_the_sender(
    client, mail_configured, reset_user, no_celery_dispatch
):
    response = client.post("/auth/password-reset/request", json={"email": reset_user.email})

    assert response.json() == {"status": "ok"}
    [sent] = reset_requests(no_celery_dispatch)
    channel, target, body, owner_id, _subject, data = sent.kwargs["args"]
    assert (channel, target, owner_id) == ("email", reset_user.email, mail_configured.user_id)
    assert (data["from_address"], data["password_reset"]) == (SENDER, True)
    assert f"{settings.SERVER_URL}/ui/reset-password#token=" in body


def test_request_for_unknown_email_looks_the_same(client, mail_configured, reset_user, no_celery_dispatch):
    known = client.post("/auth/password-reset/request", json={"email": reset_user.email})
    unknown = client.post("/auth/password-reset/request", json={"email": "nobody@example.com"})

    assert (unknown.status_code, unknown.json()) == (known.status_code, known.json())
    assert len(reset_requests(no_celery_dispatch)) == 1


def test_passwordless_users_get_no_link(client, db_session, mail_configured, no_celery_dispatch):
    keyless = HumanUser(name="Keys Only", email="keys@example.com", password_hash=None)
    db_session.add(keyless)
    db_session.commit()

    response = client.post("/auth/password-reset/request", json={"email": keyless.email})
    assert response.json() == {"status": "ok"}
    assert reset_requests(no_celery_dispatch) == []

    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": make_reset_token(keyless), "new_password": "newpass123"},
    )
    assert response.status_code == 400


def test_request_is_rate_limited_per_email(client, mail_configured, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_ENABLED", True)
    statuses = [
        client.post("/auth/password-reset/request", json={"email": "victim@example.com"}).status_code
        for _ in range(4)
    ]
    assert statuses == [200, 200, 200, 429]


def test_confirm_sets_the_password_and_revokes_every_session(
    client, db_session, mail_configured, reset_user
):
    db_session.add(UserSession(user_id=reset_user.id, expires_at=datetime.now() + timedelta(days=1)))
    db_session.commit()
    token = make_reset_token(reset_user)

    response = client.post(
        "/auth/password-reset/confirm", json={"token": token, "new_password": "newpass123"}
    )

    assert response.json() == {"status": "password_reset"}
    db_session.refresh(reset_user)
    assert verify_password("newpass123", reset_user.password_hash or "")
    assert db_session.query(UserSession).filter(UserSession.user_id == reset_user.id).count() == 0


def test_a_link_works_only_once(client, mail_configured, reset_user):
    token = make_reset_token(reset_user)
    body = {"token": token, "new_password": "newpass123"}

    assert client.post("/auth/password-reset/confirm", json=body).status_code == 200
    assert client.post("/auth/password-reset/confirm", json=body).status_code == 400


def test_a_password_change_kills_outstanding_links(client, db_session, mail_configured, reset_user):
    token = make_reset_token(reset_user)
    reset_user.password_hash = hash_password("changedelsewhere")
    db_session.commit()

    response = client.post(
        "/auth/password-reset/confirm", json={"token": token, "new_password": "newpass123"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "make_token",
    [
        lambda uid: "garbage",
        lambda uid: signed_tokens.sign({"uid": uid, "pw": "x"}, domain=TOKEN_DOMAIN, ttl_seconds=60),
        lambda uid: signed_tokens.sign({"uid": str(uid)}, domain=TOKEN_DOMAIN, ttl_seconds=60),
        lambda uid: signed_tokens.sign({"uid": uid}, domain="ingest", ttl_seconds=60),
    ],
)
def test_confirm_rejects_bad_tokens(client, db_session, mail_configured, reset_user, make_token):
    old_hash = reset_user.password_hash
    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": make_token(reset_user.id), "new_password": "newpass123"},
    )

    assert response.status_code == 400
    db_session.refresh(reset_user)
    assert reset_user.password_hash == old_hash


def test_expired_token_is_rejected(client, mail_configured, reset_user):
    with patch("memory.api.password_reset.TOKEN_TTL_SECONDS", -10):
        token = make_reset_token(reset_user)
    response = client.post(
        "/auth/password-reset/confirm", json={"token": token, "new_password": "newpass123"}
    )
    assert response.status_code == 400


def test_confirm_rejects_a_weak_password(client, mail_configured, reset_user):
    token = make_reset_token(reset_user)
    response = client.post("/auth/password-reset/confirm", json={"token": token, "new_password": "short"})
    assert response.status_code == 400


def login_page_html() -> str:
    request = Request({"type": "http", "method": "GET", "path": "/oauth/login", "headers": [], "query_string": b""})
    return bytes(login_form(request, {}, None).body).decode()


def test_login_page_links_to_reset_only_when_available(db_session, mail_configured, monkeypatch):
    assert "/ui/forgot-password" in login_page_html()

    monkeypatch.setattr(settings, "PASSWORD_RESET_FROM_EMAIL", "")
    assert "/ui/forgot-password" not in login_page_html()
