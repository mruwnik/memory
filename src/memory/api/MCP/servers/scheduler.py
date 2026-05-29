"""MCP subserver for scheduled task management."""

import logging
from datetime import datetime, timezone
from typing import Any

from croniter import croniter
from fastmcp import FastMCP
from sqlalchemy import nullslast

from memory.api.MCP.access import get_mcp_current_user
from memory.common.dates import parse_iso_datetime
from memory.common.db.models.secrets import find_secret
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledTask, TaskExecution
from memory.common.db.models.scheduled_tasks import compute_next_cron
from memory.common.scopes import SCOPE_SCHEDULE, SCOPE_SCHEDULE_WRITE

logger = logging.getLogger(__name__)

scheduler_mcp = FastMCP("memory-scheduler")


def get_authenticated_user_id() -> int:
    """Get the authenticated user's ID or raise."""
    user = get_mcp_current_user()
    if not user:
        raise ValueError("Not authenticated")
    user_id = getattr(user, "id", None)
    if user_id is None:
        raise ValueError("User not found")
    return user_id


def get_owned_task(session, task_id: str, user_id: int) -> ScheduledTask:
    """Get a scheduled task, verifying ownership."""
    task = session.get(ScheduledTask, task_id)
    if not task:
        raise ValueError("Task not found")
    if task.user_id != user_id:
        raise ValueError("Not authorized to access this task")
    return task


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE))
async def list_all(
    task_type: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List scheduled tasks for the current user.

    Args:
        task_type: Filter by task type (notification, claude_session)
        enabled: Filter by enabled status
        limit: Maximum number of tasks to return (default 50)
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        query = session.query(ScheduledTask).filter(ScheduledTask.user_id == user_id)

        if task_type:
            query = query.filter(ScheduledTask.task_type == task_type)
        if enabled is not None:
            query = query.filter(ScheduledTask.enabled if enabled else ~ScheduledTask.enabled)

        tasks = query.order_by(nullslast(ScheduledTask.next_scheduled_time)).limit(limit).all()
        return [task.serialize() for task in tasks]


SPAWN_CONFIG_FIELDS = {
    "allowed_tools", "repo_url", "custom_env",
    "enable_playwright", "run_id", "environment_id", "snapshot_id",
    "github_token", "github_token_write",
}


def validate_cron_interval(cron_expression: str) -> None:
    """Validate a standard 5-field cron and enforce MIN_CRON_INTERVAL_MINUTES.

    Raises ValueError on an invalid, non-5-field, or too-frequent expression.
    """
    if not croniter.is_valid(cron_expression):
        raise ValueError("Invalid cron expression")
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Only 5-field cron expressions supported, got {len(parts)}"
        )
    cron = croniter(cron_expression)
    first = cron.get_next(float)
    second = cron.get_next(float)
    interval_minutes = (second - first) / 60
    if interval_minutes < settings.MIN_CRON_INTERVAL_MINUTES:
        raise ValueError(
            f"Cron interval too short ({interval_minutes:.0f}m). "
            f"Minimum is {settings.MIN_CRON_INTERVAL_MINUTES} minutes."
        )


def validate_scheduled_secret_refs(
    session, user_id: int, spawn_config: dict[str, Any]
) -> None:
    """Reject scheduled-session token fields that don't resolve to a stored
    Secret. Scheduled token values are persisted in ScheduledTask.data, so a
    literal (or typo'd) value would be stored in plaintext and shipped to the
    container later. The error must NOT echo the value (credential reflection).
    """
    for token_field in ("github_token", "github_token_write"):
        token_value = spawn_config.get(token_field)
        if token_value and find_secret(session, user_id, token_value) is None:
            raise ValueError(
                f"Secret reference for '{token_field}' not found. Scheduled "
                "sessions require a stored-secret name (create one via "
                "/secrets); literal tokens are not accepted because they would "
                "be persisted in plaintext in the schedule's data column."
            )


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE_WRITE))
async def upsert(
    task_id: str,
    enabled: bool | None = None,
    cron_expression: str | None = None,
    topic: str | None = None,
    message: str | None = None,
    notification_channel: str | None = None,
    notification_target: str | None = None,
    spawn_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Update a scheduled task's fields.

    Args:
        task_id: The ID of the task to update
        enabled: Whether the task is enabled
        cron_expression: Cron schedule expression (5-field)
        topic: Topic/subject of the task
        message: Message content
        notification_channel: Notification channel (discord, slack, email)
        notification_target: Target for notifications
        spawn_config: Partial spawn config to merge (claude_session tasks only).
            Supported keys: allowed_tools, repo_url, custom_env, enable_playwright,
            run_id, environment_id, snapshot_id, github_token,
            github_token_write. Set a key to null to remove it.
            Note: initial_prompt is stored in the message field, not spawn_config.
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        task = get_owned_task(session, task_id, user_id)

        if cron_expression is not None:
            validate_cron_interval(cron_expression)
            task.cron_expression = cron_expression
            task.next_scheduled_time = compute_next_cron(cron_expression)

        if enabled is not None:
            task.enabled = enabled
        if topic is not None:
            task.topic = topic
        if message is not None:
            task.message = message
        if notification_channel is not None:
            task.notification_channel = notification_channel
        if notification_target is not None:
            task.notification_target = notification_target

        if spawn_config is not None:
            if task.task_type != "claude_session":
                raise ValueError("spawn_config can only be set on claude_session tasks")
            unknown = set(spawn_config.keys()) - SPAWN_CONFIG_FIELDS
            if unknown:
                raise ValueError(f"Unknown spawn_config keys: {', '.join(sorted(unknown))}")

            data = dict(task.data or {})
            existing = dict(data.get("spawn_config") or {})
            for key, value in spawn_config.items():
                if value is None:
                    existing.pop(key, None)
                else:
                    existing[key] = value
            data["spawn_config"] = existing
            task.data = data

        session.commit()
        return task.serialize()


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE_WRITE))
async def create(
    task_type: str,
    topic: str | None = None,
    message: str | None = None,
    cron_expression: str | None = None,
    scheduled_time: str | None = None,
    notification_channel: str | None = None,
    notification_target: str | None = None,
    spawn_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a scheduled task.

    Args:
        task_type: "notification" or "claude_session".
        topic: Short label/subject.
        message: Notification body, or the Claude session's initial prompt.
        cron_expression: 5-field cron for a recurring task. Provide this OR
            scheduled_time, not both.
        scheduled_time: ISO datetime for a one-time task (fires once, then the
            task is Done).
        notification_channel: required for notifications (discord, slack, email).
        notification_target: required for notifications.
        spawn_config: claude_session only; keys limited to the spawn whitelist
            (initial_prompt goes in message, not here). Token fields must name
            a stored secret.
    """
    user_id = get_authenticated_user_id()

    if task_type not in ("notification", "claude_session"):
        raise ValueError("task_type must be 'notification' or 'claude_session'")
    if bool(cron_expression) == bool(scheduled_time):
        raise ValueError("Provide exactly one of cron_expression or scheduled_time")

    if cron_expression:
        validate_cron_interval(cron_expression)
        next_time = compute_next_cron(cron_expression)
    else:
        parsed = parse_iso_datetime(scheduled_time)
        if parsed is None:
            raise ValueError("Invalid datetime format for scheduled_time")
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        if parsed <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise ValueError("scheduled_time must be in the future")
        next_time = parsed

    with make_session() as session:
        active_count = (
            session.query(ScheduledTask)
            .filter(ScheduledTask.user_id == user_id, ScheduledTask.enabled)
            .count()
        )
        if active_count >= settings.MAX_SCHEDULED_TASKS_PER_USER:
            raise ValueError(
                f"Maximum of {settings.MAX_SCHEDULED_TASKS_PER_USER} active "
                "scheduled tasks per user reached"
            )

        task = ScheduledTask(
            user_id=user_id,
            task_type=task_type,
            topic=topic,
            cron_expression=cron_expression,
            next_scheduled_time=next_time,
        )

        if task_type == "notification":
            if not (notification_channel and notification_target and message):
                raise ValueError(
                    "notification tasks require notification_channel, "
                    "notification_target, and message"
                )
            task.message = message
            task.notification_channel = notification_channel
            task.notification_target = notification_target
            task.data = {"notification_type": "notify_user", "subject": topic}
        else:
            if not spawn_config:
                raise ValueError("claude_session tasks require spawn_config")
            if not message:
                raise ValueError(
                    "claude_session tasks require message (the initial prompt)"
                )
            unknown = set(spawn_config) - SPAWN_CONFIG_FIELDS
            if unknown:
                raise ValueError(
                    f"Unknown spawn_config keys: {', '.join(sorted(unknown))}"
                )
            validate_scheduled_secret_refs(session, user_id, spawn_config)
            task.message = message
            task.topic = topic or message[:100]
            task.data = {"spawn_config": spawn_config}

        session.add(task)
        session.commit()
        return task.serialize()


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE))
async def cancel(task_id: str) -> dict[str, Any]:
    """
    Cancel (disable) a scheduled task.

    Args:
        task_id: The ID of the task to cancel
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        task = get_owned_task(session, task_id, user_id)
        task.enabled = False
        session.commit()
        return {"success": True, "task": task.serialize()}


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE_WRITE))
async def delete(task_id: str) -> dict[str, Any]:
    """
    Permanently delete a scheduled task and its execution history.

    Args:
        task_id: The ID of the task to delete
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        task = get_owned_task(session, task_id, user_id)
        session.delete(task)
        session.commit()
        return {"deleted": True, "task_id": task_id}


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE))
async def executions(
    task_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Get execution history for a scheduled task.

    Args:
        task_id: The ID of the task
        limit: Maximum number of executions to return (default 10)
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        task = get_owned_task(session, task_id, user_id)

        results = (
            session.query(TaskExecution)
            .filter(TaskExecution.task_id == task.id)
            .order_by(TaskExecution.scheduled_time.desc())
            .limit(limit)
            .all()
        )

        return [e.serialize() for e in results]
