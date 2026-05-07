"""
Tests for transcript accounts API endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import HumanUser
from memory.common.db.models.sources import TranscriptAccount


def make_account(
    db_session,
    user_id,
    *,
    name="Personal",
    provider="fireflies",
    api_key="ff_test_key",
    webhook_secret=None,
    tags=None,
    project_id=None,
    sensitivity="basic",
    active=True,
) -> TranscriptAccount:
    """Helper for building TranscriptAccount rows in tests."""
    account = TranscriptAccount(
        user_id=user_id,
        name=name,
        provider=provider,
        tags=tags or [],
        project_id=project_id,
        sensitivity=sensitivity,
        active=active,
    )
    account.api_key = api_key
    if webhook_secret is not None:
        account.webhook_secret = webhook_secret
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def make_other_user(db_session, user_id=999, email="other@example.com") -> HumanUser:
    """Create a second HumanUser inline (mirrors test_email_accounts.py pattern)."""
    other = HumanUser(
        id=user_id,
        email=email,
        name="Other User",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.flush()
    return other


# Test list_providers


def test_list_providers_returns_sorted_list(client, user, db_session, monkeypatch):
    """GET /providers should return providers, sorted."""
    # Patch the cached SUPPORTED_PROVIDERS list to a multi-item, intentionally
    # unsorted source so the assert data == sorted(data) is non-vacuous.
    monkeypatch.setattr(
        "memory.api.transcript_accounts.SUPPORTED_PROVIDERS",
        sorted(["fireflies", "granola", "otter"]),
    )

    response = client.get("/transcript-accounts/providers")

    assert response.status_code == 200
    data = response.json()
    assert data == ["fireflies", "granola", "otter"]
    assert data == sorted(data)


def test_list_providers_includes_fireflies(client, user, db_session):
    """The default provider list should include the worker's only provider."""
    response = client.get("/transcript-accounts/providers")

    assert response.status_code == 200
    assert "fireflies" in response.json()


# Test list_accounts


def test_list_accounts_empty_when_no_accounts(client, user, db_session):
    """List should return empty array when user has no accounts."""
    response = client.get("/transcript-accounts")

    assert response.status_code == 200
    assert response.json() == []


def test_list_accounts_returns_user_accounts(client, user, db_session):
    """List should return the user's transcript accounts."""
    make_account(db_session, user.id, name="Work", api_key="key1")
    make_account(db_session, user.id, name="Personal", api_key="key2")

    response = client.get("/transcript-accounts")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    names = {acc["name"] for acc in data}
    assert names == {"Work", "Personal"}


def test_list_accounts_admin_filter_by_user_id(client, user, db_session):
    """Admin can filter list by ?user_id= and only see that user's accounts."""
    other = make_other_user(db_session)

    make_account(db_session, user.id, name="Mine", api_key="k1")
    make_account(db_session, other.id, name="Theirs", api_key="k2")

    response = client.get(f"/transcript-accounts?user_id={other.id}")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Theirs"


def test_list_accounts_non_admin_user_id_filter_ignored(
    regular_client, user, db_session
):
    """Non-admin's ?user_id= is ignored — they always see only their own accounts."""
    other = make_other_user(db_session)

    make_account(db_session, user.id, name="Mine", api_key="k1")
    make_account(db_session, other.id, name="Theirs", api_key="k2")

    response = regular_client.get(f"/transcript-accounts?user_id={other.id}")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Mine"


# Test create_account


def test_create_account_success(client, user, db_session):
    """Creating a transcript account should succeed and return expected fields."""
    payload = {
        "name": "My Fireflies",
        "provider": "fireflies",
        "api_key": "secret_api_key_value",
        "tags": ["work", "meetings"],
        "project_id": None,
        "sensitivity": "internal",
    }

    response = client.post("/transcript-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Fireflies"
    assert data["provider"] == "fireflies"
    assert data["has_api_key"] is True
    assert data["has_webhook_secret"] is False
    assert data["tags"] == ["work", "meetings"]
    assert data["sensitivity"] == "internal"
    assert data["active"] is True
    # Crucially: the secret must NOT come back in the response.
    assert "api_key" not in data
    assert "webhook_secret" not in data


def test_create_account_persists_encrypted_api_key(client, user, db_session):
    """Created account stores the api_key encrypted; the property decrypts it."""
    payload = {
        "name": "ff",
        "provider": "fireflies",
        "api_key": "plaintext-key-xyz",
    }

    response = client.post("/transcript-accounts", json=payload)
    assert response.status_code == 200

    account = (
        db_session.query(TranscriptAccount)
        .filter_by(user_id=user.id, name="ff")
        .first()
    )
    assert account is not None
    # Stored as bytes (encrypted), not plaintext.
    assert account.api_key_encrypted is not None
    assert isinstance(account.api_key_encrypted, (bytes, bytearray, memoryview))
    assert b"plaintext-key-xyz" not in bytes(account.api_key_encrypted)
    # But the property decrypts it back.
    assert account.api_key == "plaintext-key-xyz"


def test_create_account_invalid_provider(client, user, db_session):
    """provider=bogus should 400 with detail mentioning supported list."""
    payload = {
        "name": "x",
        "provider": "bogus",
        "api_key": "k",
    }

    response = client.post("/transcript-accounts", json=payload)

    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "supported" in detail
    assert "fireflies" in detail


def test_create_account_duplicate_returns_400(client, user, db_session):
    """Creating with the same (user, provider, name) twice returns 400, not 500."""
    make_account(db_session, user.id, name="dup", provider="fireflies", api_key="k1")

    payload = {
        "name": "dup",
        "provider": "fireflies",
        "api_key": "k2",
    }

    response = client.post("/transcript-accounts", json=payload)

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


def test_create_account_duplicate_via_integrity_error_returns_400(
    client, user, db_session, monkeypatch
):
    """Race fallback: pre-check passes but DB unique constraint fires.

    Simulates two concurrent POSTs that both pass the application-level
    duplicate check but only one wins the unique-constraint commit.
    """
    from sqlalchemy.exc import IntegrityError

    # Force the pre-check query to return None so we proceed to db.add/commit.
    # Then make commit raise IntegrityError on first call.
    real_commit = db_session.commit
    call_count = {"n": 0}

    def fake_commit():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IntegrityError("dup", {}, Exception("simulated unique violation"))
        return real_commit()

    # No pre-existing row this time — we want the application-level pre-check
    # to PASS, so the IntegrityError fallback is the path under test. We
    # achieve this by having commit() raise IntegrityError as if a concurrent
    # writer had inserted the duplicate between our pre-check and our commit.
    monkeypatch.setattr(db_session, "commit", fake_commit)

    payload = {
        "name": "race",
        "provider": "fireflies",
        "api_key": "k2",
    }

    response = client.post("/transcript-accounts", json=payload)

    # IntegrityError fallback should produce a clean 400, not a 500.
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


def test_create_account_empty_api_key_returns_400(client, user, db_session):
    """Empty api_key on create should be rejected (mirrors update guard)."""
    payload = {
        "name": "empty-key",
        "provider": "fireflies",
        "api_key": "",
    }

    response = client.post("/transcript-accounts", json=payload)

    assert response.status_code == 400
    assert "api_key" in response.json()["detail"].lower()


def test_create_account_with_webhook_secret(client, user, db_session):
    """Creating with webhook_secret should result in has_webhook_secret=True."""
    payload = {
        "name": "with-webhook",
        "provider": "fireflies",
        "api_key": "k",
        "webhook_secret": "shh",
    }

    response = client.post("/transcript-accounts", json=payload)

    assert response.status_code == 200
    assert response.json()["has_webhook_secret"] is True


# Test get_account


def test_get_account_success(client, user, db_session):
    """Getting an owned account should succeed."""
    account = make_account(db_session, user.id, name="g", api_key="k")

    response = client.get(f"/transcript-accounts/{account.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == account.id
    assert data["name"] == "g"
    assert data["has_api_key"] is True


def test_get_account_not_found(client, user, db_session):
    """Getting a non-existent account should return 404."""
    response = client.get("/transcript-accounts/99999")

    assert response.status_code == 404


def test_get_account_not_owned(regular_client, user, db_session):
    """Non-admin getting an account owned by a different user should return 404."""
    other = make_other_user(db_session)
    other_account = make_account(db_session, other.id, name="theirs", api_key="k")

    response = regular_client.get(f"/transcript-accounts/{other_account.id}")

    assert response.status_code == 404


# Test update_account


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Renamed"),
        ("tags", ["a", "b"]),
        ("active", False),
        ("sensitivity", "confidential"),
    ],
)
def test_update_account_fields(client, user, db_session, field, value):
    """PATCH should update simple fields."""
    account = make_account(db_session, user.id, name="x", api_key="k")

    response = client.patch(f"/transcript-accounts/{account.id}", json={field: value})

    assert response.status_code == 200
    assert response.json()[field] == value


def test_update_account_active_toggle_round_trip(client, user, db_session):
    """active=False then active=True should round-trip."""
    account = make_account(db_session, user.id, name="t", api_key="k")

    r1 = client.patch(f"/transcript-accounts/{account.id}", json={"active": False})
    assert r1.status_code == 200
    assert r1.json()["active"] is False

    r2 = client.patch(f"/transcript-accounts/{account.id}", json={"active": True})
    assert r2.status_code == 200
    assert r2.json()["active"] is True


def test_update_account_rotates_api_key(client, user, db_session):
    """PATCH api_key=<new> rotates the key (stored bytes change, decrypts to new)."""
    account = make_account(db_session, user.id, name="rot", api_key="old-key")
    old_encrypted = bytes(account.api_key_encrypted)

    response = client.patch(
        f"/transcript-accounts/{account.id}", json={"api_key": "new-key"}
    )
    assert response.status_code == 200

    db_session.refresh(account)
    new_encrypted = bytes(account.api_key_encrypted)
    assert new_encrypted != old_encrypted
    assert account.api_key == "new-key"


def test_update_account_empty_api_key_returns_400(client, user, db_session):
    """PATCH api_key="" should return 400."""
    account = make_account(db_session, user.id, name="emp", api_key="k")

    response = client.patch(f"/transcript-accounts/{account.id}", json={"api_key": ""})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_update_webhook_secret_set(client, user, db_session):
    """PATCH webhook_secret=<non-empty> should set the secret."""
    account = make_account(db_session, user.id, name="wh", api_key="k")
    assert account.webhook_secret_encrypted is None

    response = client.patch(
        f"/transcript-accounts/{account.id}",
        json={"webhook_secret": "the-secret"},
    )
    assert response.status_code == 200
    assert response.json()["has_webhook_secret"] is True

    db_session.refresh(account)
    assert account.webhook_secret == "the-secret"


def test_update_webhook_secret_clear_via_empty_string(client, user, db_session):
    """PATCH webhook_secret="" should clear the secret."""
    account = make_account(
        db_session, user.id, name="wh", api_key="k", webhook_secret="initial"
    )
    assert account.webhook_secret_encrypted is not None

    response = client.patch(
        f"/transcript-accounts/{account.id}", json={"webhook_secret": ""}
    )
    assert response.status_code == 200
    assert response.json()["has_webhook_secret"] is False

    db_session.refresh(account)
    assert account.webhook_secret_encrypted is None
    assert account.webhook_secret is None


def test_update_webhook_secret_omitted_unchanged(client, user, db_session):
    """PATCH without webhook_secret in body should leave the secret unchanged."""
    account = make_account(
        db_session, user.id, name="wh", api_key="k", webhook_secret="keep-me"
    )
    assert account.webhook_secret_encrypted is not None
    original_encrypted = bytes(account.webhook_secret_encrypted)

    response = client.patch(
        f"/transcript-accounts/{account.id}", json={"name": "renamed"}
    )
    assert response.status_code == 200
    assert response.json()["has_webhook_secret"] is True

    db_session.refresh(account)
    assert account.webhook_secret_encrypted is not None
    assert bytes(account.webhook_secret_encrypted) == original_encrypted
    assert account.webhook_secret == "keep-me"


def test_update_account_not_owned(regular_client, user, db_session):
    """PATCH on another user's account should 404 for non-admin."""
    other = make_other_user(db_session)
    other_account = make_account(db_session, other.id, name="theirs", api_key="k")

    response = regular_client.patch(
        f"/transcript-accounts/{other_account.id}", json={"name": "hacked"}
    )

    assert response.status_code == 404


def test_update_account_not_found(client, user, db_session):
    """PATCH on a non-existent account should 404."""
    response = client.patch("/transcript-accounts/99999", json={"name": "x"})

    assert response.status_code == 404


# Test delete_account


def test_delete_account_success(client, user, db_session):
    """DELETE on an owned account should remove it."""
    account = make_account(db_session, user.id, name="d", api_key="k")
    account_id = account.id

    response = client.delete(f"/transcript-accounts/{account_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    assert db_session.query(TranscriptAccount).filter_by(id=account_id).first() is None


def test_delete_account_not_owned(regular_client, user, db_session):
    """DELETE on another user's account should 404 for non-admin."""
    other = make_other_user(db_session)
    other_account = make_account(db_session, other.id, name="theirs", api_key="k")

    response = regular_client.delete(f"/transcript-accounts/{other_account.id}")

    assert response.status_code == 404
    # And the row is still there.
    assert (
        db_session.query(TranscriptAccount).filter_by(id=other_account.id).first()
        is not None
    )


def test_delete_account_not_found(client, user, db_session):
    """DELETE on a non-existent account should 404."""
    response = client.delete("/transcript-accounts/99999")

    assert response.status_code == 404


# Test sync / rescan dispatch


@patch("memory.api.transcript_accounts.celery_app")
def test_trigger_sync_dispatches_correct_task(mock_app, client, user, db_session):
    """POST /sync should dispatch SYNC_TRANSCRIPT_ACCOUNT with args=[id]."""
    from memory.common.celery_app import SYNC_TRANSCRIPT_ACCOUNT

    mock_task = MagicMock()
    mock_task.id = "task-uuid-sync"
    mock_app.send_task.return_value = mock_task

    account = make_account(db_session, user.id, name="s", api_key="k")

    response = client.post(f"/transcript-accounts/{account.id}/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["task_id"] == "task-uuid-sync"

    mock_app.send_task.assert_called_once()
    call_args = mock_app.send_task.call_args
    assert call_args[0][0] == SYNC_TRANSCRIPT_ACCOUNT
    assert call_args[1]["args"] == [account.id]


@patch("memory.api.transcript_accounts.celery_app")
def test_trigger_rescan_dispatches_correct_task(mock_app, client, user, db_session):
    """POST /rescan should dispatch RESCAN_TRANSCRIPT_ACCOUNT with args=[id]."""
    from memory.common.celery_app import RESCAN_TRANSCRIPT_ACCOUNT

    mock_task = MagicMock()
    mock_task.id = "task-uuid-rescan"
    mock_app.send_task.return_value = mock_task

    account = make_account(db_session, user.id, name="r", api_key="k")

    response = client.post(f"/transcript-accounts/{account.id}/rescan")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["task_id"] == "task-uuid-rescan"

    mock_app.send_task.assert_called_once()
    call_args = mock_app.send_task.call_args
    assert call_args[0][0] == RESCAN_TRANSCRIPT_ACCOUNT
    assert call_args[1]["args"] == [account.id]


@patch("memory.api.transcript_accounts.celery_app")
def test_trigger_sync_not_owned(mock_app, regular_client, user, db_session):
    """POST /sync on another user's account should 404 for non-admin."""
    other = make_other_user(db_session)
    other_account = make_account(db_session, other.id, name="theirs", api_key="k")

    response = regular_client.post(f"/transcript-accounts/{other_account.id}/sync")

    assert response.status_code == 404
    mock_app.send_task.assert_not_called()


@patch("memory.api.transcript_accounts.celery_app")
def test_trigger_rescan_not_owned(mock_app, regular_client, user, db_session):
    """POST /rescan on another user's account should 404 for non-admin."""
    other = make_other_user(db_session)
    other_account = make_account(db_session, other.id, name="theirs", api_key="k")

    response = regular_client.post(f"/transcript-accounts/{other_account.id}/rescan")

    assert response.status_code == 404
    mock_app.send_task.assert_not_called()


# Full CRUD round-trip


def test_full_crud_round_trip(client, user, db_session):
    """create → list → get → patch → delete, with tags + project_id + sensitivity."""
    create_payload = {
        "name": "Round Trip",
        "provider": "fireflies",
        "api_key": "round-trip-key",
        "tags": ["foo", "bar"],
        "project_id": None,  # FK-safe; project rows aren't created in this test
        "sensitivity": "internal",
    }
    create_resp = client.post("/transcript-accounts", json=create_payload)
    assert create_resp.status_code == 200
    created = create_resp.json()
    account_id = created["id"]
    assert created["has_api_key"] is True
    assert created["has_webhook_secret"] is False
    assert created["tags"] == ["foo", "bar"]
    assert created["sensitivity"] == "internal"

    # list
    list_resp = client.get("/transcript-accounts")
    assert list_resp.status_code == 200
    assert any(a["id"] == account_id for a in list_resp.json())

    # get
    get_resp = client.get(f"/transcript-accounts/{account_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Round Trip"

    # patch
    patch_resp = client.patch(
        f"/transcript-accounts/{account_id}",
        json={"name": "Round Trip 2", "tags": ["baz"]},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Round Trip 2"
    assert patch_resp.json()["tags"] == ["baz"]

    # delete
    delete_resp = client.delete(f"/transcript-accounts/{account_id}")
    assert delete_resp.status_code == 200

    # confirm gone
    final_get = client.get(f"/transcript-accounts/{account_id}")
    assert final_get.status_code == 404
