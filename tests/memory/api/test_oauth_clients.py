"""What an OAuth client's session may do, and what the login page tells the user
about the client asking for access."""

import uuid
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from memory.agents.runner import mint_session
from memory.api import auth, users
from memory.api.MCP.oauth_provider import make_token_from_data
from memory.common import settings
from memory.common.db.models import APIKey, HumanUser, UserSession
from memory.common.db.models.users import OAuthClientInformation, OAuthState

PASSWORD = "current-pass-123"
THIRD_PARTY = "https://claude.ai/api/mcp/auth_callback"


def web_ui_redirect() -> str:
    return settings.SERVER_URL.rstrip("/") + "/"


def report_redirect() -> str:
    return settings.SERVER_URL.rstrip("/") + "/reports/abc_x.html"


def with_userinfo(url: str, userinfo: str) -> str:
    parts = urlsplit(url)
    return parts._replace(netloc=f"{userinfo}@{parts.netloc}").geturl()


@pytest.fixture
def human(db_session):
    user = HumanUser.create_with_password(email="owner@example.test", name="Owner", password=PASSWORD)
    user.scopes = ["*"]
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def api(db_session, monkeypatch):
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    app = FastAPI()
    app.add_middleware(auth.AuthenticationMiddleware)
    app.include_router(users.router)
    return TestClient(app)


def register_client(db_session, redirect_uri: str, name: str = "Some Client") -> str:
    client_id = uuid.uuid4().hex
    db_session.add(
        OAuthClientInformation(
            client_id=client_id,
            client_id_issued_at=0,
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="read write",
            client_name=name,
        )
    )
    db_session.flush()
    return client_id


def authorization(db_session, client_id: str, redirect_uri: str, user_id: int | None = None) -> OAuthState:
    state = OAuthState(
        state=uuid.uuid4().hex,
        client_id=client_id,
        redirect_uri=redirect_uri,
        redirect_uri_provided_explicitly=True,
        code_challenge="c",
        scopes=["read"],
        user_id=user_id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10),
    )
    db_session.add(state)
    db_session.commit()
    return state


def oauth_token(db_session, user, redirect_uri: str, refreshed: bool) -> str:
    client_id = register_client(db_session, redirect_uri)
    state = authorization(db_session, client_id, redirect_uri, user.id)
    # A refresh exchange issues a session with no authorization of its own.
    state_id = None if refreshed else state.id
    return make_token_from_data(db_session, state_id, user.id, client_id, ["read"]).access_token


def agent_run_token(db_session, user) -> str:
    key = APIKey.create(user_id=user.id, key_type="internal", name="run")
    db_session.add(key)
    db_session.flush()
    session = UserSession(
        user_id=user.id, expires_at=datetime.now() + timedelta(days=1), api_key_id=key.id
    )
    db_session.add(session)
    db_session.commit()
    return session.token


def api_key_token(db_session, user) -> str:
    key = APIKey.create(user_id=user.id, key_type="internal", name="script")
    db_session.add(key)
    db_session.commit()
    return key.key


def setup_token(db_session, user) -> str:
    return auth.create_user_session(user.id, db_session, from_setup=True)


def unmarked_session_token(db_session, user) -> str:
    return auth.create_user_session(user.id, db_session)


def keyless_run_token(db_session, user) -> str:
    return mint_session(user.id, 60, None)


@pytest.mark.parametrize(
    "make_token, status",
    [
        (lambda db, u: oauth_token(db, u, web_ui_redirect(), refreshed=False), 200),
        (lambda db, u: oauth_token(db, u, web_ui_redirect(), refreshed=True), 200),
        (setup_token, 200),
        (lambda db, u: oauth_token(db, u, THIRD_PARTY, refreshed=False), 403),
        (lambda db, u: oauth_token(db, u, THIRD_PARTY, refreshed=True), 403),
        (lambda db, u: oauth_token(db, u, report_redirect(), refreshed=False), 403),
        (lambda db, u: oauth_token(db, u, report_redirect(), refreshed=True), 403),
        (agent_run_token, 403),
        (keyless_run_token, 403),
        (unmarked_session_token, 403),
        (api_key_token, 403),
    ],
    ids=[
        "web-ui", "web-ui-refreshed", "setup", "connector", "connector-refreshed",
        "same-origin-page", "same-origin-page-refreshed", "agent-run", "keyless-agent-run",
        "clientless-not-setup", "api-key",
    ],
)
def test_only_web_ui_logins_create_api_keys(api, db_session, human, make_token, status):
    token = make_token(db_session, human)
    response = api.post(
        f"/api/users/{human.id}/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "k", "scopes": ["read"]},
    )
    assert response.status_code == status


def test_connector_session_still_reads_user_details(api, db_session, human):
    """Connectors keep their full scopes; only key creation is held back."""
    token = oauth_token(db_session, human, THIRD_PARTY, refreshed=False)
    response = api.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


@pytest.fixture
def web_ui_headers(db_session, human) -> dict:
    token = oauth_token(db_session, human, web_ui_redirect(), refreshed=False)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "extra, status, email",
    [
        ({}, 400, "owner@example.test"),
        ({"current_password": "wrong-password"}, 400, "owner@example.test"),
        ({"current_password": PASSWORD}, 200, "new@example.test"),
    ],
)
def test_changing_own_email_needs_current_password(
    api, db_session, human, web_ui_headers, extra, status, email
):
    body = {"email": "new@example.test"} | extra
    response = api.patch(f"/api/users/{human.id}", headers=web_ui_headers, json=body)
    db_session.refresh(human)
    assert (response.status_code, human.email) == (status, email)


def test_admin_changes_another_users_email_without_password(api, db_session, human, web_ui_headers):
    other = HumanUser.create_with_password(email="other@example.test", name="Other", password="x" * 12)
    db_session.add(other)
    db_session.commit()

    response = api.patch(f"/api/users/{other.id}", headers=web_ui_headers, json={"email": "moved@example.test"})

    assert response.status_code == 200


def test_unchanged_email_needs_no_password(api, human, web_ui_headers):
    response = api.patch(
        f"/api/users/{human.id}", headers=web_ui_headers, json={"email": human.email, "name": "Renamed"}
    )
    assert response.status_code == 200


def login_page(client, db_session, redirect_uri: str, name: str):
    client_id = register_client(db_session, redirect_uri, name)
    state = authorization(db_session, client_id, redirect_uri)
    return client.get("/oauth/login", params={"state": state.state, "client_id": client_id})


def test_login_page_names_a_third_party_client(client, db_session):
    response = login_page(client, db_session, "https://evil.example/cb", '<script>alert(1)</script>"Bot')

    assert "&lt;script&gt;alert(1)&lt;/script&gt;&#34;Bot" in response.text
    assert "<script>alert(1)" not in response.text
    assert "<strong>evil.example</strong> wants access to your Memory" in response.text


def test_login_page_for_the_web_ui_is_unchanged(client, db_session):
    response = login_page(client, db_session, web_ui_redirect(), "Memory web UI")

    assert "Access your Memory knowledge base" in response.text
    assert "wants access" not in response.text


@pytest.mark.parametrize(
    "path, host_shown",
    [
        ("/reports/abc_x.html", True),
        ("/?next=1", True),
        ("/", False),
        ("/ui/", False),
    ],
)
def test_login_page_names_a_client_redirecting_elsewhere_on_this_server(
    client, db_session, path, host_shown
):
    """Only the web UI's own callback passes for the web UI, whatever the client calls itself."""
    redirect = settings.SERVER_URL.rstrip("/") + path
    response = login_page(client, db_session, redirect, "React Memory App")

    assert ("wants access to your Memory" in response.text) == host_shown


def registration(redirect_uri: str) -> dict:
    return {
        "client_name": "React Memory App",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": "read write",
        "token_endpoint_auth_method": "none",
    }


@pytest.mark.parametrize(
    "path, status",
    [
        ("/", 201),
        ("/reports/abc_x.html", 400),
        ("/files/x.html", 400),
        ("/callback", 400),
        ("/?next=/reports/abc_x.html", 400),
        ("/#frag", 400),
    ],
)
def test_registration_on_this_server_only_for_the_web_ui_callback(client, path, status):
    response = client.post("/register", json=registration(settings.SERVER_URL.rstrip("/") + path))

    assert response.status_code == status, response.text


@pytest.mark.parametrize(
    "path, lands_on",
    [("/", "/oauth/login?state="), ("/reports/abc_x.html", "/reports/abc_x.html?error=unauthorized_client&")],
)
def test_authorize_refuses_stored_client_redirecting_elsewhere_on_this_server(
    client, db_session, path, lands_on
):
    redirect = settings.SERVER_URL.rstrip("/") + path
    client_id = register_client(db_session, redirect, "React Memory App")
    db_session.commit()

    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
            "state": "s",
        },
        follow_redirects=False,
    )

    assert response.headers["location"].startswith(settings.SERVER_URL.rstrip("/") + lands_on)


@pytest.mark.parametrize("userinfo", ["x", "x:y", "user%40example.com"])
@pytest.mark.parametrize("path", ["/", "/reports/abc_x.html", "/files/x.html", "/anything?next=1"])
def test_registration_refuses_userinfo_on_this_server(client, userinfo, path):
    redirect = with_userinfo(settings.SERVER_URL.rstrip("/") + path, userinfo)

    response = client.post("/register", json=registration(redirect))

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_redirect_uri"


def test_registration_refuses_userinfo_on_an_allowed_host(client):
    response = client.post("/register", json=registration(with_userinfo(THIRD_PARTY, "x")))

    assert response.status_code == 400, response.text


@pytest.mark.parametrize("path", ["/", "/reports/abc_x.html"])
def test_authorize_refuses_stored_client_with_userinfo(client, db_session, path):
    redirect = with_userinfo(settings.SERVER_URL.rstrip("/") + path, "x")
    client_id = register_client(db_session, redirect, "React Memory App")
    db_session.commit()

    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
            "state": "s",
        },
        follow_redirects=False,
    )

    assert "/oauth/login" not in response.headers.get("location", "")
    assert "error=unauthorized_client" in response.headers["location"]


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("https://memory.example.com/", True),
        ("https://memory.example.com:443/", True),
        ("HTTPS://Memory.Example.com/ui", True),
        ("https://x@memory.example.com/", False),
        ("https://memory.example.com:8443/", False),
        ("https://memory.example.com.evil.com/", False),
        ("http://memory.example.com/", False),
    ],
)
def test_web_ui_callback_compares_the_origin_a_browser_loads(monkeypatch, uri, expected):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    monkeypatch.setattr(settings, "ALLOW_LOCALHOST_CORS", False)

    assert auth.is_web_ui_callback(uri) is expected
