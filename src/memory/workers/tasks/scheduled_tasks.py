# src/memory/workers/tasks/scheduled_tasks.py
"""
Celery tasks for executing scheduled tasks.

Supports notification (Discord/Slack/Email) and agent_run (started on the agents
queue) tasks.
"""

import asyncio
import concurrent.futures
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.agents.principal import PrincipalRevoked
from memory.common import discord as discord_utils
from memory.common.access_control import UserProxy, credential_scopes
from memory.common.celery_app import (
    EXECUTE_SCHEDULED_TASK,
    RUN_SCHEDULED_TASKS,
    SEND_NOTIFICATION,
    START_CRON_RUN,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import (
    APIKey,
    DiscordBot,
    EmailAccount,
    ScheduledTask,
    TaskExecution,
    User,
)
from memory.common.db.models.scheduled_tasks import (
    ExecutionStatus,
    TaskType,
    compute_next_cron,
)
from memory.common.db.models.slack import SlackUserCredentials
from memory.common.discord_data import (
    caller_can_see_discord_channel,
    caller_can_see_discord_user,
    discord_channel_for_target,
    discord_target_is_channel,
)
from memory.common.email_sender import (
    PASSWORD_RESET_HEADER,
    get_account_by_address,
    prepare_send_config,
    send_email,
)
from memory.common.jobs import tracked_task
from memory.common.notify import notify_user_now
from memory.common.slack import async_slack_call, slack_api_base_url, slack_target_is_dm

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


def resolve_discord_bot_id(session, params: NotificationParams) -> int | None:
    bot_id = params.data.get("discord_bot_id")
    if bot_id:
        return bot_id
    bot = (
        session.query(DiscordBot)
        .filter(DiscordBot.created_by_user_id == params.user_id)
        .first()
    )
    if bot:
        return bot.id
    logger.error(f"No Discord bot found for user {params.user_id}")
    return None


def send_via_discord(params: NotificationParams) -> bool:
    """Send via Discord — channel post or user DM, derived from the target id."""
    target = params.notification_target
    if not target:
        logger.error("No Discord target for notification")
        return False
    # Snowflake ids can arrive as int across the celery boundary; the collector
    # API and classification both want a str.
    target = str(target)

    with make_session() as session:
        bot_id = resolve_discord_bot_id(session, params)
        if bot_id is None:
            return False
        is_channel = discord_target_is_channel(session, target)

    message = params.message
    if len(message) > DISCORD_MESSAGE_LIMIT:
        message = message[: DISCORD_MESSAGE_LIMIT - 3] + "..."
        logger.warning(
            f"Discord message truncated from {len(params.message)} to {DISCORD_MESSAGE_LIMIT} chars"
        )

    if is_channel:
        success = discord_utils.send_to_channel(bot_id, target, message)
        label = f"channel {target}"
    else:
        success = discord_utils.send_dm(bot_id, target, message)
        label = f"user {target}"

    if success:
        logger.info(f"Discord message sent to {label}")
    else:
        logger.error(f"Failed to send Discord message to {label}")
    return success


async def send_via_slack_async(params: NotificationParams) -> bool:
    """Async Slack send — opens a DM for a user id, posts directly to a channel."""
    target = params.notification_target
    message = params.message

    # Truncate message if it exceeds Slack's limit
    if len(message) > SLACK_MESSAGE_LIMIT:
        message = message[: SLACK_MESSAGE_LIMIT - 3] + "..."
        logger.warning(
            f"Slack message truncated from {len(params.message)} to {SLACK_MESSAGE_LIMIT} chars"
        )

    if not target:
        logger.error("No Slack target for notification")
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
        base_url = slack_api_base_url(credentials.slack_app)

    try:
        if slack_target_is_dm(target):
            data = await async_slack_call(
                slack_token, "conversations.open", base_url=base_url, users=target
            )
            channel_id = data.get("channel", {}).get("id")
            if not channel_id:
                raise ValueError(f"Failed to open DM with Slack user {target}")
            label = f"user {target}"
        else:
            channel_id = target
            label = f"channel {target}"

        await async_slack_call(
            slack_token,
            "chat.postMessage",
            base_url=base_url,
            channel=channel_id,
            text=message,
        )

        logger.info(f"Slack message sent to {label}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Slack message: {e}")
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
        message = (
            message[: EMAIL_BODY_LIMIT - 100]
            + "\n\n[Message truncated due to size limit]"
        )
        logger.warning(
            f"Email body truncated from {len(params.message)} to {EMAIL_BODY_LIMIT} chars"
        )

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
        headers={PASSWORD_RESET_HEADER: "1"} if params.data.get("password_reset") else None,
    )

    if result.success:
        logger.info(f"Email sent to {to_address}")
        return True
    logger.error(f"Failed to send email: {result.error}")
    return False


def deliver_notification(params: NotificationParams) -> bool:
    """Route a notification to the right channel and deliver it."""
    channel = params.notification_channel

    if channel == "discord":
        return send_via_discord(params)
    elif channel == "slack":
        return send_via_slack(params)
    elif channel == "email":
        return send_via_email(params)

    logger.error(f"Unknown notification_channel: {channel}")
    return False


@app.task(name=SEND_NOTIFICATION)
@tracked_task
def send_notification(
    channel: str,
    target: str,
    message: str,
    user_id: int,
    topic: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deliver a one-off notification immediately (fire-and-forget).

    No ScheduledTask / TaskExecution rows: immediate sends are not scheduled
    work. Success/failure is recorded in the worker log only.
    """
    params = NotificationParams(
        notification_channel=channel,
        notification_target=target,
        message=message,
        user_id=user_id,
        topic=topic,
        data=data or {},
    )
    sent = deliver_notification(params)
    if not sent:
        logger.error(
            f"Immediate notification to {channel}:{target} failed for user {user_id}"
        )
    return {"sent": sent}


def check_notification_principal(db, task: ScheduledTask) -> None:
    """Re-check, at fire time, that the API key a notification was scheduled
    through is still valid and may still address its target. Only Discord
    targets depend on scopes: admins may DM anyone, others whom their bots have seen."""
    key_id = (task.data or {}).get("api_key_id")
    if key_id is None:
        return
    user = db.get(User, task.user_id)
    if user is None:
        raise PrincipalRevoked(f"user {task.user_id} no longer exists")
    key = db.get(APIKey, key_id)
    if key is None or not key.is_valid():
        raise PrincipalRevoked(f"the API key that set this up ({key_id}) is no longer valid")
    if task.notification_channel != "discord" or not task.notification_target:
        return
    caller = UserProxy.from_user(user, scopes=credential_scopes(user, key))
    channel = discord_channel_for_target(db, task.notification_target)
    visible = (
        caller_can_see_discord_channel(db, caller, channel)
        if channel is not None
        else caller_can_see_discord_user(db, caller, int(task.notification_target))
    )
    if not visible:
        raise PrincipalRevoked("the API key that set this up may not message its target")


def revoke_schedule(db, task: ScheduledTask, error: PrincipalRevoked) -> None:
    """Disable the schedule, so its owner is told once rather than every tick."""
    task.enabled = False
    db.commit()
    user = db.get(User, task.user_id)
    if user is None:
        return
    try:
        notify_user_now(
            db,
            user,
            subject=f"Scheduled task paused: {task.topic or task.id}",
            message=f"Scheduled task {task.id} was disabled: {error}",
        )
    except Exception:
        logger.exception("could not notify user %s about scheduled task %s", user.id, task.id)


@app.task(bind=True, name=EXECUTE_SCHEDULED_TASK)
@tracked_task
def execute_scheduled_task(self, execution_id: str):
    """Execute a scheduled task."""
    logger.info(f"Executing scheduled task execution: {execution_id}")

    with make_session() as session:
        execution = session.get(TaskExecution, execution_id)

        if not execution:
            logger.error(f"TaskExecution {execution_id} not found")
            return {"error": "Execution not found"}

        if execution.status != ExecutionStatus.PENDING:
            logger.warning(
                f"TaskExecution {execution_id} is not pending (status: {execution.status})"
            )
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

        if task_type == TaskType.AGENT_RUN:
            # Starting a run needs the API image, so the agents worker owns this
            # execution from here. While it's pending, stuck-pending recovery
            # re-dispatches it, and the run's dedupe key keeps that idempotent.
            app.send_task(START_CRON_RUN, args=[execution_id])
            return {"handed_off": START_CRON_RUN, "execution_id": execution_id}

        # Mark as running and commit - use try/finally to ensure we always
        # update the final status even if something goes wrong after this point
        execution.status = ExecutionStatus.RUNNING
        execution.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()

        final_status = ExecutionStatus.FAILED
        error_message = None

        try:
            if task_type == TaskType.NOTIFICATION:
                params = extract_notification_params(task)
                if not params:
                    raise ValueError(
                        "Missing notification_channel or notification_target"
                    )
                try:
                    check_notification_principal(session, task)
                except PrincipalRevoked as e:
                    revoke_schedule(session, task, e)
                    raise

                sent = deliver_notification(params)
                if sent:
                    final_status = ExecutionStatus.COMPLETED
                else:
                    error_message = "Failed to send notification"

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
            execution.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()

        return {
            "success": final_status != ExecutionStatus.FAILED,
            "execution_id": execution_id,
            "task_id": task_id,
            "task_type": task_type,
        }


@app.task(name=RUN_SCHEDULED_TASKS)
@tracked_task
def run_scheduled_tasks():
    """Find and dispatch due scheduled tasks."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with make_session() as session:
        # 1. Recover stale executions (stuck in "running" for too long).
        stale_cutoff = now - timedelta(hours=STALE_EXECUTION_TIMEOUT_HOURS)
        stale_executions = (
            session.query(TaskExecution)
            .filter(
                TaskExecution.status == ExecutionStatus.RUNNING,
                TaskExecution.finished_at.is_(None),
                TaskExecution.started_at < stale_cutoff,
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        for stale in stale_executions:
            logger.warning(
                f"Recovering stale execution {stale.id} (stuck since {stale.started_at})"
            )
            stale.status = ExecutionStatus.FAILED
            stale.error_message = "Recovered from stale execution state"
            stale.finished_at = now

        # 1b. Recover stuck pending executions (pending for too long without being picked up)
        # This handles the case where tasks were dispatched after commit but Celery never received them
        pending_cutoff = now - timedelta(
            minutes=30
        )  # Pending for more than 30 minutes is suspicious
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
            logger.warning(
                f"Re-dispatching stuck pending execution {stuck.id} (scheduled for {stuck.scheduled_time})"
            )
            recovered_pending_count += 1

        if stale_executions or stuck_pending:
            session.commit()
            if stale_executions:
                logger.info(f"Recovered {len(stale_executions)} stale executions")
            if stuck_pending:
                logger.info(
                    f"Found {recovered_pending_count} stuck pending executions to re-dispatch"
                )

        # 2. Find due tasks without pending or running executions
        active_executions_subq = (
            session.query(TaskExecution.task_id)
            .filter(
                TaskExecution.status.in_(
                    [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]
                )
            )
            .subquery()
        )

        due_tasks = (
            session.query(ScheduledTask)
            .filter(
                ScheduledTask.next_scheduled_time < now,
                ~ScheduledTask.id.in_(session.query(active_executions_subq.c.task_id)),
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        # 3. Create executions and dispatch
        execution_ids = []
        for task in due_tasks:
            scheduled_time = task.next_scheduled_time
            if task.cron_expression:
                task.next_scheduled_time = compute_next_cron(
                    task.cron_expression, task.user.timezone, now
                )
            else:
                task.next_scheduled_time = None

            execution = TaskExecution(
                task_id=task.id,
                scheduled_time=scheduled_time,
                status=ExecutionStatus.PENDING,
            )
            session.add(execution)
            session.flush()
            execution_ids.append(execution.id)

        session.commit()

        # Dispatch new executions
        for execution_id in execution_ids:
            app.send_task(EXECUTE_SCHEDULED_TASK, args=[execution_id])

        # Re-dispatch stuck pending executions
        for stuck in stuck_pending:
            app.send_task(EXECUTE_SCHEDULED_TASK, args=[stuck.id])

        return {
            "executions": execution_ids,
            "count": len(execution_ids),
            "recovered_stale": len(stale_executions),
            "recovered_pending": recovered_pending_count,
        }
