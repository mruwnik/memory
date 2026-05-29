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
