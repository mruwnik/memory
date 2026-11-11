"""Every response carries anti-framing and baseline headers; logout only answers POST."""

import pytest
from fastapi.testclient import TestClient

from memory.common import settings
from memory.common.db.models import Report, UserSession

PAGE = "text/html,application/xhtml+xml"


@pytest.fixture
def spa(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>spa</title>")
    monkeypatch.setattr(settings, "STATIC_DIR", tmp_path)


def headers_of(response) -> dict[str, str]:
    names = ("x-frame-options", "content-security-policy", "referrer-policy", "x-content-type-options")
    return {name: response.headers.get(name) for name in names}


DENY = {
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "referrer-policy": "same-origin",
    "x-content-type-options": "nosniff",
}


@pytest.mark.parametrize(
    "path, accept",
    [
        ("/oauth/login?state=whatever", PAGE),
        ("/", PAGE),
        ("/search", PAGE),
        ("/no-such-endpoint", "application/json"),
        ("/auth/me", "application/json"),
    ],
)
def test_pages_and_api_refuse_framing(client: TestClient, spa, path, accept):
    response = client.get(path, headers={"accept": accept})

    assert response.status_code < 500, response.text
    assert headers_of(response) == DENY


def test_rate_limited_response_carries_headers(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_OAUTH_REGISTER", "1/minute")
    responses = [client.post("/register", json={}) for _ in range(2)]

    assert (responses[1].status_code, headers_of(responses[1])) == (429, DENY)


def test_report_keeps_sandbox_and_allows_same_origin_framing(client: TestClient, db_session):
    (settings.REPORT_STORAGE_DIR / "framed.html").write_text("<h1>Report</h1>")
    db_session.add(
        Report(
            modality="doc",
            mime_type="text/html",
            report_title="Framed",
            report_format="html",
            filename="reports/framed.html",
            sha256=b"\x07" * 32,
        )
    )
    db_session.commit()

    response = client.get("/reports/framed.html")

    assert response.status_code == 200, response.text
    assert headers_of(response) == {
        "x-frame-options": "SAMEORIGIN",
        "content-security-policy": (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; sandbox; "
            "frame-ancestors 'self'"
        ),
        "referrer-policy": "no-referrer",
        "x-content-type-options": "nosniff",
    }


@pytest.mark.parametrize("method, survives", [("GET", True), ("POST", False)])
def test_only_post_logs_out(client: TestClient, db_session, user_session, method, survives):
    session_id = user_session.id
    db_session.expunge_all()

    client.request(method, "/auth/logout", headers={"Authorization": "Bearer test-session-token"})

    assert (db_session.get(UserSession, session_id) is not None) == survives
