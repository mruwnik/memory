"""Tests for Cloud Claude Code session management API."""

from unittest.mock import MagicMock, patch

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
    assert task.data["spawn_config"]["initial_prompt"] == "Review the latest changes and create a summary"


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
