"""Tests for Cloud Claude Code session management API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.api.cloud_claude import (
    get_user_id_from_session,
    make_session_id,
    user_owns_session,
)
from memory.common import settings
from memory.common.db.models import ScheduledTask
from memory.common.db.models.secrets import decrypt_value, encrypt_value
from memory.common.db.models.users import HumanUser


# Tests for session ID generation and parsing


def test_make_session_id_includes_user_id():
    """Test that session IDs include the user ID prefix and source indicator."""
    # With no environment_id or snapshot_id, source is 'x'
    session_id = make_session_id(42)
    assert session_id.startswith("u42-x-")
    # Should have random hex after the source indicator
    random_part = session_id.split("-")[2]
    assert len(random_part) == 12  # 6 bytes = 12 hex chars

    # With environment_id
    session_id = make_session_id(42, environment_id=5)
    assert session_id.startswith("u42-e5-")

    # With snapshot_id
    session_id = make_session_id(42, snapshot_id=10)
    assert session_id.startswith("u42-s10-")


def test_make_session_id_unique():
    """Test that session IDs are unique."""
    ids = {make_session_id(1) for _ in range(100)}
    assert len(ids) == 100  # All unique


def test_get_user_id_from_session_valid():
    """Test extracting user ID from valid session IDs."""
    assert get_user_id_from_session("u42-abc123") == 42
    assert get_user_id_from_session("u1-deadbeef") == 1
    assert get_user_id_from_session("u999-xyz") == 999


def test_get_user_id_from_session_invalid():
    """Test that invalid session IDs return None."""
    assert get_user_id_from_session("abc123") is None  # No u prefix
    assert get_user_id_from_session("uabc-123") is None  # Non-numeric user id
    assert get_user_id_from_session("") is None  # Empty
    assert get_user_id_from_session("u") is None  # Just prefix


def test_user_owns_session():
    """Test user ownership check."""
    user = MagicMock()
    user.id = 42

    assert user_owns_session(user, "u42-abc123") is True
    assert user_owns_session(user, "u42-xyz789") is True
    assert user_owns_session(user, "u1-abc123") is False
    assert user_owns_session(user, "invalid") is False


# Tests for SSH key encryption (in users.py, using secrets module)


def test_ssh_key_encryption_roundtrip():
    """Test that SSH keys can be encrypted and decrypted."""
    test_key = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBbeW91cl9rZXlfaGVyZV0AAAAA
-----END OPENSSH PRIVATE KEY-----"""

    with patch(
        "memory.common.settings.SECRETS_ENCRYPTION_KEY",
        "test-secret-key-32-chars-minimum!",
    ):
        encrypted = encrypt_value(test_key)
        decrypted = decrypt_value(encrypted)

    assert decrypted == test_key
    assert encrypted != test_key.encode()


def test_ssh_key_encryption_requires_secret():
    """Test that encryption fails without a secret."""
    with patch("memory.common.settings.SECRETS_ENCRYPTION_KEY", ""):
        with pytest.raises(ValueError) as exc_info:
            encrypt_value("test key")

    assert "SECRETS_ENCRYPTION_KEY must be set" in str(exc_info.value)


def test_ssh_key_user_property(db_session):
    """Test that User.ssh_private_key property encrypts/decrypts."""
    with patch(
        "memory.common.settings.SECRETS_ENCRYPTION_KEY",
        "test-secret-key-32-chars-minimum!",
    ):
        user = HumanUser.create_with_password(
            email="ssh@example.com", name="SSH User", password="test123"
        )
        db_session.add(user)
        db_session.commit()

        # Set private key
        user.ssh_private_key = "test-private-key"
        db_session.commit()

        # Verify it's stored encrypted
        assert user.ssh_private_key_encrypted is not None
        assert user.ssh_private_key_encrypted != b"test-private-key"

        # Verify it decrypts correctly
        assert user.ssh_private_key == "test-private-key"


def test_ssh_key_user_property_none(db_session):
    """Test that None ssh_private_key is handled correctly."""
    with patch(
        "memory.common.settings.SECRETS_ENCRYPTION_KEY",
        "test-secret-key-32-chars-minimum!",
    ):
        user = HumanUser.create_with_password(
            email="nossh@example.com", name="No SSH User", password="test123"
        )
        db_session.add(user)
        db_session.commit()

        # Should be None by default
        assert user.ssh_private_key is None
        assert user.ssh_private_key_encrypted is None

        # Setting to None should work
        user.ssh_private_key = None
        assert user.ssh_private_key_encrypted is None


# --- Schedule endpoint tests ---


def test_schedule_request_invalid_cron(client, user):
    """Test that invalid cron expression returns 400."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "not a cron",
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test prompt",
            },
        },
    )
    assert response.status_code == 400
    assert "Invalid cron expression" in response.json()["detail"]


def test_schedule_requires_initial_prompt(client, user):
    """Test that missing initial_prompt returns 400."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "0 9 * * *",
            "spawn_config": {
                "environment_id": 1,
            },
        },
    )
    assert response.status_code == 400
    assert "initial_prompt" in response.json()["detail"]


def test_schedule_creates_scheduled_task(client, user, db_session):
    """Test that a valid schedule request creates a ScheduledTask in the DB."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "0 9 * * *",
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "Review the latest changes and create a summary",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cron_expression"] == "0 9 * * *"
    assert data["task_id"]
    assert data["next_scheduled_time"]
    assert "Review the latest changes" in data["topic"]

    # Verify it's in the database
    task = db_session.query(ScheduledTask).filter(ScheduledTask.id == data["task_id"]).first()
    assert task is not None
    assert task.task_type == "claude_session"
    assert task.enabled is True
    assert task.data["spawn_config"]["environment_id"] == 1
    # initial_prompt is stored in task.message, not in spawn_config
    assert "initial_prompt" not in task.data["spawn_config"]
    assert task.message == "Review the latest changes and create a summary"


def test_schedule_rejects_too_frequent_cron(client, user):
    """Test that cron expressions with intervals below the minimum are rejected."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "* * * * *",  # every minute
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test prompt",
            },
        },
    )
    assert response.status_code == 400
    assert "Cron interval too short" in response.json()["detail"]


def test_schedule_rejects_over_per_user_limit(client, user, db_session):
    """Test that exceeding the per-user scheduled task limit is rejected."""
    # Create MAX_SCHEDULED_TASKS_PER_USER tasks to fill the quota
    for i in range(settings.MAX_SCHEDULED_TASKS_PER_USER):
        task = ScheduledTask(
            user_id=user.id,
            task_type="claude_session",
            topic=f"Task {i}",
            data={"spawn_config": {"environment_id": 1, "initial_prompt": f"prompt {i}"}},
            cron_expression="0 9 * * *",
            enabled=True,
        )
        db_session.add(task)
    db_session.commit()

    # The next schedule attempt should be rejected
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "0 9 * * *",
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "one too many",
            },
        },
    )
    assert response.status_code == 400
    assert "Maximum" in response.json()["detail"]


def test_schedule_stores_enable_playwright_in_spawn_config(client, user, db_session):
    """Test that enable_playwright is stored in the scheduled task's spawn_config."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "0 9 * * *",
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "Run playwright tests",
                "enable_playwright": True,
            },
        },
    )
    assert response.status_code == 200
    data = response.json()

    task = db_session.query(ScheduledTask).filter(ScheduledTask.id == data["task_id"]).first()
    assert task is not None
    assert task.data["spawn_config"]["enable_playwright"] is True


def test_schedule_enable_playwright_defaults_false(client, user, db_session):
    """Test that enable_playwright defaults to False when not specified."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "0 9 * * *",
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "Normal session",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()

    task = db_session.query(ScheduledTask).filter(ScheduledTask.id == data["task_id"]).first()
    assert task is not None
    assert task.data["spawn_config"].get("enable_playwright") is False


def test_spawn_request_enable_playwright_cannot_be_set_via_custom_env():
    """Test that ENABLE_PLAYWRIGHT is in the reserved env vars list."""
    from memory.api.cloud_claude import RESERVED_ENV_VARS

    assert "ENABLE_PLAYWRIGHT" in RESERVED_ENV_VARS


def test_schedule_rejects_six_field_cron(client, user):
    """Test that 6-field cron expressions (with seconds) are rejected."""
    response = client.post(
        "/claude/schedule",
        json={
            "cron_expression": "0 0 9 * * *",  # 6 fields
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test prompt",
            },
        },
    )
    assert response.status_code == 400
    assert "5-field" in response.json()["detail"]


# --- Stats endpoint tests ---

# A snapshot covering both user 1 (the test client's user) and user 9999 (other),
# plus a non-running container. Used by all the /claude/stats* tests.
_STATS_SNAPSHOT = {
    "ts": "2026-04-27T14:18:05.813523+00:00",
    "global": {
        "running": 2, "max": 12,
        "memory_mb": {"used": 1200, "allocated": 12288, "max": 49152},
        "cpus": {"used": 0.07, "allocated": 4.0, "max": 8},
    },
    "containers": [
        {
            "id": "u1-e1-aaaa1111",
            "status": "running",
            "allocated": {"memory_mb": 6144, "cpus": 2.0},
            "used": {"memory_mb": 600, "memory_pct": 9.74, "cpu_pct": 4.05},
        },
        {
            "id": "u9999-e2-bbbb2222",
            "status": "running",
            "allocated": {"memory_mb": 6144, "cpus": 2.0},
            "used": {"memory_mb": 612, "memory_pct": 9.95, "cpu_pct": 3.80},
        },
        {
            "id": "u1-e3-cccc3333",
            "status": "exited",
            "allocated": {"memory_mb": 6144, "cpus": 2.0},
            "used": None,
        },
    ],
}


_HISTORY_RESULT = {
    "points": [
        {"ts": "2026-04-27T14:00:00+00:00", "session_id": "u1-e1-aaaa1111",
         "cpu_pct": 1.0, "memory_mb": 100, "memory_pct": 1.6},
        {"ts": "2026-04-27T14:00:30+00:00", "session_id": "u9999-e2-bbbb2222",
         "cpu_pct": 2.0, "memory_mb": 200, "memory_pct": 3.2},
        {"ts": "2026-04-27T14:01:00+00:00", "session_id": "u1-e1-aaaa1111",
         "cpu_pct": 3.0, "memory_mb": 110, "memory_pct": 1.8},
    ],
    "count": 3,
    "truncated": False,
}


def test_stats_admin_sees_full_snapshot(client, user):
    """Admins (scopes=['*']) get the orchestrator response unmodified —
    other users' containers, the global block, everything."""
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats",
        new=AsyncMock(return_value=_STATS_SNAPSHOT),
    ):
        response = client.get("/claude/stats")
    assert response.status_code == 200
    data = response.json()
    assert data == _STATS_SNAPSHOT


def test_stats_regular_user_filtered_no_global(regular_client, user):
    """Non-admin users see only their own containers and no global block.
    The global block leaks orchestrator-wide capacity, which is an operator
    concern — surfacing it to every user is information disclosure."""
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats",
        new=AsyncMock(return_value=_STATS_SNAPSHOT),
    ):
        response = regular_client.get("/claude/stats")
    assert response.status_code == 200
    data = response.json()
    assert "global" not in data
    ids = sorted(c["id"] for c in data["containers"])
    assert ids == ["u1-e1-aaaa1111", "u1-e3-cccc3333"]


def test_stats_orchestrator_down_returns_502(client, user):
    """Orchestrator unreachable → 502, not a misleading 200."""
    from memory.api.orchestrator_client import OrchestratorError

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats",
        new=AsyncMock(side_effect=OrchestratorError("socket missing")),
    ):
        response = client.get("/claude/stats")
    assert response.status_code == 502


def test_container_stats_admin_can_view_others(client, user):
    """Admin can fetch stats for any session including ones they don't own."""
    payload = _STATS_SNAPSHOT["containers"][1]
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.container_stats",
        new=AsyncMock(return_value=payload),
    ):
        response = client.get("/claude/u9999-e2-bbbb2222/stats")
    assert response.status_code == 200
    assert response.json() == payload


def test_container_stats_regular_user_404_on_other_user(regular_client, user):
    """Non-admin requesting another user's session gets 404 (not 403) —
    same shape as kill_session and friends, so we don't disclose existence."""
    response = regular_client.get("/claude/u9999-e2-bbbb2222/stats")
    assert response.status_code == 404


def test_container_stats_404_when_session_unknown(client, user):
    """Orchestrator returning 404 (None from client) propagates as 404."""
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.container_stats",
        new=AsyncMock(return_value=None),
    ):
        response = client.get("/claude/u1-x-deadbeef/stats")
    assert response.status_code == 404


def test_container_stats_orchestrator_status_propagates(client, user):
    """If the orchestrator raises with a status_code, the API forwards it
    unchanged rather than squashing to 502 — mirrors stats_history behavior."""
    from memory.api.orchestrator_client import OrchestratorError

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.container_stats",
        new=AsyncMock(
            side_effect=OrchestratorError("orch unavailable", status_code=503)
        ),
    ):
        response = client.get("/claude/u1-e1-aaaa1111/stats")
    assert response.status_code == 503
    assert "orch unavailable" in response.json()["detail"]


def test_stats_history_non_admin_without_session_id_400(regular_client, user):
    """Non-admin must specify `session_id` — calling without one would force
    the orchestrator to materialize every user's history just so the API
    can filter it back down. That's a soft-DoS vector, so we reject early."""
    response = regular_client.get("/claude/stats/history")
    assert response.status_code == 400
    assert "session_id" in response.json()["detail"]


def test_stats_history_non_admin_with_own_session_id(regular_client, user):
    """Non-admin happy path: with `session_id` set to one they own, the
    orchestrator is called with that id and the result is returned as-is."""
    captured = {}

    async def fake_history(self, *, session_id=None, since=None, max_points=1000):
        captured["session_id"] = session_id
        return _HISTORY_RESULT

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats_history",
        new=fake_history,
    ):
        response = regular_client.get(
            "/claude/stats/history?session_id=u1-e1-aaaa1111"
        )
    assert response.status_code == 200
    assert captured["session_id"] == "u1-e1-aaaa1111"
    assert response.json() == _HISTORY_RESULT


def test_stats_history_admin_sees_all_points(client, user):
    """Admin gets the orchestrator response untouched."""
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats_history",
        new=AsyncMock(return_value=_HISTORY_RESULT),
    ):
        response = client.get("/claude/stats/history")
    assert response.status_code == 200
    assert response.json() == _HISTORY_RESULT


def test_stats_history_404_on_other_user_session(regular_client, user):
    """Asking for another user's session by id is 404 — never reaches the
    orchestrator. (If we let it through, a regular user could probe which
    session ids exist.)"""
    response = regular_client.get(
        "/claude/stats/history?session_id=u9999-e2-bbbb2222"
    )
    assert response.status_code == 404


def test_stats_history_passes_query_params_through(client, user):
    """`since` and `max` round-trip cleanly to the orchestrator client.
    The wire name is `max=` (orchestrator's parameter); inside Python
    we pass it as `max_points=` to avoid shadowing the builtin."""
    captured = {}

    async def fake_history(self, *, session_id=None, since=None, max_points=1000):
        captured["session_id"] = session_id
        captured["since"] = since
        captured["max_points"] = max_points
        return _HISTORY_RESULT

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats_history",
        new=fake_history,
    ):
        response = client.get(
            "/claude/stats/history?"
            "session_id=u1-e1-aaaa1111&since=2026-04-27T13:00:00Z&max=200"
        )
    assert response.status_code == 200
    assert captured == {
        "session_id": "u1-e1-aaaa1111",
        "since": "2026-04-27T13:00:00Z",
        "max_points": 200,
    }


def test_stats_history_max_out_of_range_422(client, user):
    """FastAPI's Query(ge=1, le=10000) rejects max=20000 with a 422.
    (This is a behavior contract; the orchestrator's own 400 is unreachable
    once FastAPI validates first.)"""
    response = client.get("/claude/stats/history?max=20000")
    assert response.status_code == 422


def test_stats_history_orchestrator_400_propagates(client, user):
    """If the orchestrator returns a 400 (e.g. bad `since`), the API forwards
    it unchanged rather than squashing to 502 — pins the
    `status = e.status_code if e.status_code else 502` propagation behavior."""
    from memory.api.orchestrator_client import OrchestratorError

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.stats_history",
        new=AsyncMock(side_effect=OrchestratorError("bad since", status_code=400)),
    ):
        response = client.get("/claude/stats/history?since=not-a-date")
    assert response.status_code == 400
    assert "bad since" in response.json()["detail"]
