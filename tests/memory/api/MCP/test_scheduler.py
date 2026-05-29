"""Tests for the scheduler MCP server (hybrid enabled property)."""
# pyright: reportFunctionMemberAccess=false

import uuid
from datetime import datetime, timezone

import pytest

from memory.api.MCP.servers import scheduler
from memory.common.db import connection as db_connection
from memory.common.db.models import ScheduledTask
from tests.conftest import mcp_auth_context


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.mark.asyncio
async def test_cancel_clears_next_scheduled_time(db_session, regular_user, user_session):
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=regular_user.id,
        task_type="notification",
        cron_expression="0 9 * * *",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(task)
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await scheduler.cancel.fn(task_id=task.id)

    assert result["task"]["enabled"] is False
    assert result["task"]["next_scheduled_time"] is None


@pytest.mark.asyncio
async def test_upsert_enable_recurring_recomputes(db_session, regular_user, user_session):
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=regular_user.id,
        task_type="notification",
        cron_expression="0 9 * * *",
        next_scheduled_time=None,
    )
    db_session.add(task)
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await scheduler.upsert.fn(task_id=task.id, enabled=True)

    assert result["enabled"] is True
    assert result["next_scheduled_time"] is not None


def test_validate_scheduled_secret_refs_rejects_unknown(db_session, regular_user):
    from memory.api.MCP.servers.scheduler import validate_scheduled_secret_refs
    with pytest.raises(ValueError, match="github_token"):
        validate_scheduled_secret_refs(
            db_session, regular_user.id, {"github_token": "no-such-secret"}
        )


def test_validate_scheduled_secret_refs_allows_no_tokens(db_session, regular_user):
    from memory.api.MCP.servers.scheduler import validate_scheduled_secret_refs
    validate_scheduled_secret_refs(db_session, regular_user.id, {"repo_url": "x"})


@pytest.mark.asyncio
async def test_create_notification_recurring(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.id):
        result = await scheduler.create.fn(
            task_type="notification", topic="Daily ping", message="hello",
            cron_expression="0 9 * * *",
            notification_channel="discord", notification_target="123",
        )
    assert result["task_type"] == "notification"
    assert result["cron_expression"] == "0 9 * * *"
    assert result["next_scheduled_time"] is not None
    assert result["enabled"] is True


@pytest.mark.asyncio
async def test_create_notification_one_time(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.id):
        result = await scheduler.create.fn(
            task_type="notification", topic="Once", message="hi",
            scheduled_time="2999-01-01T09:00:00Z",
            notification_channel="email", notification_target="a@b.c",
        )
    assert result["cron_expression"] is None
    assert result["next_scheduled_time"] == "2999-01-01T09:00:00Z"


@pytest.mark.asyncio
async def test_create_claude_session(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.id):
        result = await scheduler.create.fn(
            task_type="claude_session", message="Run the digest",
            cron_expression="0 9 * * *",
            spawn_config={"repo_url": "https://github.com/x/y"},
        )
    assert result["task_type"] == "claude_session"
    assert result["message"] == "Run the digest"
    assert result["data"]["spawn_config"] == {"repo_url": "https://github.com/x/y"}
    assert result["topic"] == "Run the digest"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,match", [
    (dict(task_type="bogus", message="x", cron_expression="0 9 * * *"), "task_type"),
    (dict(task_type="notification", message="x", notification_channel="discord",
          notification_target="1", cron_expression="0 9 * * *",
          scheduled_time="2999-01-01T09:00:00Z"), "exactly one"),
    (dict(task_type="notification", message="x", notification_channel="discord",
          notification_target="1"), "exactly one"),
    (dict(task_type="notification", message="x", notification_channel="discord",
          notification_target="1", cron_expression="*/1 * * * *"), "too short"),
    (dict(task_type="notification", cron_expression="0 9 * * *"), "require"),
    (dict(task_type="claude_session", cron_expression="0 9 * * *", message="p"), "spawn_config"),
    (dict(task_type="claude_session", cron_expression="0 9 * * *",
          spawn_config={"repo_url": "x"}), "initial prompt"),
    (dict(task_type="claude_session", cron_expression="0 9 * * *", message="p",
          spawn_config={"bogus_key": "x"}), "Unknown spawn_config"),
    (dict(task_type="claude_session", cron_expression="0 9 * * *", message="p",
          spawn_config={"github_token": "no-such"}), "Secret reference"),
])
async def test_create_validation_errors(db_session, regular_user, user_session, kwargs, match):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=match):
            await scheduler.create.fn(**kwargs)


@pytest.mark.asyncio
async def test_create_enforces_per_user_cap(db_session, regular_user, user_session, monkeypatch):
    from memory.common import settings as settings_mod
    monkeypatch.setattr(settings_mod, "MAX_SCHEDULED_TASKS_PER_USER", 1)
    with mcp_auth_context(user_session.id):
        await scheduler.create.fn(
            task_type="notification", message="a", cron_expression="0 9 * * *",
            notification_channel="discord", notification_target="1",
        )
        with pytest.raises(ValueError, match="Maximum"):
            await scheduler.create.fn(
                task_type="notification", message="b", cron_expression="0 10 * * *",
                notification_channel="discord", notification_target="1",
            )
