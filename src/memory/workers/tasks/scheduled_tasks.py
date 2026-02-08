# src/memory/workers/tasks/scheduled_tasks.py
"""
Celery tasks for executing scheduled tasks.

Supports multiple task types: notification (Discord/Slack/Email), claude_session.
"""

import asyncio
import concurrent.futures
import copy
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from memory.common import discord as discord_utils
from memory.common.celery_app import (
    EXECUTE_SCHEDULED_TASK,
    RUN_SCHEDULED_TASKS,
    app,
)
from memory.common.content_processing import safe_task_execution
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordBot, EmailAccount, ScheduledTask, TaskExecution, UserSession
from memory.common.db.models.scheduled_tasks import (
    ExecutionStatus,
    TaskType,
    compute_next_cron,
)
from memory.common.db.models.slack import SlackUserCredentials
from memory.common.email_sender import get_account_by_address, prepare_send_config, send_email
from memory.common.slack import async_slack_call

logger = logging.getLogger(__name__)

STALE_EXECUTION_TIMEOUT_HOURS = 2
DISCORD_MESSAGE_LIMIT = 2000
SLACK_MESSAGE_LIMIT = 4000  # Slack chat.postMessage text limit
EMAIL_BODY_LIMIT = 100000  # Reasonable limit for email body size


@dataclass
class NotificationParams:
    """Parameters for sending notifications."""
    notification_channel: str
    notification_target: str
    message: str
    user_id: int
    topic: str | None
    data: dict[str, Any]


def extract_notification_params(task: ScheduledTask) -> NotificationParams | None:
    """Extract notification parameters from a ScheduledTask."""
    if not task.notification_channel or not task.notification_target:
        return None

    return NotificationParams(
        notification_channel=task.notification_channel,
        notification_target=task.notification_target,
        message=task.message or "",
        user_id=task.user_id,
        topic=task.topic,
        data=task.data or {},
    )


def send_via_discord(params: NotificationParams) -> bool:
    """Send message via Discord DM."""
    discord_user_id = params.notification_target
    if not discord_user_id:
        logger.error("No Discord user ID for notification")
        return False

    bot_id = params.data.get("discord_bot_id")

    if not bot_id:
        with make_session() as session:
            bot = (
                session.query(DiscordBot)
                .filter(DiscordBot.user_id == params.user_id)
                .first()
            )
            if bot:
                bot_id = bot.id
            else:
                logger.error(f"No Discord bot found for user {params.user_id}")
                return False

    message = params.message
    if len(message) > DISCORD_MESSAGE_LIMIT:
        message = message[:DISCORD_MESSAGE_LIMIT - 3] + "..."
        logger.warning(f"Discord message truncated from {len(params.message)} to {DISCORD_MESSAGE_LIMIT} chars")

    success = discord_utils.send_dm(bot_id, discord_user_id, message)
    if success:
        logger.info(f"Discord DM sent to user {discord_user_id}")
    else:
        logger.error(f"Failed to send Discord DM to user {discord_user_id}")
    return success


async def send_via_slack_async(params: NotificationParams) -> bool:
    """Async implementation of Slack DM sending."""
    slack_user_id = params.notification_target
    message = params.message

    # Truncate message if it exceeds Slack's limit
    if len(message) > SLACK_MESSAGE_LIMIT:
        message = message[:SLACK_MESSAGE_LIMIT - 3] + "..."
        logger.warning(f"Slack message truncated from {len(params.message)} to {SLACK_MESSAGE_LIMIT} chars")

    if not slack_user_id:
        logger.error("No Slack user ID for notification")
        return False

    with make_session() as session:
        credentials = (
            session.query(SlackUserCredentials)
            .filter(SlackUserCredentials.user_id == params.user_id)
            .first()
        )
        if not credentials or not credentials.access_token:
            logger.error(f"No Slack credentials for user {params.user_id}")
            return False
        slack_token = credentials.access_token

    try:
        data = await async_slack_call(slack_token, "conversations.open", users=slack_user_id)
        channel = data.get("channel", {})
        channel_id = channel.get("id")
        if not channel_id:
            raise ValueError(f"Failed to open DM with Slack user {slack_user_id}")

        await async_slack_call(
            slack_token,
            "chat.postMessage",
            channel=channel_id,
            text=message,
        )

        logger.info(f"Slack DM sent to user {slack_user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Slack DM: {e}")
        return False


def send_via_slack(params: NotificationParams) -> bool:
    """
    Send message via Slack DM.

    This is a sync wrapper around the async implementation. It handles the
    event loop boundary by always creating a new event loop via asyncio.run().
    If called from within an existing event loop (e.g., in tests), it runs
    the async code in a separate thread.
    """
    try:
        # Try to run directly - this works when no event loop is running
        return asyncio.run(send_via_slack_async(params))
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            # We're inside an existing event loop - run in a thread
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, send_via_slack_async(params))
                return future.result(timeout=30)
        raise


def send_via_email(params: NotificationParams) -> bool:
    """Send message via email."""
    to_address = params.notification_target
    if not to_address:
        logger.error("No email address for notification")
        return False

    message = params.message
    # Truncate message if it exceeds reasonable email body size
    if len(message) > EMAIL_BODY_LIMIT:
        message = message[:EMAIL_BODY_LIMIT - 100] + "\n\n[Message truncated due to size limit]"
        logger.warning(f"Email body truncated from {len(params.message)} to {EMAIL_BODY_LIMIT} chars")

    from_address = params.data.get("from_address")

    with make_session() as session:
        if from_address:
            account = get_account_by_address(session, params.user_id, from_address)
        else:
            account = (
                session.query(EmailAccount)
                .filter(
                    EmailAccount.user_id == params.user_id,
                    EmailAccount.active.is_(True),
                )
                .first()
            )

        if not account:
            logger.error(f"No email account for user {params.user_id}")
            return False

        config = prepare_send_config(session, account)

    subject = params.data.get("subject", params.topic or "Notification")

    result = send_email(
        config=config,
        to=[to_address],
        subject=subject,
        body=message,
    )

    if result.success:
        logger.info(f"Email sent to {to_address}")
        return True
    logger.error(f"Failed to send email: {result.error}")
    return False


def send_notification(params: NotificationParams) -> bool:
    """Send notification via the appropriate channel."""
    channel = params.notification_channel

    if channel == "discord":
        return send_via_discord(params)
    elif channel == "slack":
        return send_via_slack(params)
    elif channel == "email":
        return send_via_email(params)

    logger.error(f"Unknown notification_channel: {channel}")
    return False


def sanitize_slug(text: str, max_length: int = 30) -> str:
    """Sanitize text for use in git branch names and run IDs.

    Keeps only alphanumeric characters and hyphens, collapses runs of hyphens,
    and strips leading/trailing hyphens.
    """
    slug = re.sub(r"[^a-z0-9-]", "-", text.lower())
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_length].strip("-")


@contextmanager
def session_token(db, user_id: int):
    """Create a short-lived session token that is cleaned up on exit.

    Usage::

        with session_token(db_session, user_id) as token:
            requests.post(url, headers={"Authorization": f"Bearer {token}"})

    The token row is deleted in the ``finally`` block regardless of success or
    failure, so there are no orphaned session rows.
    """
    user_session = UserSession(
        user_id=user_id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
    )
    db.add(user_session)
    db.commit()
    token = user_session.id
    try:
        yield token
    finally:
        db.query(UserSession).filter(UserSession.id == token).delete()
        db.commit()


def spawn_claude_session(task: ScheduledTask, db) -> str:
    """Spawn a Claude Code session by calling the API's /claude/spawn endpoint.

    The worker cannot talk to the orchestrator socket directly (only the API
    container has it), so we create a short-lived session token and POST to the
    internal API URL.

    Args:
        task: The ScheduledTask containing spawn_config in its data.
        db: SQLAlchemy session for creating/cleaning up the auth token.

    Returns the session_id of the spawned container.
    """
    data = task.data or {}
    spawn_config = copy.deepcopy(data.get("spawn_config"))
    if not spawn_config:
        raise ValueError("Missing spawn_config in task data")

    # Auto-suffix run_id with execution timestamp to avoid branch conflicts
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if spawn_config.get("run_id"):
        spawn_config["run_id"] = f"{spawn_config['run_id']}-{now_str}"
    else:
        raw = task.topic or f"task-{str(task.id)[:8]}"
        slug = sanitize_slug(raw)
        spawn_config["run_id"] = f"{slug}-{now_str}"

    # NOTE: INTERNAL_API_URL should only point to container-internal or
    # localhost addresses. The session token is sent over HTTP (not HTTPS).
    # Pointing this at an external hostname would leak the token in transit.
    url = f"{settings.INTERNAL_API_URL}/claude/spawn"

    with session_token(db, task.user_id) as token:
        resp = requests.post(
            url,
            json=spawn_config,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if not resp.ok:
            raise ValueError(f"API returned {resp.status_code}: {resp.text}")
        session_id = resp.json().get("session_id")

    logger.info(f"Spawned Claude session {session_id} for task {task.id}")
    return session_id


@app.task(bind=True, name=EXECUTE_SCHEDULED_TASK)
@safe_task_execution
def execute_scheduled_task(self, execution_id: str):
    """Execute a scheduled task."""
    logger.info(f"Executing scheduled task execution: {execution_id}")

    with make_session() as session:
        execution = session.get(TaskExecution, execution_id)

        if not execution:
            logger.error(f"TaskExecution {execution_id} not found")
            return {"error": "Execution not found"}

        if execution.status != ExecutionStatus.PENDING:
            logger.warning(f"TaskExecution {execution_id} is not pending (status: {execution.status})")
            return {"error": f"Execution is not pending (status: {execution.status})"}

        task = execution.task
        if not task:
            logger.error(f"ScheduledTask for execution {execution_id} not found")
            execution.status = ExecutionStatus.FAILED
            execution.error_message = "Task not found"
            execution.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
            return {"error": "Task not found"}

        task_type = task.task_type
        task_id = task.id

        # Mark as running and commit - use try/finally to ensure we always
        # update the final status even if something goes wrong after this point
        execution.status = ExecutionStatus.RUNNING
        execution.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()

        final_status = ExecutionStatus.FAILED
        error_message = None
        execution_data = None

        try:
            if task_type == TaskType.NOTIFICATION:
                params = extract_notification_params(task)
                if not params:
                    raise ValueError("Missing notification_channel or notification_target")

                sent = send_notification(params)
                if sent:
                    final_status = ExecutionStatus.COMPLETED
                else:
                    error_message = "Failed to send notification"

            elif task_type == TaskType.CLAUDE_SESSION:
                session_id = spawn_claude_session(task, session)
                final_status = ExecutionStatus.COMPLETED
                execution_data = {"session_id": session_id}

            else:
                raise ValueError(f"Unknown task_type: {task_type}")

        except Exception as e:
            logger.exception(f"Failed to execute task {task_id}: {e}")
            error_message = str(e)
        finally:
            # Always update the execution status in a finally block
            # to ensure we don't leave executions stuck in "running" state
            execution.status = final_status
            execution.error_message = error_message
            if execution_data:
                execution.data = execution_data
            execution.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()

        return {
            "success": final_status == ExecutionStatus.COMPLETED,
            "execution_id": execution_id,
            "task_id": task_id,
            "task_type": task_type,
        }


@app.task(name=RUN_SCHEDULED_TASKS)
@safe_task_execution
def run_scheduled_tasks():
    """Find and dispatch due scheduled tasks."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with make_session() as session:
        # 1. Recover stale executions (stuck in "running" for too long)
        stale_cutoff = now - timedelta(hours=STALE_EXECUTION_TIMEOUT_HOURS)
        stale_executions = (
            session.query(TaskExecution)
            .filter(
                TaskExecution.status == ExecutionStatus.RUNNING,
                TaskExecution.started_at < stale_cutoff,
                TaskExecution.finished_at.is_(None),
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        for stale in stale_executions:
            logger.warning(f"Recovering stale execution {stale.id} (stuck since {stale.started_at})")
            stale.status = ExecutionStatus.FAILED
            stale.error_message = "Recovered from stale execution state"
            stale.finished_at = now

        # 1b. Recover stuck pending executions (pending for too long without being picked up)
        # This handles the case where tasks were dispatched after commit but Celery never received them
        pending_cutoff = now - timedelta(minutes=30)  # Pending for more than 30 minutes is suspicious
        stuck_pending = (
            session.query(TaskExecution)
            .filter(
                TaskExecution.status == ExecutionStatus.PENDING,
                TaskExecution.scheduled_time < pending_cutoff,
                TaskExecution.started_at.is_(None),
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        recovered_pending_count = 0
        for stuck in stuck_pending:
            logger.warning(f"Re-dispatching stuck pending execution {stuck.id} (scheduled for {stuck.scheduled_time})")
            recovered_pending_count += 1

        if stale_executions or stuck_pending:
            session.commit()
            if stale_executions:
                logger.info(f"Recovered {len(stale_executions)} stale executions")
            if stuck_pending:
                logger.info(f"Found {recovered_pending_count} stuck pending executions to re-dispatch")

        # 2. Find due tasks without pending or running executions
        active_executions_subq = (
            session.query(TaskExecution.task_id)
            .filter(TaskExecution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]))
            .subquery()
        )

        due_tasks = (
            session.query(ScheduledTask)
            .filter(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.next_scheduled_time < now,
                ~ScheduledTask.id.in_(session.query(active_executions_subq.c.task_id)),
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        # 3. Create executions and dispatch
        execution_ids = []
        for task in due_tasks:
            execution = TaskExecution(
                task_id=task.id,
                scheduled_time=task.next_scheduled_time,
                status=ExecutionStatus.PENDING,
            )
            session.add(execution)

            if task.cron_expression:
                task.next_scheduled_time = compute_next_cron(task.cron_expression, now)
            else:
                task.next_scheduled_time = None

            session.flush()
            execution_ids.append(execution.id)

        session.commit()

        # Dispatch new executions
        for execution_id in execution_ids:
            execute_scheduled_task.delay(execution_id)

        # Re-dispatch stuck pending executions
        for stuck in stuck_pending:
            execute_scheduled_task.delay(stuck.id)

        return {
            "executions": execution_ids,
            "count": len(execution_ids),
            "recovered_stale": len(stale_executions),
            "recovered_pending": recovered_pending_count,
        }
