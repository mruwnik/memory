"""
Discord integration.

Simple HTTP client that communicates with the Discord collector's API server.
"""

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.scoping import scoped_session

from memory.common import settings
from memory.common.db.models import DiscordServer

logger = logging.getLogger(__name__)


def get_api_url() -> str:
    """Get the Discord API server URL"""
    host = settings.DISCORD_COLLECTOR_SERVER_URL
    port = settings.DISCORD_COLLECTOR_PORT
    return f"http://{host}:{port}"


def send_dm(bot_id: int, user_identifier: str, message: str) -> bool:
    """Send a DM via the Discord collector API"""
    try:
        response = requests.post(
            f"{get_api_url()}/send_dm",
            json={"bot_id": bot_id, "user": user_identifier, "message": message},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to send DM to {user_identifier}: {e}")
        return False


def send_to_channel(bot_id: int, channel: int | str, message: str) -> bool:
    """Send message to a channel by name or ID (ID supports threads)"""
    try:
        response = requests.post(
            f"{get_api_url()}/send_channel",
            json={
                "bot_id": bot_id,
                "channel": channel,
                "message": message,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to send to channel {channel}: {e}")
        return False


def add_reaction(bot_id: int, channel: int | str, message_id: int, emoji: str) -> bool:
    """Add a reaction to a message in a channel"""
    try:
        response = requests.post(
            f"{get_api_url()}/add_reaction",
            json={
                "bot_id": bot_id,
                "channel": channel,
                "message_id": message_id,
                "emoji": emoji,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(
            f"Failed to add reaction {emoji} to message {message_id} in channel {channel}: {e}"
        )
        return False


def broadcast_message(bot_id: int, channel: int | str, message: str) -> bool:
    """Send a message to a channel by name or ID (ID supports threads)"""
    try:
        response = requests.post(
            f"{get_api_url()}/send_channel",
            json={
                "bot_id": bot_id,
                "channel": channel,
                "message": message,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to send message to channel {channel}: {e}")
        return False


def is_collector_healthy(bot_id: int) -> bool:
    """Check if the Discord collector is running and healthy"""
    try:
        response = requests.get(f"{get_api_url()}/health", timeout=5)
        response.raise_for_status()
        result = response.json()
        bot_status = result.get(str(bot_id))
        if not isinstance(bot_status, dict):
            return False
        return bool(bot_status.get("connected"))

    except requests.RequestException:
        return False


def refresh_discord_metadata() -> dict[str, Any] | None:
    """Refresh Discord server/channel/user metadata from Discord API"""
    try:
        response = requests.post(f"{get_api_url()}/refresh_metadata", timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to refresh Discord metadata: {e}")
        return None


# =============================================================================
# Resolution Helpers
# =============================================================================


def resolve_guild(guild: int | str | None, session: "Session | scoped_session[Session] | None" = None) -> int | None:
    """Resolve guild ID from either numeric ID or server name.

    Args:
        guild: Numeric guild ID (int or numeric string) or server name, or None
        session: Database session (required only for name lookups)

    Returns:
        Resolved guild ID as int, or None if guild is None

    Raises:
        ValueError: If guild is a name string but session is None, or name not found
    """
    if guild is None:
        return None

    # Try as numeric first
    if isinstance(guild, int):
        return guild
    try:
        return int(guild)
    except ValueError:
        pass

    # It's a name string - need database lookup
    if session is None:
        raise ValueError("Database session required to resolve guild by name")

    server = session.query(DiscordServer).filter(DiscordServer.name == guild).first()
    if not server:
        raise ValueError(f"Discord server '{guild}' not found")
    return server.id


# Convenience functions
def send_error_message(bot_id: int, message: str) -> bool:
    """Send an error message to the error channel"""
    return broadcast_message(bot_id, settings.DISCORD_ERROR_CHANNEL, message)


def notify_task_failure(
    task_name: str,
    error_message: str,
    task_args: tuple = (),
    task_kwargs: dict[str, Any] | None = None,
    traceback_str: str | None = None,
    bot_id: int | None = None,
) -> None:
    """
    Send a task failure notification to Discord.

    Args:
        task_name: Name of the failed task
        error_message: Error message
        task_args: Task arguments
        task_kwargs: Task keyword arguments
        traceback_str: Full traceback string
    """
    if not settings.DISCORD_NOTIFICATIONS_ENABLED:
        logger.debug("Discord notifications disabled")
        return

    if bot_id is None:
        bot_id = settings.DISCORD_BOT_ID

    if not bot_id:
        logger.debug(
            "No Discord bot ID provided for task failure notification; skipping"
        )
        return

    message = f"🚨 **Task Failed: {task_name}**\n\n"
    message += f"**Error:** {error_message[:500]}\n"

    if task_args:
        message += f"**Args:** `{str(task_args)[:200]}`\n"

    if task_kwargs:
        message += f"**Kwargs:** `{str(task_kwargs)[:200]}`\n"

    if traceback_str:
        message += f"**Traceback:**\n```\n{traceback_str[-800:]}\n```"

    try:
        send_error_message(bot_id, message)
        logger.info(f"Discord error notification sent for task: {task_name}")
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")
