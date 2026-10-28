"""Tests for the scheduler MCP server (hybrid enabled property)."""
# pyright: reportFunctionMemberAccess=false

import uuid
from datetime import datetime, timezone

import pytest

from memory.api.MCP.servers import scheduler
from memory.common import settings as settings_mod
from memory.common.db import connection as db_connection
from memory.common.db.models import APIKey, ScheduledTask
from tests.conftest import mcp_auth_context


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


@pytest.mark.asyncio
async def test_cancel_clears_next_scheduled_time(
    db_session, regular_user, user_session
):
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
async def test_upsert_enable_recurring_recomputes(
    db_session, regular_user, user_session
):
    regular_user.timezone = "Asia/Kolkata"
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
    assert result["next_scheduled_time"].endswith("T03:30:00Z")


@pytest.mark.asyncio
async def test_create_notification_recurring(db_session, regular_user, user_session):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()
    with mcp_auth_context(user_session.id):
        result = await scheduler.upsert.fn(
            task_type="notification",
            topic="Daily ping",
            message="hello",
            cron_expression="0 9 * * *",
            notification_channel="email",
            notification_target="ping@example.com",
        )
    assert result["task_type"] == "notification"
    assert result["cron_expression"] == "0 9 * * *"
    assert result["next_scheduled_time"].endswith("T03:30:00Z")
    assert result["enabled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scheduled_time, stored",
    [
        ("2999-01-01T09:00:00Z", "2999-01-01T09:00:00Z"),
        ("2999-01-01T09:00:00+01:00", "2999-01-01T08:00:00Z"),
        ("2999-01-01T09:00:00", "2999-01-01T03:30:00Z"),
    ],
)
async def test_create_notification_one_time(
    db_session, regular_user, user_session, scheduled_time, stored
):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()
    with mcp_auth_context(user_session.id):
        result = await scheduler.upsert.fn(
            task_type="notification",
            topic="Once",
            message="hi",
            scheduled_time=scheduled_time,
            notification_channel="email",
            notification_target="a@b.c",
        )
    assert result["cron_expression"] is None
    assert result["next_scheduled_time"] == stored


@pytest.mark.asyncio
async def test_create_rejects_bad_target_without_persisting(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="No email address found"):
            await scheduler.upsert.fn(
                task_type="notification",
                message="hi",
                cron_expression="0 9 * * *",
                notification_channel="email",
                notification_target="not-an-email",
            )
    assert (
        db_session.query(ScheduledTask).filter_by(user_id=regular_user.id).count() == 0
    )


@pytest.mark.asyncio
async def test_update_channel_change_revalidates_target(
    db_session, regular_user, user_session
):
    """Changing channel without a new target must re-validate the existing one."""
    with mcp_auth_context(user_session.id):
        created = await scheduler.upsert.fn(
            task_type="notification",
            message="hi",
            cron_expression="0 9 * * *",
            notification_channel="email",
            notification_target="a@example.com",
        )
        # An email address is not a valid Slack target — switching channel alone
        # must error rather than silently keep the now-invalid target.
        with pytest.raises(ValueError, match="not a known person, channel, or user id"):
            await scheduler.upsert.fn(
                task_id=created["id"], notification_channel="slack"
            )
    # The stored target is unchanged (transaction rolled back).
    task = db_session.get(ScheduledTask, created["id"])
    assert task.notification_channel == "email"
    assert task.notification_target == "a@example.com"


@pytest.mark.asyncio
async def test_create_claude_session(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.id, scopes=["claude_sessions"]):
        result = await scheduler.upsert.fn(
            task_type="claude_session",
            message="Run the digest",
            cron_expression="0 9 * * *",
            spawn_config={"repo_url": "https://github.com/x/y"},
        )
    assert result["task_type"] == "claude_session"
    assert result["message"] == "Run the digest"
    assert result["data"]["spawn_config"] == {"repo_url": "https://github.com/x/y"}
    assert result["topic"] == "Run the digest"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,match",
    [
        (
            dict(task_type="bogus", message="x", cron_expression="0 9 * * *"),
            "task_type",
        ),
        (
            dict(
                task_type="notification",
                message="x",
                notification_channel="discord",
                notification_target="1",
                cron_expression="0 9 * * *",
                scheduled_time="2999-01-01T09:00:00Z",
            ),
            "exactly one",
        ),
        (
            dict(
                task_type="notification",
                message="x",
                notification_channel="discord",
                notification_target="1",
            ),
            "exactly one",
        ),
        (
            dict(
                task_type="notification",
                message="x",
                notification_channel="discord",
                notification_target="1",
                cron_expression="*/1 * * * *",
            ),
            "too short",
        ),
        (dict(task_type="notification", cron_expression="0 9 * * *"), "require"),
        (
            dict(task_type="agent_run", cron_expression="0 9 * * *", message="p"),
            "agent_schedule",
        ),
        (
            dict(task_type="claude_session", cron_expression="0 9 * * *", message="p"),
            "spawn_config",
        ),
        (
            dict(
                task_type="claude_session",
                cron_expression="0 9 * * *",
                spawn_config={"repo_url": "x"},
            ),
            "initial prompt",
        ),
        (
            dict(
                task_type="claude_session",
                cron_expression="0 9 * * *",
                message="p",
                spawn_config={"bogus_key": "x"},
            ),
            "Unknown spawn_config",
        ),
        (
            dict(
                task_type="claude_session",
                cron_expression="0 9 * * *",
                message="p",
                spawn_config={"github_token": "ghp_x"},
            ),
            "saved GitHub tokens",
        ),
        (
            dict(
                task_type="claude_session",
                cron_expression="0 9 * * *",
                message="p",
                spawn_config={"github_token_write": None},
            ),
            "saved GitHub tokens",
        ),
    ],
)
async def test_create_validation_errors(
    db_session, regular_user, user_session, kwargs, match
):
    with mcp_auth_context(user_session.id, scopes=["claude_sessions"]):
        with pytest.raises(ValueError, match=match):
            await scheduler.upsert.fn(**kwargs)


def agent_run_task(db_session, user, enabled=True):
    task = ScheduledTask(
        user=user,
        task_type="agent_run",
        cron_expression="0 9 * * *",
        data={"definition_id": 1, "input": "go"},
    )
    task.enabled = enabled
    db_session.add(task)
    db_session.commit()
    return task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"message": "other"}, {"enabled": True}, {"cron_expression": "0 8 * * *"}],
)
async def test_agent_schedules_can_only_be_paused_here(
    db_session, regular_user, user_session, kwargs
):
    task = agent_run_task(db_session, regular_user, enabled=False)

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="agent_schedule"):
            await scheduler.upsert.fn(task_id=task.id, **kwargs)


@pytest.mark.asyncio
async def test_agent_schedules_can_be_paused_here(
    db_session, regular_user, user_session
):
    task = agent_run_task(db_session, regular_user)

    with mcp_auth_context(user_session.id):
        paused = await scheduler.upsert.fn(task_id=task.id, enabled=False)

    assert paused["enabled"] is False


@pytest.mark.asyncio
async def test_create_enforces_per_user_cap(
    db_session, regular_user, user_session, monkeypatch
):
    monkeypatch.setattr(settings_mod, "MAX_SCHEDULED_TASKS_PER_USER", 1)
    with mcp_auth_context(user_session.id):
        await scheduler.upsert.fn(
            task_type="notification",
            message="a",
            cron_expression="0 9 * * *",
            notification_channel="email",
            notification_target="a@example.com",
        )
        with pytest.raises(ValueError, match="Maximum"):
            await scheduler.upsert.fn(
                task_type="notification",
                message="b",
                cron_expression="0 10 * * *",
                notification_channel="email",
                notification_target="b@example.com",
            )


@pytest.mark.asyncio
async def test_upsert_reenable_respects_cap(
    db_session, regular_user, user_session, monkeypatch
):
    """create-disabled-then-enable must not bypass the active-task cap."""
    monkeypatch.setattr(settings_mod, "MAX_SCHEDULED_TASKS_PER_USER", 1)
    with mcp_auth_context(user_session.id):
        # One active recurring task — fills the cap.
        await scheduler.upsert.fn(
            task_type="notification",
            message="a",
            cron_expression="0 9 * * *",
            notification_channel="email",
            notification_target="a@example.com",
        )
        # A second created disabled (allowed — inactive, doesn't count).
        paused = await scheduler.upsert.fn(
            task_type="notification",
            message="b",
            cron_expression="0 10 * * *",
            notification_channel="email",
            notification_target="b@example.com",
            enabled=False,
        )
        assert paused["enabled"] is False
        # Re-enabling it would exceed the cap — must be rejected.
        with pytest.raises(ValueError, match="Maximum"):
            await scheduler.upsert.fn(task_id=paused["id"], enabled=True)
        # Re-activating via the documented cron-reschedule route is also capped.
        with pytest.raises(ValueError, match="Maximum"):
            await scheduler.upsert.fn(
                task_id=paused["id"], cron_expression="0 11 * * *"
            )


@pytest.mark.asyncio
async def test_upsert_create_rejects_unknown_channel(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="Unknown notification_channel"):
            await scheduler.upsert.fn(
                task_type="notification",
                message="x",
                cron_expression="0 9 * * *",
                notification_channel="carrier_pigeon",
                notification_target="1",
            )


@pytest.mark.asyncio
async def test_upsert_create_one_time_disabled_rejected(
    db_session, regular_user, user_session
):
    """A one-time task can't be created disabled — the hybrid setter would clear
    its next_scheduled_time, silently discarding the requested time."""
    with mcp_auth_context(user_session.id):
        with pytest.raises(
            ValueError, match="one-time task cannot be created disabled"
        ):
            await scheduler.upsert.fn(
                task_type="notification",
                message="x",
                scheduled_time="2999-01-01T09:00:00Z",
                enabled=False,
                notification_channel="discord",
                notification_target="1",
            )


@pytest.mark.asyncio
async def test_upsert_update_rejects_scheduled_time(
    db_session, regular_user, user_session
):
    """scheduled_time is create-only; passing it on update must error, not no-op."""
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
        with pytest.raises(
            ValueError, match="scheduled_time can only be set when creating"
        ):
            await scheduler.upsert.fn(
                task_id=task.id, scheduled_time="2999-01-01T09:00:00Z"
            )


@pytest.mark.asyncio
async def test_upsert_create_then_update_in_place(
    db_session, regular_user, user_session
):
    """A second upsert with the same (existing) id updates, not duplicates."""
    fixed_id = str(uuid.uuid4())
    with mcp_auth_context(user_session.id):
        created = await scheduler.upsert.fn(
            task_id=fixed_id,
            task_type="notification",
            message="x",
            cron_expression="0 9 * * *",
            notification_channel="email",
            notification_target="x@example.com",
        )
        assert created["topic"] is None
        updated = await scheduler.upsert.fn(task_id=fixed_id, topic="renamed")
    assert updated["id"] == fixed_id
    assert updated["topic"] == "renamed"
    assert updated["message"] == "x"


SESSION_TASK = dict(
    task_type="claude_session",
    message="Run the digest",
    cron_expression="0 9 * * *",
    spawn_config={"environment_id": 1},
)


@pytest.mark.asyncio
async def test_scheduling_a_session_needs_the_claude_sessions_scope(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id, scopes=["schedule:write", "read", "write"]):
        with pytest.raises(ValueError, match="claude_sessions"):
            await scheduler.upsert.fn(**SESSION_TASK)

    assert db_session.query(ScheduledTask).count() == 0


@pytest.mark.asyncio
async def test_sessions_cannot_be_scheduled_without_claude_containers(
    db_session, regular_user, user_session, set_modules
):
    set_modules([])
    with mcp_auth_context(user_session.id, scopes=["claude_sessions"]):
        with pytest.raises(ValueError, match="claude_containers"):
            await scheduler.upsert.fn(**SESSION_TASK)

    assert db_session.query(ScheduledTask).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("via_key", [True, False])
async def test_scheduled_session_records_the_key_it_will_act_through(
    db_session, regular_user, user_session, via_key
):
    key = APIKey.create(user_id=regular_user.id, scopes=["schedule:write", "claude_sessions"])
    db_session.add(key)
    db_session.commit()
    token = key.key if via_key else user_session.id

    with mcp_auth_context(token, scopes=["schedule:write", "claude_sessions"]):
        result = await scheduler.upsert.fn(**SESSION_TASK)

    assert result["data"]["api_key_id"] == (key.id if via_key else None)


@pytest.mark.asyncio
async def test_changing_a_session_schedule_restamps_its_key(
    db_session, regular_user, user_session
):
    key = APIKey.create(user_id=regular_user.id, scopes=["claude_sessions"])
    db_session.add(key)
    db_session.commit()
    with mcp_auth_context(key.key, scopes=["claude_sessions"]):
        created = await scheduler.upsert.fn(**SESSION_TASK)

    with mcp_auth_context(user_session.id, scopes=["claude_sessions"]):
        updated = await scheduler.upsert.fn(task_id=created["id"], message="New prompt")

    assert updated["data"]["api_key_id"] is None


@pytest.mark.asyncio
async def test_pausing_a_session_schedule_needs_no_claude_scope(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id, scopes=["claude_sessions"]):
        created = await scheduler.upsert.fn(**SESSION_TASK)

    with mcp_auth_context(user_session.id, scopes=["schedule:write"]):
        paused = await scheduler.upsert.fn(task_id=created["id"], enabled=False)

    assert paused["enabled"] is False


@pytest.mark.asyncio
async def test_changing_a_session_schedule_needs_the_scope(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id, scopes=["claude_sessions"]):
        created = await scheduler.upsert.fn(**SESSION_TASK)

    with mcp_auth_context(user_session.id, scopes=["schedule:write"]):
        with pytest.raises(ValueError, match="claude_sessions"):
            await scheduler.upsert.fn(task_id=created["id"], message="New prompt")
