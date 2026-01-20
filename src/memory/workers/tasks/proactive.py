"""
Celery tasks for proactive Discord check-ins.
"""

import logging
import re
import textwrap
from datetime import datetime, timezone
from typing import Any, Literal, cast

from croniter import croniter

from memory.common import settings
from memory.common.celery_app import app
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordChannel, DiscordServer, DiscordUser
from memory.discord.messages import call_llm, comm_channel_prompt, send_discord_response
from memory.common.content_processing import safe_task_execution

logger = logging.getLogger(__name__)

EVALUATE_PROACTIVE_CHECKINS = "memory.workers.tasks.proactive.evaluate_proactive_checkins"
EXECUTE_PROACTIVE_CHECKIN = "memory.workers.tasks.proactive.execute_proactive_checkin"

EntityType = Literal["user", "channel", "server"]


def is_cron_due(cron_expr: str, last_run: datetime | None, now: datetime) -> bool:
    """Check if a cron expression is due to run now.

    Uses croniter to determine if the current time falls within the cron's schedule
    and enough time has passed since the last run.
    """
    try:
        cron = croniter(cron_expr, now)
        # Get the previous scheduled time from now
        prev_run = cron.get_prev(datetime)
        # Get the one before that to determine the interval
        cron.get_prev(datetime)
        cron.get_current(datetime)

        # If we haven't run since the last scheduled time, we should run
        if last_run is None:
            # Never run before - check if current time is within a minute of prev_run
            time_since_scheduled = (now - prev_run).total_seconds()
            return time_since_scheduled < 120  # Within 2 minutes of scheduled time

        # Make sure last_run is timezone aware
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)

        # We should run if last_run is before the previous scheduled time
        return last_run < prev_run
    except Exception as e:
        logger.warning(f"Invalid cron expression '{cron_expr}': {e}")
        return False


def get_bot_for_entity(
    session, entity_type: EntityType, entity_id: int
) -> DiscordUser | None:
    """Get the bot user associated with an entity."""
    from memory.common.db.models import DiscordBotUser, DiscordMessage

    from sqlalchemy.orm import joinedload

    # For servers, find a bot that has sent messages in that server
    if entity_type == "server":
        # Find bots that have interacted with this server
        bot_users = (
            session.query(DiscordUser)
            .options(joinedload(DiscordUser.system_user))
            .join(DiscordMessage, DiscordMessage.from_id == DiscordUser.id)
            .filter(
                DiscordMessage.server_id == entity_id,
                DiscordUser.system_user_id.isnot(None),
            )
            .distinct()
            .all()
        )
        # Find one that's actually a bot
        for user in bot_users:
            if user.system_user and user.system_user.user_type == "discord_bot":
                return user

    # For channels, check the server the channel belongs to
    if entity_type == "channel":
        channel = session.get(DiscordChannel, entity_id)
        if channel and channel.server_id:
            return get_bot_for_entity(session, "server", channel.server_id)

    # Fallback: use first available bot
    bot = (
        session.query(DiscordBotUser)
        .options(joinedload(DiscordBotUser.discord_users).joinedload(DiscordUser.system_user))
        .first()
    )
    if bot and bot.discord_users:
        return bot.discord_users[0]
    return None


def get_target_user_for_entity(
    session, entity_type: EntityType, entity_id: int
) -> DiscordUser | None:
    """Get the target user for sending a proactive message."""
    if entity_type == "user":
        return session.get(DiscordUser, entity_id)
    # For channels and servers, we don't have a specific target user
    return None


def get_channel_for_entity(
    session, entity_type: EntityType, entity_id: int
) -> DiscordChannel | None:
    """Get the channel for sending a proactive message."""
    if entity_type == "channel":
        return session.get(DiscordChannel, entity_id)
    if entity_type == "server":
        # For servers, find the first text channel (prefer "general")
        channels = (
            session.query(DiscordChannel)
            .filter(
                DiscordChannel.server_id == entity_id,
                DiscordChannel.channel_type == "text",
            )
            .all()
        )
        if not channels:
            return None
        # Prefer a channel named "general" if it exists
        for channel in channels:
            if channel.name and "general" in channel.name.lower():
                return channel
        return channels[0]
    # For users, we use DMs (no channel)
    return None


@app.task(name=EVALUATE_PROACTIVE_CHECKINS)
@safe_task_execution
def evaluate_proactive_checkins() -> dict[str, Any]:
    """
    Evaluate which entities need proactive check-ins.

    This task runs every minute and checks all entities with proactive_cron set
    to see if they're due for a check-in.
    """
    now = datetime.now(timezone.utc)
    dispatched = []

    with make_session() as session:
        # Query all entities with proactive_cron set
        for model, entity_type in [
            (DiscordUser, "user"),
            (DiscordChannel, "channel"),
            (DiscordServer, "server"),
        ]:
            entities = (
                session.query(model)
                .filter(model.proactive_cron.isnot(None))
                .all()
            )

            for entity in entities:
                cron_expr = cast(str, entity.proactive_cron)
                last_run = entity.last_proactive_at

                if is_cron_due(cron_expr, last_run, now):
                    logger.info(
                        f"Proactive check-in due for {entity_type} {entity.id}"
                    )
                    execute_proactive_checkin.delay(entity_type, entity.id)
                    dispatched.append({"type": entity_type, "id": entity.id})

    return {
        "evaluated_at": now.isoformat(),
        "dispatched": dispatched,
        "count": len(dispatched),
    }


@app.task(name=EXECUTE_PROACTIVE_CHECKIN)
@safe_task_execution
def execute_proactive_checkin(entity_type: EntityType, entity_id: int) -> dict[str, Any]:
    """
    Execute a proactive check-in for a specific entity.

    This evaluates whether the bot should reach out and, if so, generates
    and sends a check-in message.
    """
    logger.info(f"Executing proactive check-in for {entity_type} {entity_id}")

    with make_session() as session:
        # Get the entity
        model_class = {
            "user": DiscordUser,
            "channel": DiscordChannel,
            "server": DiscordServer,
        }[entity_type]

        entity = session.get(model_class, entity_id)
        if not entity:
            return {"error": f"{entity_type} {entity_id} not found"}

        # Get the bot user
        bot_user = get_bot_for_entity(session, entity_type, entity_id)
        if not bot_user:
            return {"error": "No bot user found"}

        # Get target user and channel
        target_user = get_target_user_for_entity(session, entity_type, entity_id)
        channel = get_channel_for_entity(session, entity_type, entity_id)

        if not target_user and not channel:
            return {"error": "No target user or channel for proactive check-in"}

        # Get chattiness threshold
        chattiness = entity.chattiness_threshold or 90

        # Build the evaluation prompt
        proactive_prompt = entity.proactive_prompt or ""
        eval_prompt = textwrap.dedent("""
            You are considering whether to proactively reach out to check in.

            {proactive_prompt}

            Based on your notes and the context of previous conversations:
            1. Is there anything worth checking in about?
            2. Has enough happened or enough time passed to warrant a check-in?
            3. Would reaching out now be welcome or intrusive?

            Please return a number between 0 and 100 indicating how strongly you want to check in
            (0 = definitely not, 100 = definitely yes).

            <response>
                <number>50</number>
                <reason>Your reasoning here</reason>
            </response>
        """).format(proactive_prompt=proactive_prompt)

        # Build context
        system_prompt = comm_channel_prompt(
            session, bot_user, target_user, channel
        )

        # First, evaluate whether we should check in
        eval_response = call_llm(
            session,
            bot_user=bot_user,
            from_user=target_user,
            channel=channel,
            model=settings.SUMMARIZER_MODEL,
            system_prompt=system_prompt,
            messages=[eval_prompt],
            allowed_tools=[
                "update_channel_summary",
                "update_user_summary",
                "update_server_summary",
            ],
        )

        if not eval_response:
            entity.last_proactive_at = datetime.now(timezone.utc)
            session.commit()
            return {"status": "no_eval_response", "entity_type": entity_type, "entity_id": entity_id}

        # Parse the interest score
        match = re.search(r"<number>(\d+)</number>", eval_response)
        if not match:
            entity.last_proactive_at = datetime.now(timezone.utc)
            session.commit()
            return {"status": "no_score_in_response", "entity_type": entity_type, "entity_id": entity_id}

        interest_score = int(match.group(1))
        threshold = 100 - chattiness

        logger.info(
            f"Proactive check-in eval: interest={interest_score}, threshold={threshold}, chattiness={chattiness}"
        )

        if interest_score < threshold:
            entity.last_proactive_at = datetime.now(timezone.utc)
            session.commit()
            return {
                "status": "below_threshold",
                "interest": interest_score,
                "threshold": threshold,
                "entity_type": entity_type,
                "entity_id": entity_id,
            }

        # Generate the actual check-in message
        checkin_prompt = textwrap.dedent("""
            You've decided to proactively check in. Generate a natural, friendly check-in message.

            {proactive_prompt}

            Keep it brief and genuine. Don't be overly formal or robotic.
            Reference specific things from your notes if relevant.
        """).format(proactive_prompt=proactive_prompt)

        response = call_llm(
            session,
            bot_user=bot_user,
            from_user=target_user,
            channel=channel,
            model=settings.DISCORD_MODEL,
            system_prompt=system_prompt,
            messages=[checkin_prompt],
        )

        if not response:
            entity.last_proactive_at = datetime.now(timezone.utc)
            session.commit()
            return {"status": "no_message_generated", "entity_type": entity_type, "entity_id": entity_id}

        # Send the message
        bot_id = bot_user.system_user.id if bot_user.system_user else None
        if not bot_id:
            return {"error": "No system user for bot"}

        success = send_discord_response(
            bot_id=bot_id,
            response=response,
            channel_id=channel.id if channel else None,
            user_identifier=target_user.username if target_user else None,
        )

        # Update last_proactive_at
        entity.last_proactive_at = datetime.now(timezone.utc)
        session.commit()

        return {
            "status": "sent" if success else "send_failed",
            "interest": interest_score,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "response_preview": response[:100] + "..." if len(response) > 100 else response,
        }
