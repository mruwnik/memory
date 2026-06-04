from unittest.mock import MagicMock

import fakeredis.aioredis as fakeaioredis
import pytest

from memory.common.check.redis_client import get_check_redis
from memory.common.db.models.users import HumanUser


@pytest.fixture
def fake_redis():
    return fakeaioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def check_client(app_client, db_session, user, fake_redis):
    from memory.api.auth import get_current_user
    from memory.common.db.connection import get_session

    test_client, app = app_client

    def get_test_session():
        yield db_session

    user.scopes = ["check"]
    db_session.flush()

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_check_redis] = lambda: fake_redis
    yield test_client
    app.dependency_overrides.clear()


def test_submit_returns_job_id(check_client):
    resp = check_client.post("/check", json={"text": "verify this", "mode": "verify"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"].startswith("chk_")
    assert body["status"] == "queued"


def test_submit_missing_text_422(check_client):
    assert check_client.post("/check", json={"mode": "verify"}).status_code == 422


def test_submit_unknown_mode_422(check_client):
    assert check_client.post("/check", json={"text": "x", "mode": "bogus"}).status_code == 422


def test_submit_oversized_text_413(check_client):
    big = "a" * (64 * 1024 + 1)
    assert check_client.post("/check", json={"text": big}).status_code == 413


def test_submit_oversized_context_413(check_client):
    big = "a" * (64 * 1024 + 1)
    resp = check_client.post("/check", json={"text": "ok", "context": {"blob": big}})
    assert resp.status_code == 413


def test_poll_unknown_404(check_client):
    assert check_client.get("/check/chk_missing").status_code == 404


def test_full_lifecycle(check_client):
    job_id = check_client.post("/check", json={"text": "x", "mode": "verify"}).json()["job_id"]
    nxt = check_client.get("/check/next?wait=0")
    assert nxt.status_code == 200
    job = nxt.json()
    assert job["job_id"] == job_id
    lease = job["lease_id"]
    res = check_client.post(f"/check/{job_id}/result",
                            json={"status": "ok", "lease_id": lease,
                                  "result": {"summary": "done"}})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    poll = check_client.get(f"/check/{job_id}")
    assert poll.status_code == 200
    body = poll.json()
    assert body["status"] == "ok"
    assert body["result"] == {"summary": "done"}


def test_next_empty_returns_204(check_client):
    assert check_client.get("/check/next?wait=0").status_code == 204


def test_next_mode_filter_claims_matching(check_client):
    check_client.post("/check", json={"text": "r", "mode": "research"})
    dd = check_client.post("/check", json={"text": "d", "mode": "deep-dive"}).json()["job_id"]
    nxt = check_client.get("/check/next?wait=0&mode=deep-dive")
    assert nxt.status_code == 200
    assert nxt.json()["job_id"] == dd


def test_next_mode_filter_set_claims_any_member(check_client):
    check_client.post("/check", json={"text": "v", "mode": "verify"})
    dd = check_client.post("/check", json={"text": "d", "mode": "deep-dive"}).json()["job_id"]
    # repeated ?mode= -> claim any of the listed types
    nxt = check_client.get("/check/next?wait=0&mode=deep-dive&mode=investigation-team")
    assert nxt.status_code == 200
    assert nxt.json()["job_id"] == dd


def test_next_mode_filter_no_match_returns_204(check_client):
    check_client.post("/check", json={"text": "r", "mode": "research"})
    assert check_client.get("/check/next?wait=0&mode=investigation-team").status_code == 204


def test_next_unknown_mode_422(check_client):
    assert check_client.get("/check/next?wait=0&mode=bogus").status_code == 422


def test_result_stale_lease_410(check_client):
    job_id = check_client.post("/check", json={"text": "x"}).json()["job_id"]
    check_client.get("/check/next?wait=0")
    resp = check_client.post(f"/check/{job_id}/result",
                             json={"status": "ok", "lease_id": "wrong", "result": {}})
    assert resp.status_code == 410


def test_result_twice_409(check_client):
    job_id = check_client.post("/check", json={"text": "x"}).json()["job_id"]
    lease = check_client.get("/check/next?wait=0").json()["lease_id"]
    check_client.post(f"/check/{job_id}/result",
                      json={"status": "ok", "lease_id": lease, "result": {}})
    resp = check_client.post(f"/check/{job_id}/result",
                             json={"status": "ok", "lease_id": lease, "result": {}})
    assert resp.status_code == 409


def test_poll_with_wait_returns_answer(check_client):
    job_id = check_client.post("/check", json={"text": "x", "mode": "verify"}).json()["job_id"]
    lease = check_client.get("/check/next?wait=0").json()["lease_id"]
    check_client.post(f"/check/{job_id}/result",
                      json={"status": "ok", "lease_id": lease, "result": {"a": 1}})
    # job already terminal -> wait returns immediately
    resp = check_client.get(f"/check/{job_id}?wait=5")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["result"] == {"a": 1}


def _override_user(app, uid, scopes):
    from memory.api.auth import get_current_user
    u = MagicMock(spec=HumanUser)
    u.id = uid
    u.scopes = scopes
    app.dependency_overrides[get_current_user] = lambda: u


def test_poll_other_user_gets_404(check_client, app_client):
    _test_client, app = app_client
    job_id = check_client.post("/check", json={"text": "secret"}).json()["job_id"]
    _override_user(app, uid=999, scopes=["check"])  # different, non-admin
    resp = check_client.get(f"/check/{job_id}")
    assert resp.status_code == 404  # must not leak existence (not 403)


def test_poll_other_user_with_wait_404_no_block(check_client, app_client):
    _test_client, app = app_client
    job_id = check_client.post("/check", json={"text": "secret"}).json()["job_id"]
    _override_user(app, uid=999, scopes=["check"])  # non-owner
    resp = check_client.get(f"/check/{job_id}?wait=2")
    assert resp.status_code == 404


def test_admin_can_poll_any_job(check_client, app_client):
    _test_client, app = app_client
    job_id = check_client.post("/check", json={"text": "secret"}).json()["job_id"]
    _override_user(app, uid=999, scopes=["*"])  # admin
    resp = check_client.get(f"/check/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id


def test_submit_requires_check_scope(check_client, app_client):
    _test_client, app = app_client
    _override_user(app, uid=1, scopes=["read"])  # lacks check scope
    resp = check_client.post("/check", json={"text": "x"})
    assert resp.status_code == 403


def test_next_requires_check_scope(check_client, app_client):
    _test_client, app = app_client
    _override_user(app, uid=1, scopes=["read"])  # lacks check scope
    resp = check_client.get("/check/next?wait=0")
    assert resp.status_code == 403
