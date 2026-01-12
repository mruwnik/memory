"""Tests for Calendar Accounts API endpoints."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from memory.common.db.models.sources import CalendarAccount, GoogleAccount


# ====== GET /calendar-accounts tests ======


def test_list_accounts_returns_all_accounts(client, db_session, user):
    """List accounts returns all calendar accounts."""
    # Create Google account first for foreign key
    google_account = GoogleAccount(
        user_id=user.id,
        name="Test Google",
        email="test@gmail.com",
    )
    db_session.add(google_account)
    db_session.commit()

    account1 = CalendarAccount(
        name="Work Calendar",
        calendar_type="caldav",
        caldav_url="https://cal.example.com/dav",
        caldav_username="user1",
        caldav_password="pass1",
        calendar_ids=["work"],
        tags=["work"],
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    account2 = CalendarAccount(
        name="Personal Calendar",
        calendar_type="google",
        google_account_id=google_account.id,
        calendar_ids=["primary"],
        tags=["personal"],
        check_interval=30,
        sync_past_days=60,
        sync_future_days=180,
    )
    db_session.add(account1)
    db_session.add(account2)
    db_session.commit()

    response = client.get("/calendar-accounts")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Work Calendar"
    assert data[1]["name"] == "Personal Calendar"


def test_list_accounts_empty_when_no_accounts(client, db_session, user):
    """List accounts returns empty list when no accounts exist."""
    response = client.get("/calendar-accounts")

    assert response.status_code == 200
    assert response.json() == []


# ====== POST /calendar-accounts tests ======


def test_create_caldav_account_success(client, db_session, user):
    """Create CalDAV account succeeds with required fields."""
    payload = {
        "name": "My CalDAV",
        "calendar_type": "caldav",
        "caldav_url": "https://caldav.example.com/dav",
        "caldav_username": "testuser",
        "caldav_password": "testpass",
        "calendar_ids": ["cal1", "cal2"],
        "tags": ["work"],
        "check_interval": 20,
        "sync_past_days": 45,
        "sync_future_days": 120,
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My CalDAV"
    assert data["calendar_type"] == "caldav"
    assert data["caldav_url"] == "https://caldav.example.com/dav"
    assert data["caldav_username"] == "testuser"
    assert data["calendar_ids"] == ["cal1", "cal2"]
    assert data["tags"] == ["work"]
    assert data["check_interval"] == 20

    # Verify in database
    account = db_session.query(CalendarAccount).filter_by(name="My CalDAV").first()
    assert account is not None
    assert account.calendar_type == "caldav"


def test_create_google_account_success(client, db_session, user):
    """Create Google Calendar account succeeds with valid google_account_id."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="Test Google",
        email="test@gmail.com",
    )
    db_session.add(google_account)
    db_session.commit()

    payload = {
        "name": "My Google Calendar",
        "calendar_type": "google",
        "google_account_id": google_account.id,
        "calendar_ids": ["primary"],
        "tags": ["personal"],
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Google Calendar"
    assert data["calendar_type"] == "google"
    assert data["google_account_id"] == google_account.id

    # Verify in database
    account = db_session.query(CalendarAccount).filter_by(name="My Google Calendar").first()
    assert account is not None
    assert account.google_account_id == google_account.id


def test_create_caldav_account_missing_url_fails(client, db_session, user):
    """Create CalDAV account without caldav_url fails."""
    payload = {
        "name": "Incomplete CalDAV",
        "calendar_type": "caldav",
        "caldav_username": "user",
        "caldav_password": "pass",
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 400
    assert "caldav_url" in response.json()["detail"]


def test_create_caldav_account_missing_username_fails(client, db_session, user):
    """Create CalDAV account without caldav_username fails."""
    payload = {
        "name": "Incomplete CalDAV",
        "calendar_type": "caldav",
        "caldav_url": "https://cal.example.com",
        "caldav_password": "pass",
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 400
    assert "caldav_username" in response.json()["detail"]


def test_create_caldav_account_missing_password_fails(client, db_session, user):
    """Create CalDAV account without caldav_password fails."""
    payload = {
        "name": "Incomplete CalDAV",
        "calendar_type": "caldav",
        "caldav_url": "https://cal.example.com",
        "caldav_username": "user",
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 400
    assert "caldav_password" in response.json()["detail"]


def test_create_google_account_missing_google_account_id_fails(client, db_session, user):
    """Create Google account without google_account_id fails."""
    payload = {
        "name": "Incomplete Google",
        "calendar_type": "google",
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 400
    assert "google_account_id" in response.json()["detail"]


def test_create_google_account_invalid_google_account_id_fails(client, db_session, user):
    """Create Google account with non-existent google_account_id fails."""
    payload = {
        "name": "Invalid Google",
        "calendar_type": "google",
        "google_account_id": 999999,
    }

    response = client.post("/calendar-accounts", json=payload)

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


# ====== GET /calendar-accounts/{account_id} tests ======


def test_get_account_caldav_success(client, db_session, user):
    """Get CalDAV account by ID returns account details."""
    account = CalendarAccount(
        name="Test CalDAV",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    response = client.get(f"/calendar-accounts/{account.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == account.id
    assert data["name"] == "Test CalDAV"
    assert data["calendar_type"] == "caldav"
    assert data["caldav_url"] == "https://cal.example.com"


def test_get_account_google_with_google_account_info(client, db_session, user):
    """Get Google account includes google_account details."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="Test Google",
        email="test@gmail.com",
    )
    db_session.add(google_account)
    db_session.commit()

    account = CalendarAccount(
        name="Test Google Calendar",
        calendar_type="google",
        google_account_id=google_account.id,
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    response = client.get(f"/calendar-accounts/{account.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["google_account_id"] == google_account.id
    assert data["google_account"] is not None
    assert data["google_account"]["email"] == "test@gmail.com"


def test_get_account_not_found(client, db_session, user):
    """Get account returns 404 when account doesn't exist."""
    response = client.get("/calendar-accounts/999999")

    assert response.status_code == 404


# ====== PATCH /calendar-accounts/{account_id} tests ======


def test_update_account_name(client, db_session, user):
    """Update account name succeeds."""
    account = CalendarAccount(
        name="Original Name",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/calendar-accounts/{account.id}",
        json={"name": "Updated Name"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"

    # Verify in database
    db_session.refresh(account)
    assert account.name == "Updated Name"


@pytest.mark.parametrize(
    "field,value",
    [
        ("caldav_url", "https://new.example.com"),
        ("caldav_username", "newuser"),
        ("caldav_password", "newpass"),
        ("calendar_ids", ["cal1", "cal2"]),
        ("tags", ["new", "tags"]),
        ("check_interval", 30),
        ("sync_past_days", 60),
        ("sync_future_days", 180),
        ("active", False),
    ],
)
def test_update_account_fields(client, db_session, user, field, value):
    """Update account fields succeeds."""
    account = CalendarAccount(
        name="Test Account",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        calendar_ids=["old"],
        tags=["old"],
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
        active=True,
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/calendar-accounts/{account.id}",
        json={field: value},
    )

    assert response.status_code == 200

    # Verify in database
    db_session.refresh(account)
    assert getattr(account, field) == value


def test_update_account_google_account_id(client, db_session, user):
    """Update google_account_id with valid ID succeeds."""
    google_account = GoogleAccount(
        user_id=user.id,
        name="New Google",
        email="new@gmail.com",
    )
    db_session.add(google_account)
    db_session.commit()

    account = CalendarAccount(
        name="Test Account",
        calendar_type="google",
        google_account_id=None,
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/calendar-accounts/{account.id}",
        json={"google_account_id": google_account.id},
    )

    assert response.status_code == 200

    # Verify in database
    db_session.refresh(account)
    assert account.google_account_id == google_account.id


def test_update_account_invalid_google_account_id_fails(client, db_session, user):
    """Update google_account_id with non-existent ID fails."""
    account = CalendarAccount(
        name="Test Account",
        calendar_type="google",
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/calendar-accounts/{account.id}",
        json={"google_account_id": 999999},
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_update_account_not_found(client, db_session, user):
    """Update account returns 404 when account doesn't exist."""
    response = client.patch(
        "/calendar-accounts/999999",
        json={"name": "New Name"},
    )

    assert response.status_code == 404


# ====== DELETE /calendar-accounts/{account_id} tests ======


def test_delete_account_success(client, db_session, user):
    """Delete account succeeds."""
    account = CalendarAccount(
        name="Test Account",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()
    account_id = account.id

    response = client.delete(f"/calendar-accounts/{account_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify deleted from database
    deleted = db_session.query(CalendarAccount).filter_by(id=account_id).first()
    assert deleted is None


def test_delete_account_not_found(client, db_session, user):
    """Delete account returns 404 when not found."""
    response = client.delete("/calendar-accounts/999999")

    assert response.status_code == 404


# ====== POST /calendar-accounts/{account_id}/sync tests ======


@patch("memory.common.celery_app.app")
def test_trigger_sync_success(mock_app, client, db_session, user):
    """Trigger sync sends Celery task."""
    account = CalendarAccount(
        name="Test Account",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    mock_task = MagicMock()
    mock_task.id = "task-123-456"
    mock_app.send_task.return_value = mock_task

    response = client.post(f"/calendar-accounts/{account.id}/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "task-123-456"
    assert data["status"] == "scheduled"

    # Verify Celery task was sent
    mock_app.send_task.assert_called_once()
    call_args = mock_app.send_task.call_args
    assert call_args[1]["args"] == [account.id]
    assert call_args[1]["kwargs"] == {"force_full": False}


@patch("memory.common.celery_app.app")
def test_trigger_sync_with_force_full(mock_app, client, db_session, user):
    """Trigger sync with force_full parameter."""
    account = CalendarAccount(
        name="Test Account",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
    )
    db_session.add(account)
    db_session.commit()

    mock_task = MagicMock()
    mock_task.id = "task-789"
    mock_app.send_task.return_value = mock_task

    response = client.post(f"/calendar-accounts/{account.id}/sync?force_full=true")

    assert response.status_code == 200

    # Verify force_full was passed
    call_args = mock_app.send_task.call_args
    assert call_args[1]["kwargs"] == {"force_full": True}


@patch("memory.common.celery_app.app")
def test_trigger_sync_not_found(mock_app, client, db_session, user):
    """Trigger sync returns 404 when account doesn't exist."""
    response = client.post("/calendar-accounts/999999/sync")

    assert response.status_code == 404
    mock_app.send_task.assert_not_called()


# ====== Helper function tests ======


def test_account_to_response_caldav(db_session):
    """account_to_response converts CalDAV account."""
    from memory.api.calendar_accounts import account_to_response

    now = datetime.now(timezone.utc)
    account = CalendarAccount(
        id=1,
        name="Test CalDAV",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
        calendar_ids=["cal1"],
        tags=["work"],
        check_interval=15,
        sync_past_days=30,
        sync_future_days=90,
        last_sync_at=now,
        sync_error="Test error",
        active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(account)
    db_session.commit()

    response = account_to_response(account)

    assert response.id == 1
    assert response.name == "Test CalDAV"
    assert response.calendar_type == "caldav"
    assert response.caldav_url == "https://cal.example.com"
    assert response.calendar_ids == ["cal1"]
    assert response.tags == ["work"]
    assert response.last_sync_at == now.isoformat()
    assert response.sync_error == "Test error"


def test_account_to_response_google_with_account(db_session, user):
    """account_to_response converts Google account with GoogleAccount relation."""
    from memory.api.calendar_accounts import account_to_response

    google_account = GoogleAccount(
        user_id=user.id,
        name="Test Google",
        email="test@gmail.com",
    )
    db_session.add(google_account)
    db_session.commit()

    now = datetime.now(timezone.utc)
    account = CalendarAccount(
        id=2,
        name="Test Google Calendar",
        calendar_type="google",
        google_account_id=google_account.id,
        calendar_ids=["primary"],
        tags=["personal"],
        check_interval=30,
        sync_past_days=60,
        sync_future_days=180,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(account)
    db_session.commit()

    # Ensure relationship is loaded
    db_session.refresh(account)

    response = account_to_response(account)

    assert response.id == 2
    assert response.calendar_type == "google"
    assert response.google_account_id == google_account.id
    assert response.google_account is not None
    assert response.google_account.email == "test@gmail.com"
