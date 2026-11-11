"""Every route counts against a per-client rate limit, nested and mounted ones included."""

import re

import pytest
from fastapi.routing import _IncludedRouter
from starlette.routing import Mount

from memory.api import request_limits
from memory.common import settings
from memory.common.db.models import OAuthClientInformation, OAuthState

LIMIT_SETTINGS = [
    "API_RATE_LIMIT_DEFAULT",
    "API_RATE_LIMIT_MCP",
    "API_RATE_LIMIT_WEBHOOKS",
    "API_RATE_LIMIT_OAUTH_REGISTER",
    "API_RATE_LIMIT_OAUTH_AUTHORIZE",
]
REGISTRATION = {
    "client_name": "test",
    "redirect_uris": ["http://localhost:1/cb"],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
}


def route_paths(routes, prefix=""):
    """Every concrete route path under ``routes``, through included routers and mounts."""
    for route in routes:
        if isinstance(route, _IncludedRouter):
            yield from route_paths(
                route.original_router.routes, prefix + route.include_context.prefix
            )
        elif isinstance(route, Mount):
            yield from route_paths(route.routes, prefix + route.path)
        else:
            yield prefix + route.path


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
def app_paths(app_client):
    _, app = app_client
    return sorted({concrete(p) for p in route_paths(app.routes)})


@pytest.fixture
def one_per_minute(monkeypatch):
    for name in LIMIT_SETTINGS:
        monkeypatch.setattr(settings, name, "1/minute")


def test_route_walk_reaches_nested_and_mounted_routes(app_paths):
    expected = {"/auth/me", "/api/jobs", "/files/1", "/register", "/authorize", "/mcp", "/health"}

    assert expected <= set(app_paths)


def test_every_route_is_limited_or_exempt(client, app_paths, one_per_minute):
    """OPTIONS reaches no handler, so a second one per route shows the limit alone."""

    def second_status(path: str) -> int:
        request_limits.reset()
        client.options(path)
        return client.options(path).status_code

    statuses = {path: second_status(path) for path in app_paths}

    assert {p for p, s in statuses.items() if s != 429} == {"/health"}


@pytest.mark.parametrize("path", ["/auth/me", "/api/jobs", "/setup/status", "/files/nope"])
def test_default_limit_applies_to_router_and_app_routes(client, path):
    statuses = [client.get(path).status_code for _ in range(101)]

    assert 429 not in statuses[:100]
    assert statuses[100] == 429


def test_limited_response_says_when_to_retry(client, one_per_minute):
    client.get("/auth/me")
    response = client.get("/auth/me")

    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.json()["error"] == "Rate limit exceeded"


def test_health_is_never_limited(client, one_per_minute):
    statuses = {client.get("/health").status_code for _ in range(5)}

    assert 429 not in statuses


@pytest.mark.parametrize(
    "path, buckets",
    [
        ("/auth/me", ["default"]),
        ("/register", ["default", "/register"]),
        ("/authorize", ["default", "/authorize"]),
        ("/mcp", ["mcp"]),
        ("/mcp/", ["mcp"]),
        ("/github/webhook/3", ["webhooks"]),
        ("/slack/events/A1", ["webhooks"]),
        ("/discord/events/9", ["webhooks"]),
        ("/transcripts/ingest/4", ["webhooks"]),
        ("/slack/events/A1/extra", ["default"]),
        ("/mcpx", ["default"]),
        ("/health", []),
    ],
)
def test_limit_buckets_by_path(path, buckets):
    assert [bucket for bucket, _ in request_limits.limit_specs(path)] == buckets


def test_anonymous_registration_is_limited_per_hour(client, db_session):
    before = db_session.query(OAuthClientInformation).count()

    statuses = [client.post("/register", json=REGISTRATION).status_code for _ in range(25)]

    assert statuses == [201] * 20 + [429] * 5
    assert db_session.query(OAuthClientInformation).count() == before + 20


def test_authorization_requests_are_limited(client, db_session):
    client_id = client.post("/register", json=REGISTRATION).json()["client_id"]
    params = {
        "client_id": client_id,
        "redirect_uri": "http://localhost:1/cb",
        "response_type": "code",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
    }
    before = db_session.query(OAuthState).count()

    statuses = [
        client.get(
            "/authorize", params=params | {"state": f"s-{i}"}, follow_redirects=False
        ).status_code
        for i in range(15)
    ]

    assert statuses == [302] * 10 + [429] * 5
    assert db_session.query(OAuthState).count() == before + 10


def test_registration_without_a_name_is_accepted(client, db_session):
    response = client.post("/register", json={"redirect_uris": ["http://localhost:1/cb"]})

    assert response.status_code == 201
    stored = db_session.get(OAuthClientInformation, response.json()["client_id"])
    assert stored.client_name == ""


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "credential",
    [{"headers": bearer("tok-user")}, {"cookies": {settings.SESSION_COOKIE_NAME: "tok-user"}}],
    ids=["bearer", "session cookie"],
)
def test_anonymous_traffic_does_not_spend_a_signed_in_clients_budget(client, one_per_minute, credential):
    anonymous = [client.get("/auth/me").status_code for _ in range(2)]
    signed_in = [client.get("/auth/me", **credential).status_code for _ in range(2)]

    assert ([s == 429 for s in anonymous], [s == 429 for s in signed_in]) == ([False, True], [False, True])


def test_each_credential_has_its_own_default_budget(client, one_per_minute):
    statuses = [client.get("/auth/me", headers=bearer(token)).status_code for token in ("a", "b", "a")]

    assert [s == 429 for s in statuses] == [False, False, True]


def test_made_up_tokens_share_the_addresss_ceiling(client, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_CREDENTIALED_ADDRESS", "3/minute")

    statuses = [client.get("/auth/me", headers=bearer(f"made-up-{i}")).status_code for i in range(4)]

    assert [s == 429 for s in statuses] == [False, False, False, True]


ADDRESS = "10.0.0.1"


@pytest.mark.parametrize(
    "path, keys",
    [
        ("/auth/me", ["credential", ADDRESS]),
        ("/register", ["credential", ADDRESS, ADDRESS]),
        ("/mcp", [ADDRESS]),
        ("/github/webhook/3", [ADDRESS]),
        ("/health", []),
    ],
)
def test_only_the_default_bucket_is_keyed_by_credential(path, keys):
    keyed = request_limits.keyed_limits(path, ADDRESS, "tok")

    assert [key.split(":")[0] for _, _, key in keyed] == keys
