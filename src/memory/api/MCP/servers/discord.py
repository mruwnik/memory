"""MCP subserver for Discord messaging."""

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import discord as discord_client
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordUser,
    UserSession,
)
from memory.common.discord_data import (
    fetch_channel_history,
    fetch_channels,
)

logger = logging.getLogger(__name__)

discord_mcp = FastMCP("memory-discord")


async def has_discord_bots(user_info: dict, session: DBSession | None) -> bool:
    """Visibility checker: only show Discord tools if user has authorized bots."""
    token = user_info.get("token")
    if not token or session is None:
        return False

    def _check(session: DBSession) -> bool:
        user_session = session.get(UserSession, token)
        if not user_session or not user_session.user:
            return False
        # Check if user has any authorized Discord bots
        return len(user_session.user.discord_bots) > 0

    return await asyncio.to_thread(_check, session)


def _get_user_and_bots(session: DBSession) -> tuple[int, list[DiscordBot]]:
    """Get the current user ID and their authorized bots."""
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        raise ValueError("User not found")

    return user_session.user.id, list(user_session.user.discord_bots)


def _get_default_bot(session: DBSession) -> DiscordBot:
    """Get the user's default (first) Discord bot."""
    _, bots = _get_user_and_bots(session)
    if not bots:
        raise ValueError("No Discord bots configured for this user")
    return bots[0]


@discord_mcp.tool()
@visible_when(require_scopes("discord"), has_discord_bots)
async def send_message(
    message: str,
    channel_id: int | None = None,
    channel_name: str | None = None,
    user_id: int | None = None,
    username: str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Send a Discord message to a channel or user via DM.

    You must specify either:
    - channel_id or channel_name to send to a channel
    - user_id or username to send a DM

    Args:
        message: The message content to send
        channel_id: Discord channel ID (snowflake) to send to
        channel_name: Discord channel name to send to (will search for it)
        user_id: Discord user ID (snowflake) to DM
        username: Discord username to DM (will search for it)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and details
    """
    if not message:
        raise ValueError("Message cannot be empty")

    # Must have at least one destination
    has_channel = channel_id is not None or channel_name is not None
    has_user = user_id is not None or username is not None

    if not has_channel and not has_user:
        raise ValueError(
            "Must specify either channel_id/channel_name or user_id/username"
        )

    if has_channel and has_user:
        raise ValueError("Cannot specify both channel and user destinations")

    with make_session() as session:
        # Get bot to use
        if bot_id is not None:
            bot = session.get(DiscordBot, bot_id)
            if not bot:
                raise ValueError(f"Bot {bot_id} not found")
            # Verify user has access to this bot
            _, user_bots = _get_user_and_bots(session)
            if bot not in user_bots:
                raise ValueError(f"You don't have access to bot {bot_id}")
        else:
            bot = _get_default_bot(session)

        resolved_bot_id = bot.id

        # Resolve channel
        if has_channel:
            if channel_id is not None:
                target = channel_id
            else:
                # Look up channel by name
                channel = (
                    session.query(DiscordChannel)
                    .filter(DiscordChannel.name == channel_name)
                    .first()
                )
                if not channel:
                    raise ValueError(f"Channel '{channel_name}' not found")
                target = channel.id

            # Send to channel (run in thread to avoid blocking)
            success = await asyncio.to_thread(
                discord_client.send_to_channel, resolved_bot_id, target, message
            )

            return {
                "success": success,
                "type": "channel",
                "target": target,
                "message_preview": message[:100] + "..." if len(message) > 100 else message,
            }

        # Resolve user for DM
        if user_id is not None:
            target = str(user_id)
        else:
            # Look up user by username
            discord_user = (
                session.query(DiscordUser)
                .filter(DiscordUser.username == username)
                .first()
            )
            if not discord_user:
                raise ValueError(f"User '{username}' not found")
            target = str(discord_user.id)

        # Send DM (run in thread to avoid blocking)
        success = await asyncio.to_thread(
            discord_client.send_dm, resolved_bot_id, target, message
        )

        return {
            "success": success,
            "type": "dm",
            "target": target,
            "message_preview": message[:100] + "..." if len(message) > 100 else message,
        }


@discord_mcp.tool()
@visible_when(require_scopes("discord"), has_discord_bots)
async def get_channel_history(
    channel_id: int | None = None,
    channel_name: str | None = None,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    """
    Get message history from a Discord channel.

    Args:
        channel_id: Discord channel ID (snowflake)
        channel_name: Discord channel name (will search for it)
        limit: Maximum number of messages to return (default 50, max 200)
        before: ISO datetime - only get messages before this time
        after: ISO datetime - only get messages after this time

    Returns:
        Dict with messages list and metadata
    """
    if channel_id is None and channel_name is None:
        raise ValueError("Must specify either channel_id or channel_name")

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    # Parse datetime filters
    before_dt = None
    after_dt = None

    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid 'before' datetime format: {before}")

    if after:
        try:
            after_dt = datetime.fromisoformat(after.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid 'after' datetime format: {after}")

    with make_session() as session:
        return await asyncio.to_thread(
            fetch_channel_history,
            session,
            channel_id,
            channel_name,
            before_dt,
            after_dt,
            limit,
        )


@discord_mcp.tool()
@visible_when(require_scopes("discord"), has_discord_bots)
async def list_channels(
    server_id: int | None = None,
    server_name: str | None = None,
    include_dms: bool = False,
) -> dict[str, Any]:
    """
    List Discord channels the bot has access to.

    Args:
        server_id: Filter by server ID
        server_name: Filter by server name
        include_dms: Include DM channels (default False)

    Returns:
        Dict with channels list
    """
    with make_session() as session:
        return await asyncio.to_thread(
            fetch_channels, session, server_id, server_name, include_dms
        )
