"""MCP subserver for scheduled task management."""

import logging
from datetime import datetime, timezone
from typing import Any

from croniter import croniter
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from sqlalchemy import nullslast
from sqlalchemy.exc import IntegrityError

from memory.api.auth import lookup_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.servers.notification_targets import resolve_and_validate_target
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.claude_spawn import check_dev_channels_server
from memory.common.dates import parse_wall_clock_utc
from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledTask, TaskExecution, User
from memory.common.db.models.scheduled_tasks import TaskType, compute_next_cron
from memory.common.module_settings import enabled_modules
from memory.common.modules import CLAUDE_CONTAINERS
from memory.common.scopes import (
    SCOPE_CLAUDE_SESSIONS,
    SCOPE_SCHEDULE,
    SCOPE_SCHEDULE_WRITE,
    has_scope,
)
from memory.common.ssh_keys import container_key

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
        task_type: Filter by task type (notification, claude_session, agent_run)
        enabled: Filter by enabled status
        limit: Maximum number of tasks to return (default 50)
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        query = session.query(ScheduledTask).filter(ScheduledTask.user_id == user_id)

        if task_type:
            query = query.filter(ScheduledTask.task_type == task_type)
        if enabled is not None:
            query = query.filter(
                ScheduledTask.enabled if enabled else ~ScheduledTask.enabled
            )

        tasks = (
            query.order_by(nullslast(ScheduledTask.next_scheduled_time))
            .limit(limit)
            .all()
        )
        return [task.serialize() for task in tasks]


TASK_TYPES = {t.value for t in TaskType}

SPAWN_CONFIG_FIELDS = {
    "allowed_tools",
    "repo_url",
    "custom_env",
    "enable_playwright",
    "run_id",
    "environment_id",
    "snapshot_id",
    "use_saved_write_token",
    "ssh_key_id",
    "use_ssh_key",
    "dev_channels_server",
}


def validate_cron_interval(cron_expression: str) -> None:
    """Validate a standard 5-field cron and enforce MIN_CRON_INTERVAL_MINUTES.

    Raises ValueError on an invalid, non-5-field, or too-frequent expression.
    """
    if not croniter.is_valid(cron_expression):
        raise ValueError("Invalid cron expression")
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Only 5-field cron expressions supported, got {len(parts)}")
    cron = croniter(cron_expression)
    first = cron.get_next(float)
    second = cron.get_next(float)
    interval_minutes = (second - first) / 60
    if interval_minutes < settings.MIN_CRON_INTERVAL_MINUTES:
        raise ValueError(
            f"Cron interval too short ({interval_minutes:.0f}m). "
            f"Minimum is {settings.MIN_CRON_INTERVAL_MINUTES} minutes."
        )


def validate_spawn_config_keys(spawn_config: dict[str, Any]) -> None:
    """Reject unknown keys, and per-schedule GitHub tokens: those would sit in
    plaintext in ScheduledTask.data, so scheduled sessions use the owner's saved
    tokens instead."""
    tokens = sorted({"github_token", "github_token_write"} & set(spawn_config))
    if tokens:
        raise ValueError(
            f"Scheduled sessions don't take {', '.join(tokens)}: they use your saved "
            "GitHub tokens (set them in the Claude sessions form; set "
            "use_saved_write_token to give the session the write token)."
        )
    unknown = set(spawn_config) - SPAWN_CONFIG_FIELDS
    if unknown:
        raise ValueError(f"Unknown spawn_config keys: {', '.join(sorted(unknown))}")


def validate_spawn_choices(session, user_id: int, spawn_config: dict[str, Any]) -> None:
    """Fail at scheduling time rather than on every fire; /claude/spawn
    re-checks both as the owner when the schedule fires."""
    key_id = spawn_config.get("ssh_key_id")
    if key_id is not None:
        if type(key_id) is not int:
            raise ValueError("ssh_key_id must be an integer")
        container_key(session, session.get(User, user_id), key_id)
    dev_channels_server = spawn_config.get("dev_channels_server")
    if dev_channels_server is not None and not isinstance(dev_channels_server, str):
        raise ValueError("dev_channels_server must be a string")
    check_dev_channels_server(dev_channels_server)


def session_principal(session) -> int | None:
    """The API key a scheduled Claude session will act through (None: the
    caller's session). Each fire re-checks it still holds claude_sessions."""
    token = get_access_token()
    if token is None or not has_scope(list(token.scopes or []), SCOPE_CLAUDE_SESSIONS):
        raise ValueError(f"scheduling Claude sessions needs the '{SCOPE_CLAUDE_SESSIONS}' scope")
    key = lookup_api_key(token.token, session)
    return key.id if key else None


def check_active_task_cap(session, user_id: int, exclude_id: str | None = None) -> None:
    query = session.query(ScheduledTask).filter(
        ScheduledTask.user_id == user_id, ScheduledTask.enabled
    )
    if exclude_id is not None:
        query = query.filter(ScheduledTask.id != exclude_id)
    if query.count() >= settings.MAX_SCHEDULED_TASKS_PER_USER:
        raise ValueError(
            f"Maximum of {settings.MAX_SCHEDULED_TASKS_PER_USER} active "
            "scheduled tasks per user reached"
        )


NOTIFICATION_CHANNELS = {"discord", "slack", "email"}


def validate_notification_channel(notification_channel: str | None) -> None:
    """Reject unknown notification channels; None means 'unchanged'."""
    if notification_channel is None:
        return
    if notification_channel not in NOTIFICATION_CHANNELS:
        raise ValueError(
            f"Unknown notification_channel '{notification_channel}'. "
            f"Must be one of: {', '.join(sorted(NOTIFICATION_CHANNELS))}"
        )


def build_scheduled_task(
    session,
    user_id: int,
    *,
    task_id: str | None,
    task_type: str | None,
    enabled: bool | None,
    cron_expression: str | None,
    scheduled_time: str | None,
    topic: str | None,
    message: str | None,
    notification_channel: str | None,
    notification_target: str | None,
    spawn_config: dict[str, Any] | None,
    agent_run: dict[str, Any] | None = None,
) -> ScheduledTask:
    """Build (add but do not commit) a new ScheduledTask for upsert's create path.

    Supports both task types and both cadences (cron OR one-time). Validates
    the cadence, the per-user active-task cap, and the type-specific required
    fields. Honors an explicit task_id and a created-disabled (enabled=False)
    task. Raises ValueError on any validation failure.
    """
    if task_type not in TASK_TYPES:
        raise ValueError(
            "Creating a task requires task_type 'notification' or 'claude_session' "
            "(agent runs are scheduled with agent_schedule)"
        )
    if task_type == TaskType.CLAUDE_SESSION and CLAUDE_CONTAINERS not in enabled_modules():
        raise ValueError(
            "claude_session tasks need the claude_containers module "
            "(an admin turns it on in Server settings)"
        )
    if bool(cron_expression) == bool(scheduled_time):
        raise ValueError("Provide exactly one of cron_expression or scheduled_time")

    owner_tz = session.get_one(User, user_id).timezone
    if cron_expression:
        validate_cron_interval(cron_expression)
        next_time = compute_next_cron(cron_expression, owner_tz)
    else:
        parsed = parse_wall_clock_utc(scheduled_time, owner_tz)
        if parsed is None:
            raise ValueError("Invalid datetime format for scheduled_time")
        if parsed <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise ValueError("scheduled_time must be in the future")
        next_time = parsed

    if enabled is False and not cron_expression:
        raise ValueError(
            "A one-time task cannot be created disabled; enabled=False is only "
            "meaningful for recurring (cron) tasks."
        )

    # A created-disabled recurring task gets next_scheduled_time cleared below
    # and isn't "active", so it can't breach the cap; only enforce when active.
    if enabled is not False:
        check_active_task_cap(session, user_id)

    task = ScheduledTask(
        user_id=user_id,
        task_type=task_type,
        topic=topic,
        cron_expression=cron_expression,
        next_scheduled_time=next_time,
    )
    if task_id:
        task.id = task_id

    if task_type == "notification":
        if not (notification_channel and notification_target and message):
            raise ValueError(
                "notification tasks require notification_channel, "
                "notification_target, and message"
            )
        validate_notification_channel(notification_channel)
        task.message = message
        task.notification_channel = notification_channel
        task.notification_target = resolve_and_validate_target(
            session, user_id, notification_channel, notification_target
        )
        task.data = {"notification_type": "notify_user", "subject": topic}
    elif task_type == TaskType.AGENT_RUN:
        if not agent_run:
            raise ValueError("agent_run tasks require a definition and input")
        task.data = agent_run
    else:
        if not spawn_config:
            raise ValueError("claude_session tasks require spawn_config")
        if not message:
            raise ValueError(
                "claude_session tasks require message (the initial prompt)"
            )
        validate_spawn_config_keys(spawn_config)
        validate_spawn_choices(session, user_id, spawn_config)
        task.message = message
        task.topic = topic or message[:100]
        task.data = {"spawn_config": spawn_config}

    if enabled is False:
        # Recurring + disabled (one-time+disabled is rejected above): the hybrid
        # setter clears next_scheduled_time, so the task is created paused.
        task.enabled = False

    session.add(task)
    return task


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE_WRITE))
async def upsert(
    task_id: str | None = None,
    task_type: str | None = None,
    enabled: bool | None = None,
    cron_expression: str | None = None,
    scheduled_time: str | None = None,
    topic: str | None = None,
    message: str | None = None,
    notification_channel: str | None = None,
    notification_target: str | None = None,
    spawn_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create or update a scheduled task.

    Omit task_id (or pass one that doesn't exist yet) to CREATE a task; pass an
    existing task_id to UPDATE it in place.

    Creating requires task_type and exactly one of cron_expression (recurring)
    or scheduled_time (one-time ISO datetime — fires once, then the task is
    Done). notification tasks also require notification_channel,
    notification_target, and message; claude_session tasks require message (the
    initial prompt) and spawn_config.
    agent_run tasks are created and changed with agent_schedule; here they can
    only be paused (enabled=False).

    Args:
        task_id: ID of the task to update. Omit (or pass a fresh id) to create.
        task_type: "notification", or "claude_session" when the claude_containers
            module is on (create only; an existing task's type is immutable).
        enabled: Whether the task is enabled (disabling clears its next run time).
        cron_expression: Cron schedule expression (5-field), in the user's
            timezone (see meta_get_user).
        scheduled_time: ISO datetime for a one-time task (create only). Without
            an offset it is read in the user's timezone (see meta_get_user).
        topic: Topic/subject of the task.
        message: Notification body, or the Claude session's initial prompt.
        notification_channel: Notification channel (discord, slack, email).
        notification_target: Who/where to notify, validated and resolved to a
            concrete id/address before saving (a clear error is returned if it
            can't be). Accepts a person name/identifier/email (resolved to the
            person's Discord/Slack id or email), a Discord/Slack channel id, a
            raw Discord/Slack user id, or an email address. Delivery method
            (DM vs channel) is derived from the resolved id.
        spawn_config: Spawn config for claude_session tasks. Supported keys:
            allowed_tools, repo_url, custom_env, enable_playwright, run_id,
            environment_id, snapshot_id, use_saved_write_token. The session
            gets the owner's saved GitHub clone token, and their saved write
            token only with use_saved_write_token. On update, set a key to null
            to remove it. initial_prompt is stored in the message field, not
            spawn_config.
    """
    user_id = get_authenticated_user_id()

    with make_session() as session:
        # A missing id means "create", not "not found" — so this intentionally
        # diverges from get_owned_task's raise-on-missing.
        task = session.get(ScheduledTask, task_id) if task_id else None
        if task and task.user_id != user_id:
            raise ValueError("Not authorized to access this task")

        if task is None:
            if task_type == TaskType.AGENT_RUN:
                raise ValueError("create agent schedules with agent_schedule")
            task = build_scheduled_task(
                session,
                user_id,
                task_id=task_id,
                task_type=task_type,
                enabled=enabled,
                cron_expression=cron_expression,
                scheduled_time=scheduled_time,
                topic=topic,
                message=message,
                notification_channel=notification_channel,
                notification_target=notification_target,
                spawn_config=spawn_config,
            )
            if task.task_type == TaskType.CLAUDE_SESSION:
                task.data = {**(task.data or {}), "api_key_id": session_principal(session)}
            created_id = task.id
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                # Only relabel as "already exists" if a row with this id is
                # actually present; otherwise re-raise so a genuinely different
                # constraint violation isn't silently mislabeled.
                if created_id is not None and session.get(ScheduledTask, created_id):
                    raise ValueError("A task with this id already exists") from exc
                raise
            return task.serialize()

        if scheduled_time is not None:
            raise ValueError(
                "scheduled_time can only be set when creating a task; to change "
                "an existing task's schedule use cron_expression."
            )

        # Capture activeness before any mutation: an update that flips an
        # inactive task to active (via cron_expression OR enabled=True) must
        # respect the per-user cap, else create-disabled-then-reactivate would
        # bypass it. `enabled` is derived from next_scheduled_time, so we check
        # the transition after the mutations below rather than per-field.
        was_active = task.enabled

        # Anything but pausing changes what the schedule will run, so it needs
        # the right to run it (agent_schedule re-checks agents' scopes and tools).
        changes = (
            enabled,
            cron_expression,
            topic,
            message,
            notification_channel,
            notification_target,
            spawn_config,
        ) != (False, None, None, None, None, None, None)
        if task.task_type == TaskType.AGENT_RUN and changes:
            raise ValueError(
                "agent schedules can only be paused here; change them with agent_schedule"
            )

        if cron_expression is not None:
            validate_cron_interval(cron_expression)
            task.cron_expression = cron_expression
            task.next_scheduled_time = compute_next_cron(
                cron_expression, task.user.timezone
            )

        if enabled is not None:
            task.enabled = enabled
        if topic is not None:
            task.topic = topic
        if message is not None:
            task.message = message
        channel_changed = (
            notification_channel is not None
            and notification_channel != task.notification_channel
        )
        if notification_channel is not None:
            validate_notification_channel(notification_channel)
            task.notification_channel = notification_channel
        if notification_target is not None:
            if not task.notification_channel:
                raise ValueError("notification_target requires a notification_channel")
            task.notification_target = resolve_and_validate_target(
                session, user_id, task.notification_channel, notification_target
            )
        elif channel_changed and task.notification_target and task.notification_channel:
            # The stored target was validated for the OLD channel; a channel
            # change must re-resolve it (or surface a clear error) rather than
            # leave a target that silently breaks at dispatch.
            task.notification_target = resolve_and_validate_target(
                session, user_id, task.notification_channel, task.notification_target
            )

        if task.task_type == TaskType.CLAUDE_SESSION and changes:
            task.data = {**(task.data or {}), "api_key_id": session_principal(session)}

        if spawn_config is not None:
            if task.task_type != "claude_session":
                raise ValueError("spawn_config can only be set on claude_session tasks")
            validate_spawn_config_keys(spawn_config)
            validate_spawn_choices(session, user_id, spawn_config)

            data = dict(task.data or {})
            existing = dict(data.get("spawn_config") or {})
            for key, value in spawn_config.items():
                if value is None:
                    existing.pop(key, None)
                else:
                    existing[key] = value
            data["spawn_config"] = existing
            task.data = data

        if task.enabled and not was_active:
            # This update re-activated a previously-inactive task — enforce the
            # active-task cap (excluding this task itself).
            check_active_task_cap(session, user_id, exclude_id=task.id)

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
