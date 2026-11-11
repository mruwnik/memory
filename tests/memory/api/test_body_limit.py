"""Request body caps, enforced before authentication."""

from memory.common import settings

JSON = {"Content-Type": "application/json"}
# Over the public cap (256 KiB by default).
OVER_PUBLIC_CAP = 1024 * 1024


def poll_response(size: int) -> bytes:
    return b'{"respondent_name": "' + b"a" * size + b'", "availabilities": []}'


def test_public_route_refuses_declared_oversize_body(client):
    body = poll_response(OVER_PUBLIC_CAP)
    response = client.post("/api/polls/respond/no-such-poll", content=body, headers=JSON)
    assert response.status_code == 413


def test_public_route_refuses_streamed_oversize_body(client):
    """A body sent without Content-Length is counted as it arrives."""
    chunk = b"a" * 64 * 1024
    chunks = OVER_PUBLIC_CAP // len(chunk)
    body = (chunk for _ in range(chunks))
    response = client.post("/api/polls/respond/no-such-poll", content=body, headers=JSON)
    assert response.status_code == 413


def test_public_route_under_cap_reaches_handler(client):
    response = client.post("/api/polls/respond/no-such-poll", content=poll_response(10), headers=JSON)
    assert response.status_code == 404


def test_login_form_refuses_oversize_body(client):
    body = b"email=a%40b.c&password=" + b"a" * OVER_PUBLIC_CAP
    response = client.post(
        "/oauth/login", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 413


def test_default_cap_applies_to_authenticated_routes(client, monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_BODY_MAX_BYTES", 1024, raising=False)
    response = client.post("/sessions/ingest/batch", content=b"x" * 2048, headers=JSON)
    assert response.status_code == 413
