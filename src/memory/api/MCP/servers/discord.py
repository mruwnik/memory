"""MCP subserver for Discord messaging."""

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import or_
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


def resolve_bot_id(bot_id: int | None) -> int:
    """Resolve bot_id, using default bot if None."""
    if bot_id is not None:
        return bot_id
    with make_session() as session:
        return _get_default_bot(session).id


async def _call_discord_api(func, *args, error_msg: str) -> dict[str, Any]:
    """Call a discord_client function and raise on None result."""
    result = await asyncio.to_thread(func, *args)
    if result is None:
        raise ValueError(error_msg)
    return result


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
                "message_preview": message[:100] + "..."
                if len(message) > 100
                else message,
            }

        # Resolve user for DM
        if user_id is not None:
            target = str(user_id)
        else:
            # Look up user by username or display_name
            discord_user = (
                session.query(DiscordUser)
                .filter(
                    or_(
                        DiscordUser.username == username,
                        DiscordUser.display_name == username,
                    )
                )
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
async def channel_history(
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
    server_id: int | str | None = None,
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


@discord_mcp.tool()
@visible_when(require_scopes("discord"), has_discord_bots)
async def list_roles(
    guild_id: int,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    List all roles in a Discord server.

    Args:
        guild_id: Discord server/guild ID
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with roles list including id, name, color, position, member_count
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.list_roles,
        resolved_bot_id,
        guild_id,
        error_msg=f"Failed to list roles for guild {guild_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def list_role_members(
    guild_id: int,
    role_id: int,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    List all members who have a specific role.

    Args:
        guild_id: Discord server/guild ID
        role_id: Role ID to list members for
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with role name and list of members (id, username, display_name)
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.list_role_members,
        resolved_bot_id,
        guild_id,
        role_id,
        error_msg=f"Failed to list members for role {role_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def list_categories(
    guild_id: int,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    List all categories in a Discord server.

    Args:
        guild_id: Discord server/guild ID
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with categories list including id, name, position, and child channels
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.list_categories,
        resolved_bot_id,
        guild_id,
        error_msg=f"Failed to list categories for guild {guild_id}",
    )


# =============================================================================
# Role Management Tools
# =============================================================================


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def add_user_to_role(
    guild_id: int,
    role_id: int,
    user_id: int,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Add a user to a Discord role.

    Args:
        guild_id: Discord server/guild ID
        role_id: Role ID to add user to
        user_id: User ID to add
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status, user name, and role name
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.add_role_member,
        resolved_bot_id,
        guild_id,
        role_id,
        user_id,
        error_msg=f"Failed to add user {user_id} to role {role_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def role_remove(
    guild_id: int,
    role_id: int,
    user_id: int,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Remove a user from a Discord role.

    Args:
        guild_id: Discord server/guild ID
        role_id: Role ID to remove user from
        user_id: User ID to remove
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status, user name, and role name
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.remove_role_member,
        resolved_bot_id,
        guild_id,
        role_id,
        user_id,
        error_msg=f"Failed to remove user {user_id} from role {role_id}",
    )


# =============================================================================
# Channel Permission Tools
# =============================================================================


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def perms(
    channel_id: int,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Get permission overwrites for a Discord channel.

    Shows which roles and users have special permissions on the channel.

    Args:
        channel_id: Discord channel ID
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with channel name and list of permission overwrites
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.get_channel_permissions,
        resolved_bot_id,
        channel_id,
        error_msg=f"Failed to get permissions for channel {channel_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def set_perms(
    channel_id: int,
    role_id: int | None = None,
    user_id: int | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Set permission overwrite for a role or user on a channel.

    Common permission names: view_channel, send_messages, read_message_history,
    manage_messages, manage_channels, add_reactions, attach_files, embed_links

    Args:
        channel_id: Discord channel ID
        role_id: Role ID to set permissions for (mutually exclusive with user_id)
        user_id: User ID to set permissions for (mutually exclusive with role_id)
        allow: List of permission names to allow
        deny: List of permission names to deny
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status
    """
    if not role_id and not user_id:
        raise ValueError("Must specify either role_id or user_id")

    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.set_channel_permission,
        resolved_bot_id,
        channel_id,
        role_id,
        user_id,
        allow,
        deny,
        error_msg=f"Failed to set permissions for channel {channel_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def del_perms(
    channel_id: int,
    target_id: int,
    target_type: str = "role",
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Remove permission overwrite for a role or user from a channel.

    Args:
        channel_id: Discord channel ID
        target_id: Role or user ID to remove permissions for
        target_type: "role" or "user" (default: "role")
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.remove_channel_permission,
        resolved_bot_id,
        channel_id,
        target_id,
        target_type,
        error_msg=f"Failed to remove permissions for channel {channel_id}",
    )


# =============================================================================
# Channel/Category Management Tools
# =============================================================================


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def create_channel(
    guild_id: int,
    name: str,
    category_id: int | None = None,
    topic: str | None = None,
    copy_permissions_from: int | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new text channel in a Discord server.

    Args:
        guild_id: Discord server/guild ID
        name: Channel name
        category_id: Optional category to create channel in
        topic: Optional channel topic/description
        copy_permissions_from: Optional channel ID to copy permissions from
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and new channel info
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.create_channel,
        resolved_bot_id,
        guild_id,
        name,
        category_id,
        topic,
        copy_permissions_from,
        error_msg=f"Failed to create channel {name}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def create_category(
    guild_id: int,
    name: str,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new category in a Discord server.

    Args:
        guild_id: Discord server/guild ID
        name: Category name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and new category info
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    return await _call_discord_api(
        discord_client.create_category,
        resolved_bot_id,
        guild_id,
        name,
        error_msg=f"Failed to create category {name}",
    )
