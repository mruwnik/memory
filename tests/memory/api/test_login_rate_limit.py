"""Login rate limits: failed attempts can't lock an account's owner out."""

import fakeredis
import pytest
from fastapi.testclient import TestClient

from memory.api.MCP import base
from memory.common import rate_limit, settings
from memory.common.db.models import HumanUser
from memory.common.db.models.users import hash_password

PASSWORD = "correct horse battery staple"
EMAIL = "victim@example.com"
OWNER_IP = "198.51.100.7"
ATTACKER_IP = "203.0.113.66"


@pytest.fixture
def victim(db_session):
    user = HumanUser(name="Victim", email=EMAIL, password_hash=hash_password(PASSWORD))
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def fake_redis(monkeypatch):
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(rate_limit, "rate_limit_redis", lambda: redis)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_AUTH", "3/minute")
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXIES", "")
    return redis


@pytest.fixture
def authorized(monkeypatch):
    async def complete(oauth_params, user):
        return "https://claude.ai/callback?code=x"

    monkeypatch.setattr(base.oauth_provider, "complete_authorization", complete)


def log_in(app_client, ip: str, password: str):
    _, app = app_client
    client = TestClient(app, client=(ip, 50000))
    return client.post(
        "/oauth/login",
        data={"email": EMAIL, "password": password, "state": "s"},
        follow_redirects=False,
    )


def fill_failures(app_client, ip: str, count: int) -> None:
    for _ in range(count):
        assert log_in(app_client, ip, "wrong").status_code == 200


def test_owner_logs_in_from_known_address_after_failures_elsewhere(
    app_client, victim, fake_redis, authorized
):
    assert log_in(app_client, OWNER_IP, PASSWORD).status_code == 302
    fill_failures(app_client, ATTACKER_IP, 3)

    response = log_in(app_client, OWNER_IP, PASSWORD)

    assert response.status_code == 302


def test_full_failure_bucket_refuses_unknown_addresses(app_client, victim, fake_redis, authorized):
    fill_failures(app_client, ATTACKER_IP, 3)

    response = log_in(app_client, "192.0.2.10", PASSWORD)

    assert (response.status_code, "Too many login attempts" in response.text) == (200, True)


def test_successful_logins_leave_the_failure_bucket_empty(
    app_client, victim, fake_redis, authorized
):
    statuses = [log_in(app_client, ip, PASSWORD).status_code for ip in ("192.0.2.1", "192.0.2.2", "192.0.2.3")]
    fresh = log_in(app_client, "192.0.2.4", PASSWORD)

    assert (statuses, fresh.status_code) == ([302, 302, 302], 302)


def test_known_address_exemption_is_for_that_exact_ipv6_address(
    app_client, victim, fake_redis, authorized
):
    owner, neighbour = "2001:db8:5:6::1", "2001:db8:5:6::2"
    assert log_in(app_client, owner, PASSWORD).status_code == 302
    fill_failures(app_client, ATTACKER_IP, 3)

    from_neighbour = log_in(app_client, neighbour, PASSWORD)
    from_owner = log_in(app_client, owner, PASSWORD)

    assert (from_neighbour.status_code, "Too many login attempts" in from_neighbour.text) == (200, True)
    assert from_owner.status_code == 302
