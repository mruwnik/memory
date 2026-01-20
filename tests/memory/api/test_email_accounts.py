"""
Tests for email accounts API endpoints.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from memory.api import email_accounts
from memory.common.db.models import EmailAccount, GoogleAccount, User


# Test list_accounts


def test_list_accounts_returns_user_accounts_only(client, user, db_session):
    """List should only return accounts owned by the current user."""
    # Create accounts for current user
    account1 = EmailAccount(
        user_id=user.id,
        name="Personal",
        email_address="personal@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    account2 = EmailAccount(
        user_id=user.id,
        name="Work",
        email_address="work@example.com",
        account_type="imap",
        imap_server="imap.work.com",
        username="user",
        password="pass",
    )
    # Create a different user first
    other_user = User(id=999, name="Other User", email="other@example.com")
    db_session.add(other_user)
    db_session.flush()

    # Create account for different user
    other_account = EmailAccount(
        user_id=other_user.id,
        name="Other",
        email_address="other_account@example.com",
        account_type="imap",
        imap_server="imap.other.com",
        username="user",
        password="pass",
    )

    db_session.add_all([account1, account2, other_account])
    db_session.commit()

    response = client.get("/email-accounts")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    emails = {acc["email_address"] for acc in data}
    assert emails == {"personal@example.com", "work@example.com"}


def test_list_accounts_empty_when_no_accounts(client, user, db_session):
    """List should return empty array when user has no accounts."""
    response = client.get("/email-accounts")

    assert response.status_code == 200
    assert response.json() == []


def test_list_accounts_includes_google_account_info(client, user, db_session):
    """List should include GoogleAccount info when linked."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="My Google",
        email="google@example.com",
        access_token="token",
        refresh_token="refresh",
        token_expires_at=datetime.now(timezone.utc),
        scopes=["mail"],
    )
    db_session.add(google_account)
    db_session.flush()

    email_account = EmailAccount(
        user_id=user.id,
        name="Gmail Account",
        email_address="gmail@example.com",
        account_type="gmail",
        google_account_id=google_account.id,
    )
    db_session.add(email_account)
    db_session.commit()

    response = client.get("/email-accounts")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["google_account"] is not None
    assert data[0]["google_account"]["name"] == "My Google"
    assert data[0]["google_account"]["email"] == "google@example.com"


# Test create_account - IMAP


def test_create_imap_account_success(client, user, db_session):
    """Creating an IMAP account should succeed with valid data."""
    payload = {
        "name": "Test Account",
        "email_address": "test@example.com",
        "account_type": "imap",
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "testuser",
        "password": "testpass",
        "use_ssl": True,
        "tags": ["personal"],
        "folders": ["INBOX", "Sent"],
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Account"
    assert data["email_address"] == "test@example.com"
    assert data["account_type"] == "imap"
    assert data["imap_server"] == "imap.example.com"
    assert data["imap_port"] == 993
    assert data["username"] == "testuser"
    assert "password" not in data  # Should not return password
    assert data["tags"] == ["personal"]
    assert data["folders"] == ["INBOX", "Sent"]

    # Verify in database
    account = db_session.query(EmailAccount).filter_by(email_address="test@example.com").first()
    assert account is not None
    assert account.user_id == user.id
    assert account.password == "testpass"


def test_create_imap_account_with_smtp(client, user, db_session):
    """Creating an IMAP account with SMTP settings should work."""
    payload = {
        "name": "Test",
        "email_address": "test@example.com",
        "account_type": "imap",
        "imap_server": "imap.example.com",
        "username": "user",
        "password": "pass",
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["smtp_server"] == "smtp.example.com"
    assert data["smtp_port"] == 587


@pytest.mark.parametrize(
    "missing_field,payload_override",
    [
        ("imap_server", {"imap_server": None}),
        ("username", {"username": None}),
        ("password", {"password": None}),
    ],
)
def test_create_imap_account_missing_required_fields(
    client, user, db_session, missing_field, payload_override
):
    """Creating IMAP account without required fields should fail."""
    payload = {
        "name": "Test",
        "email_address": "test@example.com",
        "account_type": "imap",
        "imap_server": "imap.example.com",
        "username": "user",
        "password": "pass",
    }
    payload.update(payload_override)

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 500  # App returns 500 for validation errors


# Test create_account - Gmail


def test_create_gmail_account_success(client, user, db_session):
    """Creating a Gmail account with valid GoogleAccount should succeed."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="My Google",
        email="google@example.com",
        access_token="token",
        refresh_token="refresh",
        token_expires_at=datetime.now(timezone.utc),
        scopes=["mail"],
    )
    db_session.add(google_account)
    db_session.flush()

    payload = {
        "name": "Gmail",
        "email_address": "gmail@example.com",
        "account_type": "gmail",
        "google_account_id": google_account.id,
        "folders": ["INBOX"],
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["account_type"] == "gmail"
    assert data["google_account_id"] == google_account.id
    assert data["google_account"]["name"] == "My Google"
    assert data["folders"] == ["INBOX"]


def test_create_gmail_account_without_google_account_id(client, user, db_session):
    """Creating Gmail account without google_account_id should fail."""
    payload = {
        "name": "Gmail",
        "email_address": "gmail@example.com",
        "account_type": "gmail",
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 500  # App returns 500 for validation errors


def test_create_gmail_account_with_invalid_google_account(client, user, db_session):
    """Creating Gmail account with non-existent GoogleAccount should fail."""
    payload = {
        "name": "Gmail",
        "email_address": "gmail@example.com",
        "account_type": "gmail",
        "google_account_id": 99999,
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 400
    assert "google account" in response.json()["detail"].lower()


# Test create_account - validation


def test_create_account_duplicate_email(client, user, db_session):
    """Creating account with duplicate email should fail."""
    existing = EmailAccount(
        user_id=user.id,
        name="Existing",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(existing)
    db_session.commit()

    payload = {
        "name": "New",
        "email_address": "test@example.com",
        "account_type": "imap",
        "imap_server": "imap.example.com",
        "username": "user2",
        "password": "pass2",
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    "invalid_port",
    [0, -1, 65536, 100000],
)
def test_create_account_invalid_smtp_port(client, user, db_session, invalid_port):
    """Creating account with invalid SMTP port should fail."""
    payload = {
        "name": "Test",
        "email_address": "test@example.com",
        "account_type": "imap",
        "imap_server": "imap.example.com",
        "username": "user",
        "password": "pass",
        "smtp_server": "smtp.example.com",
        "smtp_port": invalid_port,
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 500  # App returns 500 for validation errors


def test_create_account_sets_defaults(client, user, db_session):
    """Creating account should set default values correctly."""
    payload = {
        "name": "Test",
        "email_address": "test@example.com",
        "account_type": "imap",
        "imap_server": "imap.example.com",
        "username": "user",
        "password": "pass",
    }

    response = client.post("/email-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["imap_port"] == 993  # Default
    assert data["use_ssl"] is True  # Default
    assert data["send_enabled"] is True  # Default
    assert data["active"] is True  # Default
    assert data["folders"] == []  # Default empty
    assert data["tags"] == []  # Default empty


# Test get_account


def test_get_account_success(client, user, db_session):
    """Getting an owned account should succeed."""
    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()

    response = client.get(f"/email-accounts/{account.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == account.id
    assert data["email_address"] == "test@example.com"


def test_get_account_not_found(client, user, db_session):
    """Getting non-existent account should return 404."""
    response = client.get("/email-accounts/99999")

    assert response.status_code == 404


def test_get_account_not_owned(client, user, db_session):
    """Getting account owned by different user should return 404."""
    other_user = User(id=999, name="Other User", email="other@example.com")
    db_session.add(other_user)
    db_session.flush()

    other_account = EmailAccount(
        user_id=other_user.id,
        name="Other",
        email_address="other_acc@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(other_account)
    db_session.commit()

    response = client.get(f"/email-accounts/{other_account.id}")

    assert response.status_code == 404


# Test update_account


def test_update_account_name(client, user, db_session):
    """Updating account name should work."""
    account = EmailAccount(
        user_id=user.id,
        name="Old Name",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(f"/email-accounts/{account.id}", json={"name": "New Name"})

    assert response.status_code == 200
    assert response.json()["name"] == "New Name"

    db_session.refresh(account)
    assert account.name == "New Name"


@pytest.mark.parametrize(
    "field,value",
    [
        ("imap_server", "new.imap.com"),
        ("imap_port", 143),
        ("use_ssl", False),
        ("smtp_server", "smtp.new.com"),
        ("smtp_port", 25),
        ("folders", ["INBOX", "Archive"]),
        ("tags", ["work", "important"]),
        ("active", False),
        ("send_enabled", False),
    ],
)
def test_update_account_fields(client, user, db_session, field, value):
    """Updating various account fields should work."""
    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(f"/email-accounts/{account.id}", json={field: value})

    assert response.status_code == 200
    assert response.json()[field] == value


def test_update_account_multiple_fields(client, user, db_session):
    """Updating multiple fields at once should work."""
    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()

    payload = {
        "name": "Updated",
        "imap_port": 143,
        "tags": ["updated"],
    }

    response = client.patch(f"/email-accounts/{account.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated"
    assert data["imap_port"] == 143
    assert data["tags"] == ["updated"]


def test_update_account_not_owned(client, user, db_session):
    """Updating account owned by different user should return 404."""
    other_user = User(id=999, name="Other User", email="other@example.com")
    db_session.add(other_user)
    db_session.flush()

    other_account = EmailAccount(
        user_id=other_user.id,
        name="Other",
        email_address="other_acc2@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(other_account)
    db_session.commit()

    response = client.patch(f"/email-accounts/{other_account.id}", json={"name": "Hacked"})

    assert response.status_code == 404


def test_update_account_not_found(client, user, db_session):
    """Updating non-existent account should return 404."""
    response = client.patch("/email-accounts/99999", json={"name": "New"})

    assert response.status_code == 404


# Test delete_account


def test_delete_account_success(client, user, db_session):
    """Deleting an owned account should succeed."""
    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()
    account_id = account.id

    response = client.delete(f"/email-accounts/{account_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify deletion
    deleted = db_session.query(EmailAccount).filter_by(id=account_id).first()
    assert deleted is None


def test_delete_account_not_owned(client, user, db_session):
    """Deleting account owned by different user should return 404."""
    other_user = User(id=999, name="Other User", email="other@example.com")
    db_session.add(other_user)
    db_session.flush()

    other_account = EmailAccount(
        user_id=other_user.id,
        name="Other",
        email_address="other_acc3@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(other_account)
    db_session.commit()

    response = client.delete(f"/email-accounts/{other_account.id}")

    assert response.status_code == 404

    # Verify not deleted
    still_exists = db_session.query(EmailAccount).filter_by(id=other_account.id).first()
    assert still_exists is not None


def test_delete_account_not_found(client, user, db_session):
    """Deleting non-existent account should return 404."""
    response = client.delete("/email-accounts/99999")

    assert response.status_code == 404


# Test trigger_sync


@patch("memory.common.celery_app.app")
def test_trigger_sync_success(mock_app, client, user, db_session):
    """Triggering sync should queue Celery task."""
    mock_task = MagicMock()
    mock_task.id = "task-uuid-123"
    mock_app.send_task.return_value = mock_task

    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()

    response = client.post(f"/email-accounts/{account.id}/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "scheduled"
    assert data["task_id"] == "task-uuid-123"

    # Verify task was queued correctly
    mock_app.send_task.assert_called_once()
    call_args = mock_app.send_task.call_args
    # Check positional arg (SYNC_ACCOUNT constant)
    assert call_args[1]["args"] == [account.id]


@patch("memory.common.celery_app.app")
def test_trigger_sync_with_since_date(mock_app, client, user, db_session):
    """Triggering sync with since_date should pass it to task."""
    mock_task = MagicMock()
    mock_task.id = "task-uuid"
    mock_app.send_task.return_value = mock_task

    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(account)
    db_session.commit()

    since = "2024-01-01T00:00:00Z"
    response = client.post(f"/email-accounts/{account.id}/sync?since_date={since}")

    assert response.status_code == 200

    call_args = mock_app.send_task.call_args[1]
    assert call_args["args"] == [account.id]
    assert call_args["kwargs"]["since_date"] == since


@patch("memory.common.celery_app.app")
def test_trigger_sync_not_owned(mock_app, client, user, db_session):
    """Triggering sync on account owned by different user should return 404."""
    other_user = User(id=999, name="Other User", email="other@example.com")
    db_session.add(other_user)
    db_session.flush()

    other_account = EmailAccount(
        user_id=other_user.id,
        name="Other",
        email_address="other_acc4@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(other_account)
    db_session.commit()

    response = client.post(f"/email-accounts/{other_account.id}/sync")

    assert response.status_code == 404
    mock_app.send_task.assert_not_called()


# Test test_connection


@patch("memory.workers.email.imap_connection")
def test_connection_success(mock_imap, client, user, db_session):
    """Testing connection with valid credentials should succeed."""
    # Mock the IMAP connection context manager and list() method
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'folder1', b'folder2'])
    mock_imap.return_value.__enter__.return_value = mock_conn
    mock_imap.return_value.__exit__.return_value = None

    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        imap_port=993,
        username="user",
        password="pass",
        use_ssl=True,
    )
    db_session.add(account)
    db_session.commit()

    response = client.post(f"/email-accounts/{account.id}/test")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "message" in data
    assert data["folders"] == 2

    # Verify connection was attempted with account object
    mock_imap.assert_called_once()
    call_arg = mock_imap.call_args[0][0]
    assert call_arg.id == account.id


@patch("memory.workers.email.imap_connection")
def test_connection_failure(mock_imap, client, user, db_session):
    """Testing connection with invalid credentials should return error."""
    mock_imap.return_value.__enter__.side_effect = Exception("Authentication failed")

    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="wrongpass",
    )
    db_session.add(account)
    db_session.commit()

    response = client.post(f"/email-accounts/{account.id}/test")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "Authentication failed" in data["message"]


@patch("memory.workers.email.imap_connection")
def test_connection_not_owned(mock_imap, client, user, db_session):
    """Testing connection on account owned by different user should return 404."""
    other_user = User(id=999, name="Other User", email="other@example.com")
    db_session.add(other_user)
    db_session.flush()

    other_account = EmailAccount(
        user_id=other_user.id,
        name="Other",
        email_address="other_acc5@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )
    db_session.add(other_account)
    db_session.commit()

    response = client.post(f"/email-accounts/{other_account.id}/test")

    assert response.status_code == 404
    mock_imap.assert_not_called()


def test_connection_gmail_account(client, user, db_session):
    """Testing connection on Gmail account should handle appropriately."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="Google",
        email="google@example.com",
        access_token="token",
        refresh_token="refresh",
        token_expires_at=datetime.now(timezone.utc),
        scopes=["mail"],
    )
    db_session.add(google_account)
    db_session.flush()

    gmail_account = EmailAccount(
        user_id=user.id,
        name="Gmail",
        email_address="gmail@example.com",
        account_type="gmail",
        google_account_id=google_account.id,
    )
    db_session.add(gmail_account)
    db_session.commit()

    # Gmail accounts don't have IMAP credentials, so test should fail
    response = client.post(f"/email-accounts/{gmail_account.id}/test")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"


# Test helper function: account_to_response


def test_account_to_response_with_timestamps(db_session, user):
    """account_to_response should format timestamps as ISO strings."""
    now = datetime.now(timezone.utc)
    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
        created_at=now,
        updated_at=now,
        last_sync_at=now,
    )
    db_session.add(account)
    db_session.commit()

    result = email_accounts.account_to_response(account, db_session)

    assert result.created_at == now.isoformat()
    assert result.updated_at == now.isoformat()
    assert result.last_sync_at == now.isoformat()


def test_account_to_response_null_timestamps(db_session, user):
    """account_to_response should handle null timestamps."""
    account = EmailAccount(
        user_id=user.id,
        name="Test",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
        last_sync_at=None,
    )
    db_session.add(account)
    db_session.commit()

    result = email_accounts.account_to_response(account, db_session)

    assert result.last_sync_at is None


def test_account_to_response_without_google_account(db_session, user):
    """account_to_response should handle accounts without GoogleAccount link."""
    account = EmailAccount(
        user_id=user.id,
        name="IMAP",
        email_address="imap@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
        google_account_id=None,
    )
    db_session.add(account)
    db_session.commit()

    result = email_accounts.account_to_response(account, db_session)

    assert result.google_account_id is None
    assert result.google_account is None


def test_account_to_response_with_google_account(db_session, user):
    """account_to_response should include GoogleAccount details when linked."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="My Google",
        email="google@example.com",
        access_token="token",
        refresh_token="refresh",
        token_expires_at=datetime.now(timezone.utc),
        scopes=["mail"],
    )
    db_session.add(google_account)
    db_session.flush()

    account = EmailAccount(
        user_id=user.id,
        name="Gmail",
        email_address="gmail@example.com",
        account_type="gmail",
        google_account_id=google_account.id,
    )
    db_session.add(account)
    db_session.flush()

    result = email_accounts.account_to_response(account, db_session)

    assert result.google_account is not None
    assert result.google_account.name == "My Google"
    assert result.google_account.email == "google@example.com"
