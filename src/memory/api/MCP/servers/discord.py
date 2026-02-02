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


def resolve_bot_id(bot_id: int | None, session: DBSession | None = None) -> int:
    """Resolve bot_id, using default bot if None.

    Args:
        bot_id: Explicit bot ID, or None to use default bot
        session: Optional session to use. If not provided, creates a new session.
                 Pass an existing session to avoid nested make_session() calls
                 which can cause DetachedInstanceError.
    """
    if bot_id is not None:
        return bot_id
    if session is not None:
        return _get_default_bot(session).id
    with make_session() as new_session:
        return _get_default_bot(new_session).id


async def _call_discord_api(func, *args, error_msg: str, **kwargs) -> dict[str, Any]:
    """Call a discord_client function and raise on None result."""
    result = await asyncio.to_thread(func, *args, **kwargs)
    if result is None:
        raise ValueError(error_msg)
    return result


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
    guild: int | str | None,
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


@discord_mcp.tool()
@visible_when(require_scopes("discord"), has_discord_bots)
async def send_message(
    message: str,
    channel_id: int | str | None = None,
    channel_name: str | None = None,
    user_id: int | str | None = None,
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
    channel_id: int | str | None = None,
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
    server: int | str | None = None,
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
        return await asyncio.to_thread(
            fetch_channels, session, server, include_dms
        )


@discord_mcp.tool()
@visible_when(require_scopes("discord"), has_discord_bots)
async def list_roles(
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    List all roles in a Discord server.

    Args:
        guild: Discord server - can be numeric ID or server name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with roles list including id, name, color, position, member_count
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)

    return await _call_discord_api(
        discord_client.list_roles,
        resolved_bot_id,
        resolved_guild_id,
        error_msg=f"Failed to list roles for guild {resolved_guild_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def list_role_members(
    role: int | str,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    List all members who have a specific role.

    Args:
        role: Role ID (snowflake) or role name
        guild: Discord server - can be numeric ID or server name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with role name and list of members (id, username, display_name)
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)

    resolved_role_id = resolve_role(role, resolved_guild_id, resolved_bot_id)

    return await _call_discord_api(
        discord_client.list_role_members,
        resolved_bot_id,
        resolved_guild_id,
        resolved_role_id,
        error_msg=f"Failed to list members for role {role}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def list_categories(
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    List all categories in a Discord server.

    Args:
        guild: Discord server - can be numeric ID or server name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with categories list including id, name, position, and child channels
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)

    return await _call_discord_api(
        discord_client.list_categories,
        resolved_bot_id,
        resolved_guild_id,
        error_msg=f"Failed to list categories for guild {resolved_guild_id}",
    )


# =============================================================================
# Role Management Tools
# =============================================================================


def resolve_role(
    role: int | str,
    guild_id: int,
    bot_id: int,
) -> int:
    """Resolve a role from either numeric ID or role name.

    Args:
        role: Role ID (snowflake as int or str) or role name
        guild_id: Discord guild ID
        bot_id: Discord bot ID

    Returns:
        The resolved role ID as int

    Raises:
        ValueError: If role name not found
    """
    # Try to parse as snowflake ID first
    if isinstance(role, int):
        return role
    try:
        return int(role)
    except ValueError:
        pass

    # Look up role by name via Discord API
    roles_result = discord_client.list_roles(bot_id, guild_id)
    if not roles_result:
        raise ValueError(f"Failed to fetch roles for guild {guild_id}")

    for r in roles_result.get("roles", []):
        if r["name"].lower() == role.lower():
            return int(r["id"])

    raise ValueError(f"Role '{role}' not found in guild")


def resolve_user(
    session: DBSession,
    user: int | str,
) -> int:
    """Resolve a user ID from either numeric ID or username.

    Args:
        session: Database session
        user: User ID (snowflake as int or str) or username

    Returns:
        The resolved user ID as int

    Raises:
        ValueError: If username not found
    """
    # Try to parse as snowflake ID first
    if isinstance(user, int):
        return user
    try:
        return int(user)
    except ValueError:
        pass

    # Look up user by username in database
    discord_user = (
        session.query(DiscordUser)
        .filter(
            or_(
                DiscordUser.username == user,
                DiscordUser.display_name == user,
            )
        )
        .first()
    )
    if not discord_user:
        raise ValueError(f"User '{user}' not found")
    return discord_user.id


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def add_user_to_role(
    role: int | str,
    user: int | str,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Add a user to a Discord role.

    Args:
        role: Role ID (snowflake) or role name
        user: User ID (snowflake) or username
        guild: Discord server - can be numeric ID or server name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status, user name, and role name
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)
        resolved_role_id = resolve_role(role, resolved_guild_id, resolved_bot_id)
        resolved_user_id = resolve_user(session, user)

    return await _call_discord_api(
        discord_client.add_role_member,
        resolved_bot_id,
        resolved_guild_id,
        resolved_role_id,
        resolved_user_id,
        error_msg=f"Failed to add user {user} to role {role}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def role_remove(
    role: int | str,
    user: int | str,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Remove a user from a Discord role.

    Args:
        role: Role ID (snowflake) or role name
        user: User ID (snowflake) or username
        guild: Discord server - can be numeric ID or server name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status, user name, and role name
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)
        resolved_role_id = resolve_role(role, resolved_guild_id, resolved_bot_id)
        resolved_user_id = resolve_user(session, user)

    return await _call_discord_api(
        discord_client.remove_role_member,
        resolved_bot_id,
        resolved_guild_id,
        resolved_role_id,
        resolved_user_id,
        error_msg=f"Failed to remove user {user} from role {role}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def create(
    name: str,
    guild: int | str | None = None,
    color: int | None = None,
    mentionable: bool = False,
    hoist: bool = False,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new Discord role in a server.

    Args:
        name: Name for the new role
        guild: Discord server - can be numeric ID or server name
        color: RGB color integer for the role (optional)
        mentionable: Whether the role can be mentioned (default: false)
        hoist: Whether the role should be displayed separately (default: false)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and new role info (id, name, color, position)
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = discord_client.resolve_guild(guild, session)
        if resolved_guild_id is None:
            raise ValueError("Must specify guild")

    return await _call_discord_api(
        discord_client.create_role,
        resolved_bot_id,
        resolved_guild_id,
        name,
        error_msg=f"Failed to create role {name}",
        color=color,
        mentionable=mentionable,
        hoist=hoist,
    )




# =============================================================================
# Channel Permission Tools
# =============================================================================


def resolve_channel_id(
    session: DBSession,
    channel_id: int | str | None,
    channel_name: str | None,
) -> int:
    """
    Resolve a channel ID from either channel_id or channel_name.

    Args:
        session: Database session
        channel_id: Discord channel ID (snowflake) as int or string
        channel_name: Discord channel name to look up

    Returns:
        The resolved channel ID as int

    Raises:
        ValueError: If neither is provided or channel_name not found
    """
    if channel_id is not None:
        return _to_snowflake(channel_id)

    if channel_name is None:
        raise ValueError("Must specify either channel_id or channel_name")

    channel = (
        session.query(DiscordChannel)
        .filter(DiscordChannel.name == channel_name)
        .first()
    )
    if not channel:
        raise ValueError(f"Channel '{channel_name}' not found")
    return channel.id


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def perms(
    channel_id: int | str | None = None,
    channel_name: str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Get permission overwrites for a Discord channel.

    Shows which roles and users have special permissions on the channel.

    Args:
        channel_id: Discord channel ID (snowflake, can be string or int)
        channel_name: Discord channel name (alternative to channel_id)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with channel name and list of permission overwrites
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_channel_id = resolve_channel_id(session, channel_id, channel_name)

    return await _call_discord_api(
        discord_client.get_channel_permissions,
        resolved_bot_id,
        resolved_channel_id,
        error_msg=f"Failed to get permissions for channel {resolved_channel_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def set_perms(
    channel: int | str | None = None,
    role: int | str | None = None,
    user: int | str | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Set permission overwrite for a role or user on a channel.

    Common permission names: view_channel, send_messages, read_message_history,
    manage_messages, manage_channels, add_reactions, attach_files, embed_links

    Args:
        channel: Discord channel ID (snowflake) or channel name
        role: Role ID (snowflake) or role name to set permissions for (mutually exclusive with user)
        user: User ID (snowflake) or username to set permissions for (mutually exclusive with role)
        allow: List of permission names to allow
        deny: List of permission names to deny
        guild: Discord server - can be numeric ID or server name (required for name resolution)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status
    """
    if not role and not user:
        raise ValueError("Must specify either role or user")
    if not channel:
        raise ValueError("Must specify channel")

    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild) if guild else None
        resolved_channel_id = resolve_channel_id(session, channel, None)

        resolved_role_id = None
        resolved_user_id = None
        if role:
            if resolved_guild_id is None:
                # Try to parse as snowflake directly
                resolved_role_id = _to_snowflake(role)
            else:
                resolved_role_id = resolve_role(role, resolved_guild_id, resolved_bot_id)
        if user:
            resolved_user_id = resolve_user(session, user)

    return await _call_discord_api(
        discord_client.set_channel_permission,
        resolved_bot_id,
        resolved_channel_id,
        resolved_role_id,
        resolved_user_id,
        allow,
        deny,
        error_msg=f"Failed to set permissions for channel {resolved_channel_id}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def del_perms(
    channel: int | str,
    role: int | str | None = None,
    user: int | str | None = None,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Remove permission overwrite for a role or user from a channel.

    Args:
        channel: Discord channel ID (snowflake) or channel name
        role: Role ID (snowflake) or role name to remove permissions for (mutually exclusive with user)
        user: User ID (snowflake) or username to remove permissions for (mutually exclusive with role)
        guild: Discord server - can be numeric ID or server name (required for name resolution)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status
    """
    if not role and not user:
        raise ValueError("Must specify either role or user")
    if role and user:
        raise ValueError("Cannot specify both role and user")

    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild) if guild else None
        resolved_channel_id = resolve_channel_id(session, channel, None)

        if role:
            if resolved_guild_id is None:
                resolved_target_id = _to_snowflake(role)
            else:
                resolved_target_id = resolve_role(role, resolved_guild_id, resolved_bot_id)
            target_type = "role"
        else:
            # user is guaranteed non-None here due to earlier validation
            assert user is not None
            resolved_target_id = resolve_user(session, user)
            target_type = "user"

    return await _call_discord_api(
        discord_client.remove_channel_permission,
        resolved_bot_id,
        resolved_channel_id,
        resolved_target_id,
        target_type,
        error_msg=f"Failed to remove permissions for channel {resolved_channel_id}",
    )


# =============================================================================
# Channel/Category Management Tools
# =============================================================================


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def create_channel(
    name: str,
    guild: int | str | None = None,
    category_id: int | str | None = None,
    category_name: str | None = None,
    topic: str | None = None,
    copy_permissions_from: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new text channel in a Discord server.

    Args:
        name: Channel name
        guild: Discord server - can be numeric ID or server name
        category_id: Optional category to create channel in (snowflake, can be string or int)
        category_name: Optional category name (alternative to category_id)
        topic: Optional channel topic/description
        copy_permissions_from: Optional channel ID to copy permissions from (snowflake, can be string or int)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and new channel info
    """
    resolved_bot_id = resolve_bot_id(bot_id)
    resolved_category_id = _to_snowflake(category_id) if category_id else None
    resolved_copy_from = _to_snowflake(copy_permissions_from) if copy_permissions_from else None

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)

    return await _call_discord_api(
        discord_client.create_channel,
        resolved_bot_id,
        resolved_guild_id,
        name,
        resolved_category_id,
        category_name,
        topic,
        resolved_copy_from,
        error_msg=f"Failed to create channel {name}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def create_category(
    name: str,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new category in a Discord server.

    Args:
        name: Category name
        guild: Discord server - can be numeric ID or server name
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and new category info
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)

    return await _call_discord_api(
        discord_client.create_category,
        resolved_bot_id,
        resolved_guild_id,
        name,
        error_msg=f"Failed to create category {name}",
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def delete_channel(
    channel_id: int | str | None = None,
    channel_name: str | None = None,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Delete a Discord channel or category.

    Args:
        channel_id: Discord channel/category ID (snowflake, can be string or int)
        channel_name: Discord channel/category name (alternative to channel_id)
        guild: Discord server - can be numeric ID or server name (required when using channel_name)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and deleted channel/category info
    """
    if channel_id is None and channel_name is None:
        raise ValueError("Must specify either channel_id or channel_name")

    if channel_name is not None and guild is None:
        raise ValueError("guild is required when using channel_name")

    resolved_bot_id = resolve_bot_id(bot_id)
    resolved_channel_id = _to_snowflake(channel_id) if channel_id is not None else None

    # Resolve guild_id if channel_name is used
    resolved_guild_id = None
    if channel_name is not None:
        with make_session() as session:
            resolved_guild_id = resolve_guild_id(session, guild)

    identifier = channel_id or channel_name
    return await _call_discord_api(
        discord_client.delete_channel,
        resolved_bot_id,
        error_msg=f"Failed to delete channel {identifier}",
        channel_id=resolved_channel_id,
        channel_name=channel_name if resolved_channel_id is None else None,
        guild_id=resolved_guild_id,
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def delete_category(
    category_id: int | str | None = None,
    category_name: str | None = None,
    guild: int | str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Delete a Discord category.

    Args:
        category_id: Discord category ID (snowflake, can be string or int)
        category_name: Discord category name (alternative to category_id)
        guild: Discord server - can be numeric ID or server name (required when using category_name)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and deleted category info
    """
    # Categories are just a type of channel in Discord, so reuse delete_channel
    return await delete_channel(
        channel_id=category_id,
        channel_name=category_name,
        guild=guild,
        bot_id=bot_id,
    )


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def edit_channel(
    channel_id: int | str | None = None,
    channel_name: str | None = None,
    guild: int | str | None = None,
    new_name: str | None = None,
    new_topic: str | None = None,
    category_id: int | str | None = None,
    category_name: str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Edit a Discord channel's properties (name, topic, or category).

    Use this to rename channels, update topics, or move channels between categories.

    Args:
        channel_id: Discord channel ID (snowflake, can be string or int)
        channel_name: Discord channel name (alternative to channel_id)
        guild: Discord server - can be numeric ID or server name (required when using channel_name or category_name)
        new_name: New name for the channel
        new_topic: New topic for the channel (empty string to clear)
        category_id: Move to this category ID (empty string or 0 to remove from category)
        category_name: Move to this category name (empty string to remove from category)
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status and updated channel info
    """
    if channel_id is None and channel_name is None:
        raise ValueError("Must specify either channel_id or channel_name")

    resolved_bot_id = resolve_bot_id(bot_id)
    resolved_channel_id = _to_snowflake(channel_id) if channel_id else None
    resolved_category_id = _to_snowflake(category_id) if category_id and category_id != "" else category_id

    # Resolve guild_id if channel_name is used
    resolved_guild_id = None
    if channel_name is not None or category_name is not None:
        with make_session() as session:
            resolved_guild_id = resolve_guild_id(session, guild)

    return await _call_discord_api(
        discord_client.edit_channel,
        resolved_bot_id,
        error_msg=f"Failed to edit channel {channel_id or channel_name}",
        channel_id=resolved_channel_id,
        channel_name=channel_name if resolved_channel_id is None else None,
        guild_id=resolved_guild_id,
        new_name=new_name,
        new_topic=new_topic,
        category_id=resolved_category_id,
        category_name=category_name,
    )
