"""Credential policies exercised through real middleware and disposable DB rows."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from memory.api import auth, jobs, secrets, users
from memory.common import settings
from memory.common.db.models import APIKey, HumanUser, PendingJob, UserSession
from memory.common.db.models.secrets import Secret


@pytest.fixture
def security_client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    app = FastAPI()
    app.add_middleware(auth.AuthenticationMiddleware)
    for router in [users.router, secrets.router, jobs.router]:
        app.include_router(router)
    return TestClient(app)


def identity(db, admin=True, key_scopes=None, session=False):
    user = HumanUser(name="Security test", email="security@example.test", password_hash="fake",
                     scopes=["*"] if admin else ["read", "write"])
    db.add(user)
    db.flush()
    if session:
        credential = UserSession(user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    else:
        credential = APIKey.create(user_id=user.id, scopes=key_scopes)
    db.add(credential)
    db.commit()
    return user, {"Authorization": f"Bearer {credential.id if isinstance(credential, UserSession) else credential.key}"}


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.parametrize("requested", [["*"], None, []])
@pytest.mark.parametrize("other_target", [True, False])
def test_read_key_cannot_mint_keys_or_change_account(security_client, db_session, admin, requested, other_target):
    user, headers = identity(db_session, admin=admin, key_scopes=["read"])
    target = user.id + 100 if other_target else user.id
    result = security_client.post(f"/users/{target}/api-keys", headers=headers,
                                  json={"name": "escalation", "scopes": requested})
    assert result.status_code == 403
    assert security_client.patch(f"/users/{target}", headers=headers,
                                  json={"email": "changed@example.test"}).status_code == 403
    db_session.refresh(user)
    assert user.email == "security@example.test"
    assert user.scopes == (["*"] if admin else ["read", "write"])


@pytest.mark.parametrize("session", [True, False])
@pytest.mark.parametrize("scopes", [["read"], [], None, ["*"]])
def test_secret_export_and_management_require_session(security_client, db_session, session, scopes):
    user, headers = identity(db_session, key_scopes=scopes, session=session)
    secret = Secret(user_id=user.id, name="test-secret")
    secret.value = "FAKE-TEST-VALUE"
    db_session.add(secret)
    db_session.commit()
    for path in ["/secrets", f"/secrets/{secret.id}", f"/secrets/{secret.id}/value"]:
        response = security_client.get(path, headers=headers)
        assert response.status_code == (200 if session else 403)
    response = security_client.patch(f"/secrets/{secret.id}", headers=headers, json={"value": "FAKE-NEW"})
    assert response.status_code == (200 if session else 403)


@pytest.mark.parametrize("scopes,expected", [(["read"], 404), (["*"], 200)])
def test_ownerless_job_only_visible_to_credential_admin(security_client, db_session, scopes, expected):
    _, headers = identity(db_session, key_scopes=scopes)
    job = PendingJob(job_type="test", params={}, status="failed", user_id=None)
    db_session.add(job)
    db_session.commit()
    assert security_client.get(f"/jobs/{job.id}", headers=headers).status_code == expected


@pytest.mark.parametrize("action", ["retry", "reingest"])
@pytest.mark.parametrize("owned", [True, False])
def test_read_key_cannot_mutate_jobs(security_client, db_session, action, owned):
    user, headers = identity(db_session, key_scopes=["read"])
    job = PendingJob(job_type="test", params={}, status="failed", user_id=user.id if owned else None)
    db_session.add(job)
    db_session.commit()
    assert security_client.post(f"/jobs/{job.id}/{action}", headers=headers).status_code == 403


def test_session_can_manage_keys(security_client, db_session):
    user, headers = identity(db_session, session=True)
    response = security_client.post(f"/users/{user.id}/api-keys", headers=headers,
                                   json={"name": "session-created", "scopes": ["read"]})
    assert response.status_code == 200
