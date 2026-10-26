"""Tests for user management API endpoints."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from memory.api.users import check_single_user
from memory.common.db.models import APIKey, HumanUser, Person, ScheduledTask, UserSession
from memory.common.db.models.users import verify_password
from memory.common.modules import MODULES, MULTI_USER

ALL_MODULES = frozenset(MODULES)


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


def test_create_my_api_key_unknown_type_rejected(client: TestClient, admin_user):
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Retired Key", "key_type": "one_time"},
    )

    assert response.status_code == 422


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


@pytest.mark.parametrize("key_type", [
    "internal",
    "discord",
    "google",
    "github",
    "mcp",
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
    assert key_data["key_preview"].startswith("internal")
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
    assert {"teams", "teams:write", "projects", "projects:write"} <= set(scope_values)


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


def test_stored_unknown_user_scope_is_hidden_and_resave_succeeds(
    client: TestClient, admin_user, db_session
):
    created = client.post(
        "/users",
        json={
            "name": "Stale Scope",
            "email": "stale_scope@example.com",
            "password": "testpass123",
            "scopes": ["read"],
        },
    ).json()
    user = db_session.get(HumanUser, created["id"])
    user.scopes = ["read", "discord-admin"]
    db_session.commit()

    listed = client.get(f"/users/{created['id']}").json()
    assert listed["scopes"] == ["read"]

    response = client.patch(f"/users/{created['id']}", json={"scopes": listed["scopes"]})
    assert response.status_code == 200


def test_stored_unknown_api_key_scope_is_hidden(client: TestClient, admin_user, db_session):
    created = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Stale Key", "scopes": ["read"]},
    ).json()
    key = db_session.get(APIKey, created["id"])
    key.scopes = ["read", "discord-admin:write"]
    db_session.commit()

    keys = client.get(f"/users/{admin_user.id}/api-keys").json()
    assert [k["scopes"] for k in keys if k["id"] == created["id"]] == [["read"]]


def test_update_user_refuses_removing_own_admin_scope(client: TestClient, admin_user, db_session):
    response = client.patch(f"/users/{admin_user.id}", json={"scopes": ["read"]})

    assert response.status_code == 400
    assert "your own" in response.json()["detail"]
    db_session.refresh(admin_user)
    assert admin_user.scopes == ["*"]


def test_reset_password_sets_it_and_signs_the_user_out(client: TestClient, admin_user, db_session):
    target = HumanUser.create_with_password(email="reset@example.com", name="Reset Me", password="oldpass123")
    db_session.add(target)
    db_session.flush()
    db_session.add(UserSession(user_id=target.id, expires_at=datetime.now() + timedelta(days=1)))
    db_session.commit()

    response = client.post(f"/users/{target.id}/reset-password", json={"new_password": "newpass123"})

    assert response.status_code == 200
    assert "newpass123" not in response.text
    db_session.refresh(target)
    assert verify_password("newpass123", target.password_hash or "")
    assert db_session.query(UserSession).filter(UserSession.user_id == target.id).count() == 0


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


@pytest.mark.parametrize(
    "user_type, modules, status",
    [("human", frozenset(), 403), ("bot", frozenset(), 200), ("human", ALL_MODULES, 200)],
)
def test_create_user_allows_one_human_without_multi_user(
    client: TestClient, admin_user, set_modules, user_type, modules, status
):
    set_modules(modules)

    response = client.post(
        "/users",
        json={
            "name": "Second",
            "email": "second@example.com",
            "password": "testpass123",
            "user_type": user_type,
            "scopes": ["read"],
        },
    )

    assert response.status_code == status


@pytest.mark.parametrize(
    "contact_info, links_to_contact",
    [
        ({"email": "Alice@Example.com"}, True),
        ({"email": "someone@else.com"}, False),
        ({}, False),
    ],
)
def test_create_user_links_an_existing_person_only_by_email(
    client: TestClient, admin_user, db_session, set_modules, contact_info, links_to_contact
):
    """Names and aliases are free text whoever created the Person chose, so a
    same-named contact must not become the new user's Person."""
    set_modules(ALL_MODULES)
    contact = Person(
        identifier="alice_smith",
        display_name="Alice Smith",
        aliases=["Alice Smith"],
        contact_info=contact_info,
    )
    db_session.add(contact)
    db_session.commit()

    response = client.post(
        "/users",
        json={"name": "Alice Smith", "email": "alice@example.com", "password": "testpass123"},
    )

    assert response.status_code == 200
    user_id = response.json()["id"]
    db_session.expire_all()
    assert (db_session.get(Person, contact.id).user_id == user_id) is links_to_contact
    assert db_session.query(Person).filter(Person.user_id == user_id).count() == 1


def test_create_user_points_a_taken_email_at_the_email_field(client: TestClient, admin_user):
    response = client.post(
        "/users",
        json={"name": "Bot", "email": admin_user.email, "user_type": "bot", "scopes": ["read"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == [
        {"loc": ["body", "email"], "msg": "User with this email already exists", "type": "value_error"}
    ]


def add_humans(db_session, *scopes: list[str]):
    for i, user_scopes in enumerate(scopes):
        human = HumanUser.create_with_password(
            email=f"human{i}@example.com", name=f"Human {i}", password="pw123456"
        )
        human.scopes = user_scopes
        db_session.add(human)
    db_session.commit()


def test_check_single_user_refuses_two_humans_without_multi_user(db_session):
    add_humans(db_session, ["*"], ["*"])

    with pytest.raises(SystemExit, match="^2 human users exist; set ENABLE_MODULES=multi_user or remove users$"):
        check_single_user(db_session, frozenset())


def test_check_single_user_allows_two_humans_with_multi_user(db_session, caplog):
    add_humans(db_session, ["read"], ["*"])

    check_single_user(db_session, frozenset({MULTI_USER}))

    assert not caplog.records


@pytest.mark.parametrize("scopes, warned", [(["read"], True), (["*"], False)])
def test_check_single_user_warns_when_sole_human_lacks_full_access(db_session, caplog, scopes, warned):
    add_humans(db_session, scopes)

    check_single_user(db_session, frozenset())

    assert any("lacks the * scope" in r.getMessage() for r in caplog.records) == warned


def test_create_api_key_scope_escalation_blocked(regular_client: TestClient, user, db_session):
    """Regular user cannot create an API key with admin (*) scope they don't have."""
    response = regular_client.post(
        f"/users/{user.id}/api-keys",
        json={"name": "Escalated Key", "scopes": ["*"]},
    )

    assert response.status_code == 403
    assert "cannot grant" in response.json()["detail"].lower() or "scope" in response.json()["detail"].lower()


def test_create_api_key_invalid_scope_rejected(client: TestClient, admin_user, db_session):
    """API key creation rejects unknown scope strings."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Bad Scope Key", "scopes": ["totally_fake_scope"]},
    )

    assert response.status_code == 400
    assert "Invalid scopes" in response.json()["detail"]


def test_create_api_key_admin_can_grant_full_scope(client: TestClient, admin_user, db_session):
    """Admin can create a key with full (*) scope."""
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Admin Full Scope Key", "scopes": ["*"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["scopes"] == ["*"]


@pytest.mark.parametrize(
    "body,run_cap,daily_cap",
    [
        ({}, None, None),
        (
            {"agent_max_run_cost_usd": "0.25", "agent_max_daily_cost_usd": "5"},
            "0.250000",
            "5.000000",
        ),
    ],
)
def test_create_api_key_with_agent_caps(
    client: TestClient, admin_user, db_session, body, run_cap, daily_cap
):
    response = client.post(f"/users/{admin_user.id}/api-keys", json={"name": "agents"} | body)

    assert response.status_code == 200
    data = response.json()
    assert (data["agent_max_run_cost_usd"], data["agent_max_daily_cost_usd"]) == (
        run_cap,
        daily_cap,
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"agent_max_run_cost_usd": "0"}, "greater than 0"),
    ],
)
def test_api_key_agent_caps_must_be_positive(
    client: TestClient, admin_user, db_session, body, expected
):
    response = client.post(f"/users/{admin_user.id}/api-keys", json={"name": "agents"} | body)

    assert response.status_code in (400, 422)
    assert expected in response.text


# --- Timezone Tests ---


@pytest.mark.parametrize("tz", ["Europe/London", "Asia/Kolkata", "UTC"])
def test_user_sets_own_timezone(regular_client: TestClient, user, db_session, tz):
    response = regular_client.patch(f"/users/{user.id}", json={"timezone": tz})

    assert response.status_code == 200
    assert response.json()["timezone"] == tz
    db_session.refresh(user)
    assert user.timezone == tz


@pytest.mark.parametrize("tz", ["Mars/Olympus_Mons", "", "../../etc/passwd"])
def test_update_user_rejects_unknown_timezone(client: TestClient, admin_user, db_session, tz):
    response = client.patch(f"/users/{admin_user.id}", json={"timezone": tz})

    assert response.status_code == 422
    db_session.refresh(admin_user)
    assert admin_user.timezone == "UTC"


def test_admin_sets_another_users_timezone(client: TestClient, admin_user, db_session):
    target = HumanUser.create_with_password(email="tz_target@example.com", name="TZ", password="pass12345")
    db_session.add(target)
    db_session.commit()

    response = client.patch(f"/users/{target.id}", json={"timezone": "Asia/Tokyo"})

    assert (response.status_code, response.json()["timezone"]) == (200, "Asia/Tokyo")
    assert client.get(f"/users/{target.id}").json()["timezone"] == "Asia/Tokyo"


def test_regular_user_cannot_set_another_users_timezone(regular_client: TestClient, user, db_session):
    target = HumanUser.create_with_password(email="tz_other@example.com", name="Other", password="pass12345")
    db_session.add(target)
    db_session.commit()

    response = regular_client.patch(f"/users/{target.id}", json={"timezone": "Asia/Tokyo"})

    assert response.status_code == 403
    db_session.refresh(target)
    assert target.timezone == "UTC"


def test_changing_timezone_moves_pending_cron_runs(client: TestClient, admin_user, db_session):
    def task(cron):
        return ScheduledTask(
            user_id=admin_user.id, task_type="notification", cron_expression=cron,
            next_scheduled_time=datetime(2999, 1, 1, 9),
        )
    recurring, paused, one_off = task("0 9 * * *"), task("0 9 * * *"), task(None)
    paused.next_scheduled_time = None
    db_session.add_all([recurring, paused, one_off])
    db_session.commit()

    client.patch(f"/users/{admin_user.id}", json={"timezone": "Asia/Kolkata"})

    db_session.refresh(recurring)
    db_session.refresh(paused)
    db_session.refresh(one_off)
    assert recurring.next_scheduled_time is not None
    assert (recurring.next_scheduled_time.hour, recurring.next_scheduled_time.minute) == (3, 30)
    assert paused.next_scheduled_time is None
    assert one_off.next_scheduled_time == datetime(2999, 1, 1, 9)


def test_revoke_my_api_key(client: TestClient, admin_user, db_session):
    key_id = client.post(f"/users/{admin_user.id}/api-keys", json={"name": "To Revoke"}).json()["id"]

    response = client.delete(f"/users/{admin_user.id}/api-keys/{key_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    keys = client.get(f"/users/{admin_user.id}/api-keys").json()
    assert [k["revoked"] for k in keys if k["id"] == key_id] == [True]


def test_revoke_nonexistent_key(client: TestClient, admin_user):
    response = client.delete(f"/users/{admin_user.id}/api-keys/99999")

    assert response.status_code == 404


def test_admin_revoke_user_api_key(client: TestClient, admin_user, db_session):
    other_user = HumanUser.create_with_password(
        email="other3@example.com", name="Other User 3", password="otherpass123"
    )
    db_session.add(other_user)
    db_session.commit()
    key = APIKey.create(user_id=other_user.id, name="To Revoke")
    db_session.add(key)
    db_session.commit()

    response = client.delete(f"/users/{other_user.id}/api-keys/{key.id}")

    assert response.status_code == 200
    db_session.refresh(key)
    assert key.revoked is True


@pytest.fixture
def someone_elses_key(db_session):
    owner = HumanUser.create_with_password(
        email="owner@example.com", name="Owner", password="ownerpass123"
    )
    db_session.add(owner)
    db_session.commit()
    key = APIKey.create(user_id=owner.id, name="Owner key")
    db_session.add(key)
    db_session.commit()
    return key


def test_regular_user_cannot_revoke_another_users_key(
    regular_client: TestClient, someone_elses_key, db_session
):
    key = someone_elses_key

    response = regular_client.delete(f"/users/{key.user_id}/api-keys/{key.id}")

    assert response.status_code == 403
    db_session.refresh(key)
    assert key.revoked is False


def test_revoke_rejects_a_key_of_another_user_under_own_id(
    regular_client: TestClient, user, someone_elses_key, db_session
):
    key = someone_elses_key

    response = regular_client.delete(f"/users/{user.id}/api-keys/{key.id}")

    assert response.status_code == 404
    db_session.refresh(key)
    assert key.revoked is False


def test_rotate_api_keys_revokes_only_target_users_keys(client, admin_user, someone_elses_key, db_session):
    old = client.post(f"/users/{admin_user.id}/api-keys", json={"name": "Old"}).json()
    response = client.post(
        f"/users/{admin_user.id}/api-keys",
        json={"name": "Replacement", "revoke_existing": True},
    )
    assert response.status_code == 200
    replacement = response.json()
    assert replacement["key"]
    assert replacement["revoked"] is False
    db_session.expire_all()
    assert db_session.get(APIKey, old["id"]).revoked is True
    assert db_session.get(APIKey, replacement["id"]).revoked is False
    assert someone_elses_key.revoked is False


def test_regular_user_cannot_rotate_someone_elses_keys(regular_client, someone_elses_key, db_session):
    response = regular_client.post(
        f"/users/{someone_elses_key.user_id}/api-keys", json={"revoke_existing": True}
    )
    assert response.status_code == 403
    db_session.refresh(someone_elses_key)
    assert someone_elses_key.revoked is False


@pytest.mark.transactional_db
def test_rotation_rolls_back_revocation_when_insert_fails(db_session, admin_user):
    from sqlalchemy import event
    from memory.api.users import APIKeyCreate, create_user_api_key

    old = APIKey.create(user_id=admin_user.id, name="Old")
    db_session.add(old)
    db_session.commit()
    old_id = old.id
    user_id = admin_user.id

    def reject_insert(mapper, connection, target):
        raise RuntimeError("Insert failed")

    event.listen(APIKey, "before_insert", reject_insert)
    try:
        with pytest.raises(RuntimeError, match="Insert failed"):
            create_user_api_key(user_id, APIKeyCreate(revoke_existing=True), admin_user, db_session)
    finally:
        event.remove(APIKey, "before_insert", reject_insert)
        db_session.rollback()
    assert db_session.get(APIKey, old_id).revoked is False
    assert db_session.query(APIKey).filter(APIKey.user_id == user_id).count() == 1


@pytest.mark.transactional_db
def test_concurrent_rotations_leave_one_live_key(db_session, db_engine, admin_user):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from memory.api.users import APIKeyCreate, create_user_api_key
    from memory.common.db.models import User

    user_id = admin_user.id
    barrier = Barrier(2)

    def rotate():
        with Session(db_engine) as session:
            owner = session.get(User, user_id)
            assert owner is not None
            barrier.wait(timeout=10)
            return create_user_api_key(user_id, APIKeyCreate(revoke_existing=True), owner, session).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(rotate)
        second = pool.submit(rotate)
        ids = {first.result(timeout=20), second.result(timeout=20)}
    db_session.expire_all()
    live = db_session.query(APIKey).filter(APIKey.user_id == user_id, APIKey.revoked.is_(False)).all()
    assert len(ids) == 2
    assert len(live) == 1
    assert live[0].id in ids


@pytest.mark.parametrize("loaded", [False, True])
def test_delete_user_with_owned_sources(client, db_session, regular_user, loaded):
    from memory.common.db.models import ArticleFeed, EmailAccount, User
    from memory.common.db.models.watchlist import WatchedMarket
    from memory.common.db.models.sources import CalendarAccount, GithubAccount, GoogleAccount, TranscriptAccount
    from memory.common.db.models.claude_config import ClaudeConfigSnapshot, ClaudeEnvironment

    user_id = regular_user.id
    mail = EmailAccount(user_id=user_id, name="Mail", email_address="delete@example.com")
    feed = ArticleFeed(user_id=user_id, url="https://example.com/delete-feed")
    market = WatchedMarket(user_id=user_id, market_id="delete-market", source="manifold")
    owned = {
        "email_accounts": mail,
        "article_feeds": feed,
        "watched_markets": market,
        "github_accounts": GithubAccount(user_id=user_id, name="GitHub", auth_type="pat"),
        "google_accounts": GoogleAccount(user_id=user_id, name="Google", email="delete@gmail.com"),
        "calendar_accounts": CalendarAccount(user_id=user_id, name="Calendar", calendar_type="caldav"),
        "transcript_accounts": TranscriptAccount(user_id=user_id, name="Transcripts", provider="generic"),
        "claude_snapshots": ClaudeConfigSnapshot(user_id=user_id, name="Snapshot", content_hash="test", filename="test.tar", size=0),
        "claude_environments": ClaudeEnvironment(user_id=user_id, name="Environment", volume_name="test-delete-user"),
    }
    person = Person(user_id=user_id, identifier="retained-contact", display_name="Contact to retain")
    bystander = EmailAccount(user_id=1, name="Other mail", email_address="keep@example.com")
    db_session.add_all([*owned.values(), person, bystander])
    db_session.commit()
    person_id, bystander_id = person.id, bystander.id
    ids = [(type(item), item.id) for item in owned.values()]
    if loaded:
        for name in owned:
            list(getattr(regular_user, name))

    response = client.delete(f"/users/{user_id}")

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, user_id) is None
    for model, item_id in ids:
        assert db_session.get(model, item_id) is None
    retained_person = db_session.get(Person, person_id)
    assert retained_person is not None and retained_person.user_id is None
    assert db_session.get(EmailAccount, bystander_id) is not None


def test_delete_user_with_oauth_refresh_tokens(client, db_session, regular_user):
    from memory.common.db.models.users import OAuthClientInformation, OAuthRefreshToken, User

    user_id = regular_user.id
    oauth_client = OAuthClientInformation(
        client_id="delete-user-client", client_id_issued_at=datetime.now().timestamp(),
        redirect_uris=["https://example.com/callback"], token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"], response_types=["code"],
        scope="read", client_name="Deletion test",
    )
    session = UserSession(user_id=user_id, expires_at=datetime.now() + timedelta(hours=1))
    db_session.add_all([oauth_client, session])
    db_session.flush()
    token = OAuthRefreshToken(
        client_id=oauth_client.client_id, user_id=user_id, scopes=["read"],
        expires_at=datetime.now() + timedelta(days=1), access_token_session_id=session.id,
    )
    db_session.add(token)
    db_session.commit()
    token_id, session_id = token.id, session.id

    response = client.delete(f"/users/{user_id}")

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, user_id) is None
    assert db_session.get(OAuthRefreshToken, token_id) is None
    assert db_session.get(UserSession, session_id) is None
    assert db_session.get(OAuthClientInformation, "delete-user-client") is not None
