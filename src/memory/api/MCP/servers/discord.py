"""MCP subserver for Discord messaging."""

import asyncio
import logging
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import or_
from fastmcp.server.dependencies import get_access_token

from memory.api.MCP.access import fetch_user_by_token
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import discord as discord_client
from memory.common.dates import parse_iso_datetime
from memory.common.scopes import (
    SCOPE_DISCORD,
    SCOPE_DISCORD_WRITE,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordUser,
)
from memory.common.people import find_person, primary_contact
from memory.common.discord_data import (
    fetch_channel_history,
    fetch_channels,
)

logger = logging.getLogger(__name__)

discord_mcp = FastMCP("memory-discord")


async def has_discord_bots(user_info: dict, session: DBSession | None) -> bool:
    """Visibility checker: only show Discord tools if user has authorized bots."""
    token = user_info.get("token")
    if not token:
        return False

    def _check() -> bool:
        # Create our own session to avoid threading issues with passed session
        with make_session() as local_session:
            user = fetch_user_by_token(local_session, token)
            if not user:
                return False
            # Check if user has any authorized Discord bots
            return len(user.discord_bots) > 0

    return await asyncio.to_thread(_check)


def _get_user_and_bots(session: DBSession) -> tuple[int, list[DiscordBot]]:
    """Get the current user ID and their authorized bots."""
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    user = fetch_user_by_token(session, access_token.token)
    if not user:
        raise ValueError("User not found")

    return user.id, list(user.discord_bots)


def _get_default_bot(session: DBSession) -> DiscordBot:
    """Get the user's default (first) Discord bot."""
    _, bots = _get_user_and_bots(session)
    if not bots:
        raise ValueError("No Discord bots configured for this user")
    return bots[0]


def resolve_bot_id(bot_id: str | None, session: DBSession | None = None) -> int:
    """Resolve and authorise bot_id, using the caller's default bot if None.

    Always verifies that the authenticated user owns (or has access to) the
    specified bot.  Uses ``resolve_bot`` internally so the ownership check
    cannot be bypassed by passing an explicit ID.

    Args:
        bot_id: Explicit bot snowflake (as string), or None to use default bot.
                Snowflakes must cross MCP boundaries as strings to avoid JS
                Number precision loss.
        session: Optional session to use. If not provided, creates a new session.
                 Pass an existing session to avoid nested make_session() calls
                 which can cause DetachedInstanceError.
    """
    if session is not None:
        return resolve_bot(session, bot_id)
    with make_session() as new_session:
        return resolve_bot(new_session, bot_id)


def _to_snowflake(value: int | str) -> int:
    """Convert a snowflake ID (int or string) to int."""
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid snowflake ID: '{value}' is not a valid integer")


def resolve_guild_id(
    session: DBSession,
    guild: str | None,
) -> int:
    """
    Resolve a guild ID from either numeric ID or server name.

    Args:
        session: Database session
        guild: Discord guild ID (snowflake) or server name

    Returns:
        The resolved guild ID as int

    Raises:
        ValueError: If guild is None or name not found
    """
    if guild is None:
        raise ValueError("Must specify guild")

    resolved = discord_client.resolve_guild(guild, session)
    if resolved is None:
        raise ValueError(f"Server '{guild}' not found")
    return resolved


def resolve_bot(session: DBSession, bot_id: str | None) -> int:
    """Resolve and authorize a bot snowflake (string at the boundary)."""
    if bot_id is not None:
        bot_pk = _to_snowflake(bot_id)
        bot = session.get(DiscordBot, bot_pk)
        if not bot:
            raise ValueError(f"Bot {bot_id} not found")
        _, user_bots = _get_user_and_bots(session)
        if bot not in user_bots:
            raise ValueError(f"You don't have access to bot {bot_id}")
        return bot.id

    return _get_default_bot(session).id


async def send_to_channel(
    session: DBSession,
    bot_id: int,
    message: str,
    channel_id: str | None,
    channel_name: str | None,
) -> dict[str, Any]:
    """Resolve channel and send message."""
    if channel_id is not None:
        target = channel_id
    else:
        channel = (
            session.query(DiscordChannel)
            .filter(DiscordChannel.name == channel_name)
            .first()
        )
        if not channel:
            raise ValueError(f"Channel '{channel_name}' not found")
        target = str(channel.id)

    success = await asyncio.to_thread(
        discord_client.send_to_channel, bot_id, target, message
    )
    preview = message[:100] + "..." if len(message) > 100 else message
    return {"success": success, "type": "channel", "target": target, "message_preview": preview}


async def send_dm(
    session: DBSession,
    bot_id: int,
    message: str,
    user_id: str | None,
    username: str | None,
) -> dict[str, Any]:
    """Resolve user and send DM, with Person fallback on failure.

    Note: returns {"success": False, ...} with suggestions instead of raising
    when the Discord user is not found. This is intentional -- it lets MCP tool
    callers display suggestions to the end user rather than surfacing a raw error.
    """
    target = resolve_discord_user(session, user_id, username)
    if target is None:
        suggestions = suggest_people(session, username or "")
        error = f"Discord user '{username}' not found"
        if suggestions:
            error += ", but found matching people"
        return {"success": False, "error": error, "suggestions": suggestions}

    success = await asyncio.to_thread(discord_client.send_dm, bot_id, target, message)
    preview = message[:100] + "..." if len(message) > 100 else message
    return {"success": success, "type": "dm", "target": target, "message_preview": preview}


def resolve_discord_user(
    session: DBSession,
    user_id: str | None,
    username: str | None,
) -> str | None:
    """Resolve a Discord user ID from explicit ID or username. Returns None if not found."""
    if user_id is not None:
        return str(user_id)

    discord_user = (
        session.query(DiscordUser)
        .filter(or_(DiscordUser.username == username, DiscordUser.display_name == username))
        .first()
    )
    if discord_user:
        return str(discord_user.id)

    return None


def suggest_people(session: DBSession, query: str) -> list[dict[str, str]]:
    """Find Person records that might match a failed username lookup.

    Returns at most one suggestion (find_person returns a single match).
    """
    person = find_person(session, query)
    if not person:
        return []

    discord_id = None
    discord_username = None
    if person.discord_accounts:
        discord_id = str(person.discord_accounts[0].id)
        discord_username = person.discord_accounts[0].username
    elif person.contact_info:
        discord_id = primary_contact(person.contact_info.get("discord_id"))
        discord_username = primary_contact(person.contact_info.get("discord"))

    if not discord_id:
        return []

    return [{
        "person": person.identifier,
        "display_name": person.display_name,
        "discord_id": str(discord_id),
        "discord_username": discord_username or "",
    }]


@discord_mcp.tool()
@visible_when(require_scopes(SCOPE_DISCORD_WRITE), has_discord_bots)
async def send_message(
    message: str,
    channel_id: str | None = None,
    channel_name: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    bot_id: str | None = None,
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

    has_channel = channel_id is not None or channel_name is not None
    has_user = user_id is not None or username is not None

    if not has_channel and not has_user:
        raise ValueError("Must specify either channel_id/channel_name or user_id/username")
    if has_channel and has_user:
        raise ValueError("Cannot specify both channel and user destinations")

    with make_session() as session:
        resolved_bot_id = resolve_bot(session, bot_id)

        if has_channel:
            return await send_to_channel(session, resolved_bot_id, message, channel_id, channel_name)

        return await send_dm(session, resolved_bot_id, message, user_id, username)


@discord_mcp.tool()
@visible_when(require_scopes(SCOPE_DISCORD), has_discord_bots)
async def channel_history(
    channel_id: str | None = None,
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
    before_dt = parse_iso_datetime(before)
    if before and before_dt is None:
        raise ValueError(f"Invalid 'before' datetime format: {before}")
    after_dt = parse_iso_datetime(after)
    if after and after_dt is None:
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
@visible_when(require_scopes(SCOPE_DISCORD), has_discord_bots)
async def list_channels(
    server: str | None = None,
    include_dms: bool = False,
) -> dict[str, Any]:
    """
    List Discord channels the bot has access to.

    Args:
        server: Filter by server - can be numeric ID or server name
        include_dms: Include DM channels (default False)

    Returns:
        Dict with channels list
    """
    with make_session() as session:
        return await asyncio.to_thread(fetch_channels, session, server, include_dms)
