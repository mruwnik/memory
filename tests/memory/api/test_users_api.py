"""Tests for user management API endpoints."""

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import APIKey, HumanUser


@pytest.fixture
def admin_user(db_session):
    """Create an admin user with full permissions."""
    existing = db_session.query(HumanUser).filter(HumanUser.id == 1).first()
    if existing:
        existing.scopes = ["*"]
        db_session.commit()
        return existing
    admin = HumanUser.create_with_password(
        email="admin@example.com",
        name="Admin User",
        password="adminpass123",
    )
    admin.id = 1
    admin.scopes = ["*"]
    db_session.add(admin)
    db_session.commit()
    return admin


# --- API Key Management Tests ---


def test_list_my_api_keys_empty(client: TestClient, admin_user):
    """Test listing API keys when user has none."""
    response = client.get(f"/users/{admin_user.id}/api-keys")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_create_my_api_key(client: TestClient, admin_user, db_session):
    """Test creating a new API key for current user."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Test Key", "key_type": "internal"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Key"
    assert data["key_type"] == "internal"
    assert "key" in data  # Full key returned on create
    assert data["key"].startswith("internal_")
    assert data["revoked"] is False
    assert data["is_one_time"] is False


def test_create_my_api_key_one_time(client: TestClient, admin_user, db_session):
    """Test creating a one-time API key."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "One Time Key", "key_type": "one_time", "is_one_time": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["is_one_time"] is True
    assert data["key"].startswith("ot_")


def test_create_my_api_key_with_expiration(client: TestClient, admin_user, db_session):
    """Test creating an API key with expiration."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Expiring Key", "expires_in_days": 30},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["expires_at"] is not None


def test_create_my_api_key_invalid_type(client: TestClient, admin_user, db_session):
    """Test creating an API key with invalid type fails."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Bad Key", "key_type": "invalid_type"},
    )

    assert response.status_code == 422  # Validation error


def test_list_my_api_keys_after_create(client: TestClient, admin_user, db_session):
    """Test listing API keys shows created keys."""
    # Create a key first
    create_response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Listed Key"},
    )
    assert create_response.status_code == 200
    created_key_id = create_response.json()["id"]

    # List keys
    response = client.get(f"/users/{admin_user.id}/api-keys")

    assert response.status_code == 200
    data = response.json()
    key_ids = [k["id"] for k in data]
    assert created_key_id in key_ids


def test_revoke_my_api_key(client: TestClient, admin_user, db_session):
    """Test revoking (soft-delete) an API key."""
    # Create a key first
    create_response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "To Revoke"},
    )
    key_id = create_response.json()["id"]

    # Revoke it
    response = client.delete(f"/users/{admin_user.id}/api-keys/{key_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"

    # Verify it's revoked
    list_response = client.get(f"/users/{admin_user.id}/api-keys")
    keys = list_response.json()
    revoked_key = next((k for k in keys if k["id"] == key_id), None)
    assert revoked_key is not None
    assert revoked_key["revoked"] is True


def test_delete_my_api_key_permanent(client: TestClient, admin_user, db_session):
    """Test permanently deleting an API key."""
    # Create a key first
    create_response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "To Delete"},
    )
    key_id = create_response.json()["id"]

    # Delete it permanently
    response = client.delete(f"/users/{admin_user.id}/api-keys/{key_id}/permanent")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify it's gone
    list_response = client.get(f"/users/{admin_user.id}/api-keys")
    keys = list_response.json()
    key_ids = [k["id"] for k in keys]
    assert key_id not in key_ids


def test_revoke_nonexistent_key(client: TestClient, admin_user):
    """Test revoking a non-existent key returns 404."""
    response = client.delete(f"/users/{admin_user.id}/api-keys/99999")

    assert response.status_code == 404


def test_delete_nonexistent_key(client: TestClient, admin_user):
    """Test deleting a non-existent key returns 404."""
    response = client.delete(f"/users/{admin_user.id}/api-keys/99999/permanent")

    assert response.status_code == 404


@pytest.mark.parametrize("key_type", [
    "internal",
    "discord",
    "google",
    "github",
    "mcp",
    "one_time",
])
def test_create_api_key_all_types(client: TestClient, admin_user, db_session, key_type):
    """Test creating API keys with all valid types."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": f"{key_type} key", "key_type": key_type},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["key_type"] == key_type


def test_api_key_serialization_hides_full_key(client: TestClient, admin_user, db_session):
    """Test that list response shows preview, not full key."""
    # Create a key
    create_response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Preview Test"},
    )
    full_key = create_response.json()["key"]

    # List keys
    list_response = client.get(f"/users/{admin_user.id}/api-keys")
    keys = list_response.json()
    key_data = keys[-1]  # Most recently created

    # Should have preview, not full key
    assert "key_preview" in key_data
    assert key_data["key_preview"].startswith("internal_")
    assert "..." in key_data["key_preview"]
    # Full key should not be present in list response
    assert key_data.get("key") != full_key


# --- Admin API Key Management Tests ---


def test_admin_list_user_api_keys(client: TestClient, admin_user, db_session):
    """Test admin can list another user's API keys."""
    # Create another user
    other_user = HumanUser.create_with_password(
        email="other@example.com",
        name="Other User",
        password="otherpass123",
    )
    db_session.add(other_user)
    db_session.commit()

    # Create a key for the other user directly
    key = APIKey.create(user_id=other_user.id, name="Other's Key")
    db_session.add(key)
    db_session.commit()

    # Admin lists other user's keys
    response = client.get(f"/users/{other_user.id}/api-keys")

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(k["name"] == "Other's Key" for k in data)


def test_admin_create_user_api_key(client: TestClient, admin_user, db_session):
    """Test admin can create API key for another user."""
    # Create another user
    other_user = HumanUser.create_with_password(
        email="other2@example.com",
        name="Other User 2",
        password="otherpass123",
    )
    db_session.add(other_user)
    db_session.commit()

    # Admin creates key for other user
    response = client.post(
        f"/users/{other_user.id}/api-keys",
        json={"name": "Admin Created Key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Admin Created Key"
    assert "key" in data


def test_admin_revoke_user_api_key(client: TestClient, admin_user, db_session):
    """Test admin can revoke another user's API key."""
    # Create another user with a key
    other_user = HumanUser.create_with_password(
        email="other3@example.com",
        name="Other User 3",
        password="otherpass123",
    )
    db_session.add(other_user)
    db_session.commit()

    key = APIKey.create(user_id=other_user.id, name="To Revoke")
    db_session.add(key)
    db_session.commit()
    key_id = key.id

    # Admin revokes the key
    response = client.delete(f"/users/{other_user.id}/api-keys/{key_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"


# --- User Response Tests ---


def test_get_current_user_shows_api_key_count(client: TestClient, admin_user, db_session):
    """Test that user response includes API key count."""
    # Create a few keys
    client.post(f"/users/{admin_user.id}/api-keys", json={"name": "Key 1"})
    client.post(f"/users/{admin_user.id}/api-keys", json={"name": "Key 2"})

    response = client.get("/users/me")

    assert response.status_code == 200
    data = response.json()
    assert "api_key_count" in data
    assert data["api_key_count"] >= 2


# --- Scopes Tests ---


def test_list_available_scopes(client: TestClient, admin_user):
    """Test listing available scopes."""
    response = client.get("/users/scopes")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check structure of scope info
    scope = data[0]
    assert "value" in scope
    assert "label" in scope
    assert "description" in scope
    assert "category" in scope

    # Check some expected scopes are present
    scope_values = [s["value"] for s in data]
    assert "read" in scope_values
    assert "*" in scope_values
    assert "discord" in scope_values


def test_create_user_with_invalid_scope(client: TestClient, admin_user, db_session):
    """Test that creating a user with invalid scopes fails."""
    response = client.post(
        "/users",
        json={
            "name": "Test User",
            "email": "invalid_scope_user@example.com",
            "password": "testpass123",
            "scopes": ["read", "invalid_scope"],
        },
    )

    assert response.status_code == 400
    assert "Invalid scopes" in response.json()["detail"]
    assert "invalid_scope" in response.json()["detail"]


def test_update_user_with_invalid_scope(client: TestClient, admin_user, db_session):
    """Test that updating a user with invalid scopes fails."""
    # Create a valid user first
    create_response = client.post(
        "/users",
        json={
            "name": "Test User",
            "email": "valid_user@example.com",
            "password": "testpass123",
            "scopes": ["read"],
        },
    )
    assert create_response.status_code == 200
    user_id = create_response.json()["id"]

    # Try to update with invalid scope
    response = client.patch(
        f"/users/{user_id}",
        json={"scopes": ["read", "nonexistent_scope"]},
    )

    assert response.status_code == 400
    assert "Invalid scopes" in response.json()["detail"]


def test_create_user_with_valid_scopes(client: TestClient, admin_user, db_session):
    """Test that creating a user with valid scopes succeeds."""
    response = client.post(
        "/users",
        json={
            "name": "Test User",
            "email": "valid_scopes_user@example.com",
            "password": "testpass123",
            "scopes": ["read", "observe", "discord", "github"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert set(data["scopes"]) == {"read", "observe", "discord", "github"}
