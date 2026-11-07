"""Notifications (Discord/Slack/Email): immediate ones, and the messages of due
ticks and timers."""

import logging
from typing import Any

from memory.agents import clock
from memory.common.celery_app import SEND_NOTIFICATION, SEND_TIME_NOTIFY, app
from memory.common.jobs import tracked_task
from memory.common.notify_delivery import NotificationParams, deliver_notification

logger = logging.getLogger(__name__)


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
    """Deliver a notification now (fire-and-forget): the outcome is only logged."""
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
        logger.error("Immediate notification over %s failed for user %s", channel, user_id)
    return {"sent": sent}


@app.task(name=SEND_TIME_NOTIFY)
def send_time_notify(event_id: int) -> str | None:
    """Send a due tick's or timer's message; a redelivery finds it ended and does nothing."""
    return clock.send_time_notify(event_id)
