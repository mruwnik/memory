"""Tests for the scheduler MCP server (hybrid enabled property)."""
# pyright: reportFunctionMemberAccess=false

import uuid
from datetime import datetime, timezone

import pytest

from memory.api.MCP.servers import scheduler
from memory.common import settings as settings_mod
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
    with pytest.raises(ValueError, match="github_token"):
        scheduler.validate_scheduled_secret_refs(
            db_session, regular_user.id, {"github_token": "no-such-secret"}
        )


def test_validate_scheduled_secret_refs_allows_no_tokens(db_session, regular_user):
    scheduler.validate_scheduled_secret_refs(
        db_session, regular_user.id, {"repo_url": "x"}
    )


@pytest.mark.asyncio
async def test_create_notification_recurring(db_session, regular_user, user_session):
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
    assert result["next_scheduled_time"] is not None
    assert result["enabled"] is True


@pytest.mark.asyncio
async def test_create_notification_one_time(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.id):
        result = await scheduler.upsert.fn(
            task_type="notification",
            topic="Once",
            message="hi",
            scheduled_time="2999-01-01T09:00:00Z",
            notification_channel="email",
            notification_target="a@b.c",
        )
    assert result["cron_expression"] is None
    assert result["next_scheduled_time"] == "2999-01-01T09:00:00Z"


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
    with mcp_auth_context(user_session.id):
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
                spawn_config={"github_token": "no-such"},
            ),
            "Secret reference",
        ),
    ],
)
async def test_create_validation_errors(
    db_session, regular_user, user_session, kwargs, match
):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=match):
            await scheduler.upsert.fn(**kwargs)


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
