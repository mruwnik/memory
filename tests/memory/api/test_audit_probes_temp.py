"""Regression checks for restricted credentials."""

import pytest

from memory.api.auth import get_current_user
from memory.common import settings
from memory.common.db.models import APIKey


@pytest.fixture
def restricted_auth(client, app_client, user, db_session, monkeypatch):
    _, app = app_client
    override = app.dependency_overrides.pop(get_current_user)
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    key = APIKey.create(user_id=user.id, key_type="internal", name="audit", scopes=["read"])
    db_session.add(key)
    db_session.commit()
    try:
        yield {"Authorization": f"Bearer {key.key}"}
    finally:
        app.dependency_overrides[get_current_user] = override


def test_reject_key_escalation(client, restricted_auth, user):
    response = client.post(f"/api/users/{user.id}/api-keys", headers=restricted_auth,
                           json={"name": "audit-escalated", "scopes": ["*"]})
    assert response.status_code == 403


def test_reject_account_mutation(client, restricted_auth, user):
    response = client.patch(f"/api/users/{user.id}", headers=restricted_auth,
                            json={"email": "audit-replacement@example.invalid"})
    assert response.status_code == 403
    assert user.email != "audit-replacement@example.invalid"


def test_reject_ownerless_job_disclosure(client, restricted_auth, user, db_session):
    from memory.common.db.models import PendingJob

    user.scopes = ["read"]
    job = PendingJob(job_type="content_ingest", user_id=None, params={"private_text": "FAKE-PRIVATE-CONTENT"})
    db_session.add(job)
    db_session.commit()
    response = client.get("/api/jobs", headers=restricted_auth)
    assert response.status_code == 200
    assert "FAKE-PRIVATE-CONTENT" not in response.text
