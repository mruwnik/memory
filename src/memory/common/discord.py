"""
Discord integration.

Every bot sends through the REST API at its API URL, whichever way it takes
its real-time events. The collector's API server reports gateway health.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import requests

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordBot, DiscordChannel, DiscordServer, DiscordUser
from memory.discord.rest import BotAccess, DiscordAPIError, DiscordRest, bot_access

logger = logging.getLogger(__name__)


def get_api_url() -> str:
    """Get the Discord API server URL"""
    host = settings.DISCORD_COLLECTOR_SERVER_URL
    port = settings.DISCORD_COLLECTOR_PORT
    return f"http://{host}:{port}"


def rest_access(bot_id: int) -> BotAccess | None:
    with make_session() as session:
        access = bot_access(session.get(DiscordBot, bot_id))
    if access is None:
        logger.warning(f"Discord bot {bot_id} cannot send: unknown, paused, tokenless or off the allowlist")
    return access


def resolve_id(model: type[DiscordChannel] | type[DiscordUser], identifier: int | str) -> int | None:
    """A snowflake as given, or the id of the channel or user with that name."""
    if str(identifier).isdigit():
        return int(identifier)
    column = DiscordChannel.name if model is DiscordChannel else DiscordUser.username
    with make_session() as session:
        row = session.query(model.id).filter(column == str(identifier)).order_by(model.id).first()
        return row[0] if row else None


def send_over_rest(access: BotAccess, action: Callable[[DiscordRest], Awaitable[Any]]) -> bool:
    async def run() -> None:
        async with DiscordRest(access) as rest:
            await action(rest)

    try:
        asyncio.run(run())
    except DiscordAPIError:
        logger.exception(f"Discord bot {access.bot_id} could not send")
        return False
    return True


def send_dm(bot_id: int, user_identifier: str, message: str) -> bool:
    """Send a DM to a user by id or username."""
    if not (access := rest_access(bot_id)):
        return False
    user_id = resolve_id(DiscordUser, user_identifier)
    if user_id is None:
        logger.error(f"Discord bot {bot_id}: no known user to DM")
        return False

    async def dm(rest: DiscordRest) -> None:
        await rest.send_message(await rest.open_dm(user_id), message)

    return send_over_rest(access, dm)


def send_to_channel(bot_id: int, channel: int | str, message: str) -> bool:
    """Send a message to a channel by name or id (an id also reaches threads)."""
    if not (access := rest_access(bot_id)):
        return False
    channel_id = resolve_id(DiscordChannel, channel)
    if channel_id is None:
        logger.error(f"Discord bot {bot_id}: no known channel to send to")
        return False
    return send_over_rest(access, lambda rest: rest.send_message(channel_id, message))


def add_reaction(bot_id: int, channel: int | str, message_id: int, emoji: str) -> bool:
    """Add a reaction to a message in a channel."""
    if not (access := rest_access(bot_id)):
        return False
    channel_id = resolve_id(DiscordChannel, channel)
    if channel_id is None:
        return False
    return send_over_rest(access, lambda rest: rest.add_reaction(channel_id, message_id, emoji))


def connected_bots() -> set[int]:
    """Ids of the bots connected to the gateway; none when the collector is unreachable."""
    try:
        response = requests.get(f"{get_api_url()}/health", timeout=5)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException:
        return set()
    return {
        int(bot_id) for bot_id, status in result.items() if isinstance(status, dict) and status.get("connected")
    }


def is_collector_healthy(bot_id: int) -> bool:
    return bot_id in connected_bots()


class BotNotConnected(Exception):
    """The collector has no gateway connection running for this bot."""


def refresh_discord_metadata(bot_id: int) -> dict[str, Any] | None:
    """Have the collector record a running bot's servers, channels and members again."""
    try:
        response = requests.post(f"{get_api_url()}/refresh_metadata/{bot_id}", timeout=30)
        if response.status_code == 404:
            raise BotNotConnected(bot_id)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        logger.exception("Failed to refresh Discord metadata")
        return None


def error_channel_id(bot_id: int) -> int | None:
    """The channel task-failure alerts go to: DISCORD_ERROR_CHANNEL as given
    when it is an id, else the one channel of that name in a server this bot
    verifiably owns. Other users' bots record channels and their names too, so
    a name is never looked up beyond the alert bot's own servers, and two
    matches there send nothing until the operator sets the id."""
    configured = str(settings.DISCORD_ERROR_CHANNEL)
    if configured.isdigit():
        return int(configured)
    with make_session() as session:
        ids = [
            row[0]
            for row in session.query(DiscordChannel.id)
            .join(DiscordServer, DiscordServer.id == DiscordChannel.server_id)
            .filter(
                DiscordChannel.name == configured,
                DiscordServer.bot_id == bot_id,
                DiscordServer.verified.is_(True),
            )
            .limit(2)
        ]
    if len(ids) != 1:
        logger.error(
            f"Discord bot {bot_id}: {len(ids) or 'no'} channels match DISCORD_ERROR_CHANNEL in its servers; "
            "set DISCORD_ERROR_CHANNEL to the channel id"
        )
        return None
    return ids[0]


def send_error_message(bot_id: int, message: str) -> bool:
    """Send a message to the operator's error channel."""
    if not (access := rest_access(bot_id)):
        return False
    if (channel_id := error_channel_id(bot_id)) is None:
        return False
    return send_over_rest(access, lambda rest: rest.send_message(channel_id, message))


def job_id_of(task_kwargs: dict[str, Any]) -> int | None:
    """The tracked job id a task was given, if it is one."""
    job_id = task_kwargs.get("job_id")
    return job_id if isinstance(job_id, int) and not isinstance(job_id, bool) else None


def failure_message(task_name: str, error_type: str, task_id: str | None = None, job_id: int | None = None) -> str:
    """An alert naming the task, the exception type and ids only: the error
    channel is outside the instance, so no item text, arguments or traceback."""
    lines = [f"🚨 **Task Failed: {task_name}**", f"**Error:** {error_type}"]
    if task_id:
        lines.append(f"**Task id:** {task_id}")
    if job_id:
        lines.append(f"**Job id:** {job_id}")
    return "\n".join(lines)


def notify_task_failure(
    task_name: str,
    error_type: str,
    task_id: str | None = None,
    job_id: int | None = None,
    bot_id: int | None = None,
) -> None:
    """Post a task failure to the operator's Discord error channel."""
    if not settings.DISCORD_NOTIFICATIONS_ENABLED:
        logger.debug("Discord notifications disabled")
        return

    bot_id = bot_id or settings.DISCORD_BOT_ID
    if not bot_id:
        logger.debug("No Discord bot ID provided for task failure notification; skipping")
        return

    try:
        send_error_message(bot_id, failure_message(task_name, error_type, task_id, job_id))
        logger.info(f"Discord error notification sent for task: {task_name}")
    except Exception:
        logger.exception("Failed to send Discord notification")
