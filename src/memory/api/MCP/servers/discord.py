"""MCP subserver for Discord messaging."""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from fastmcp.server.dependencies import get_access_token

from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import discord as discord_client
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordUser,
    Team,
    UserSession,
)
from sqlalchemy.orm import selectinload
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
            user_session = local_session.get(UserSession, token)
            if not user_session or not user_session.user:
                return False
            # Check if user has any authorized Discord bots
            return len(user_session.user.discord_bots) > 0

    return await asyncio.to_thread(_check)


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
        return await asyncio.to_thread(fetch_channels, session, server, include_dms)


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
async def role_add_user(
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
async def role_remove_user(
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
async def create_role(
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
                resolved_role_id = resolve_role(
                    role, resolved_guild_id, resolved_bot_id
                )
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
                resolved_target_id = resolve_role(
                    role, resolved_guild_id, resolved_bot_id
                )
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
# Team-Discord Role Helpers
# =============================================================================


def ensure_team_has_discord_role(
    session: DBSession,
    team: Team,
    guild_id: int,
    bot_id: int,
) -> tuple[int, bool]:
    """Ensure a team has a Discord role, creating one if needed.

    Args:
        session: Database session
        team: Team model instance
        guild_id: Discord guild ID
        bot_id: Discord bot ID

    Returns:
        Tuple of (role_id, was_created)

    Raises:
        ValueError: If role creation fails
    """
    # If team already has a role, validate guild ID matches
    if team.discord_role_id:
        if team.discord_guild_id and team.discord_guild_id != guild_id:
            raise ValueError(
                f"Team '{team.slug}' is linked to a different Discord guild "
                f"(existing: {team.discord_guild_id}, requested: {guild_id})"
            )
        return team.discord_role_id, False

    # Create a new role for this team
    role_id, created = discord_client.resolve_role(
        team.slug,  # Use team slug as role name
        guild_id,
        bot_id,
        create_if_missing=True,
    )
    if role_id is None:
        raise ValueError(f"Failed to create Discord role for team '{team.slug}'")

    # Update team with the new role
    team.discord_role_id = role_id
    team.discord_guild_id = guild_id
    team.auto_sync_discord = True
    session.flush()

    return role_id, created


def resolve_team_or_role(
    session: DBSession,
    identifier: int | str,
    guild_id: int,
    bot_id: int,
) -> tuple[Team | None, int, bool, bool]:
    """Resolve an identifier to a team and Discord role.

    The identifier can be:
    - An internal team slug/ID (will ensure it has a Discord role)
    - A Discord role name/ID (will create an internal team for it)

    Args:
        session: Database session
        identifier: Team slug/ID or Discord role name/ID
        guild_id: Discord guild ID
        bot_id: Discord bot ID

    Returns:
        Tuple of (team_or_none, role_id, team_was_created, role_was_created)
    """
    team_created = False

    # Try to find as internal team first
    team = None
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        team = session.query(Team).filter(Team.id == int(identifier)).first()
    else:
        team = session.query(Team).filter(Team.slug == identifier).first()

    if team:
        # Found internal team - ensure it has a Discord role
        role_id, role_created = ensure_team_has_discord_role(session, team, guild_id, bot_id)
        return team, role_id, False, role_created

    # Not an internal team - try as Discord role
    try:
        role_id, role_created = discord_client.resolve_role(
            identifier,
            guild_id,
            bot_id,
            create_if_missing=True,
        )
    except ValueError:
        raise ValueError(f"Could not resolve '{identifier}' as team or Discord role")

    if role_id is None:
        raise ValueError(f"Failed to resolve or create role for '{identifier}'")

    # Create an internal team for this Discord role
    slug = identifier if isinstance(identifier, str) else f"discord-role-{identifier}"
    # Normalize slug
    slug = re.sub(r"\s+", "-", slug.lower().strip())
    slug = re.sub(r"[^a-z0-9-]", "", slug)

    # Handle empty slug (e.g., non-ASCII role names)
    if not slug:
        slug = f"discord-role-{role_id}"

    # Ensure slug uniqueness with retry for concurrent inserts
    base_slug = slug
    max_retries = 3
    for attempt in range(max_retries):
        suffix = 1
        while session.query(Team).filter(Team.slug == slug).first() is not None:
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        team = Team(
            name=identifier if isinstance(identifier, str) else f"Discord Role {identifier}",
            slug=slug,
            discord_role_id=role_id,
            discord_guild_id=guild_id,
            auto_sync_discord=True,
        )
        # Use savepoint to avoid rolling back the entire transaction
        savepoint = session.begin_nested()
        try:
            session.add(team)
            session.flush()
            team_created = True
            break  # Success
        except IntegrityError:
            savepoint.rollback()
            team = None  # Clear detached object reference
            if attempt == max_retries - 1:
                raise ValueError(f"Failed to create unique slug for team after {max_retries} attempts")
            # Reset slug for retry
            slug = base_slug
            continue

    return team, role_id, team_created, role_created


# =============================================================================
# Channel/Category Management Tools
# =============================================================================


async def attach_teams_to_channel(
    session: DBSession,
    channel_id: int,
    teams: list[int | str],
    guild_id: int,
    bot_id: int,
) -> dict[str, Any]:
    """Attach teams to a channel by making it private and granting team roles access.

    Args:
        session: Database session
        channel_id: Discord channel ID (can be a category or text channel)
        teams: List of team slugs/IDs or Discord role names/IDs
        guild_id: Discord guild ID
        bot_id: Discord bot ID

    Returns:
        Dict with teams_synced, teams_created, roles_created, and warnings lists
    """
    result: dict[str, Any] = {
        "teams_synced": [],
        "teams_created": [],
        "roles_created": [],
        "warnings": [],
    }

    # Make channel private first (deny @everyone)
    private_result = await asyncio.to_thread(
        discord_client.make_channel_private,
        bot_id,
        guild_id,
        channel_id,
    )
    if not private_result:
        result["warnings"].append("Failed to make channel private")

    # Grant access to each team
    for team_identifier in teams:
        try:
            team, role_id, team_created, role_created = resolve_team_or_role(
                session, team_identifier, guild_id, bot_id
            )

            if team_created:
                result["teams_created"].append(team.slug if team else str(team_identifier))
            if role_created:
                result["roles_created"].append(str(role_id))

            # Grant role access to channel
            access_result = await asyncio.to_thread(
                discord_client.grant_role_channel_access,
                bot_id,
                channel_id,
                role_id,
            )
            if access_result:
                result["teams_synced"].append(team.slug if team else str(team_identifier))
            else:
                result["warnings"].append(f"Failed to grant access for {team_identifier}")

        except ValueError as e:
            # Expected errors (validation, not found, etc.)
            result["warnings"].append(f"Error processing team {team_identifier}: {e}")
        except Exception as e:
            # Unexpected errors - log at error level to distinguish from expected failures
            logger.error(f"Unexpected error processing team {team_identifier}: {e}", exc_info=True)
            result["warnings"].append(f"Unexpected error processing team {team_identifier}: {e}")

    return result


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def upsert_channel(
    name: str,
    guild: int | str | None = None,
    category: int | str | None = None,
    topic: str | None = None,
    teams: list[int | str] | None = None,
    project_id: int | None = None,
    sensitivity: str | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create or update a Discord text channel.

    If a channel with the given name exists, updates it. Otherwise creates it.

    If teams are specified, the channel is made private and those teams get access.
    Teams can be internal team slugs/IDs or Discord role names/IDs:
    - Internal teams will have Discord roles created if needed
    - Discord roles not linked to teams will have teams created for them

    Args:
        name: Channel name
        guild: Discord server - can be numeric ID or server name
        category: Category to place channel in - can be ID or name
        topic: Optional channel topic/description
        teams: List of team slugs/IDs or Discord role names/IDs for access control.
               If provided, channel becomes private with only these teams having access.
        project_id: Optional project ID to link the channel to for access control.
        sensitivity: Optional sensitivity level ('public', 'basic', 'internal', 'confidential').
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status, channel info, and sync details
    """
    result: dict[str, Any] = {
        "success": False,
        "channel": None,
        "teams_synced": [],
        "teams_created": [],
        "roles_created": [],
        "warnings": [],
    }

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)
        needs_discord_api = category is not None or topic is not None or teams is not None

        # Local-only update: just set project_id/sensitivity on existing channel
        if not needs_discord_api:
            channel_record = (
                session.query(DiscordChannel)
                .filter(DiscordChannel.server_id == resolved_guild_id, DiscordChannel.name == name)
                .first()
            )
            if not channel_record:
                result["error"] = f"Channel '{name}' not found in server {resolved_guild_id}"
                return result

            if project_id is not None:
                channel_record.project_id = project_id
                result["project_id"] = project_id
            if sensitivity is not None:
                channel_record.sensitivity = sensitivity
                result["sensitivity"] = sensitivity

            result["channel"] = {"id": str(channel_record.id), "name": name}
            result["action"] = "updated_local"
            session.commit()
            result["success"] = True
            return result

        # Discord API upsert for category/topic/teams changes
        resolved_bot_id = resolve_bot_id(bot_id)

        resolved_category_id = None
        if category is not None:
            resolved_category_id = discord_client.resolve_category(
                category, resolved_guild_id, resolved_bot_id
            )

        channel_result = await asyncio.to_thread(
            discord_client.upsert_channel,
            resolved_bot_id,
            resolved_guild_id,
            name,
            category_id=resolved_category_id,
            topic=topic,
        )

        if not channel_result or not channel_result.get("success"):
            result["error"] = channel_result.get("error") if channel_result else "Unknown error"
            return result

        result["channel"] = channel_result.get("channel")
        result["action"] = channel_result.get("action")
        channel_id = int(channel_result["channel"]["id"])

        # Handle teams/permissions
        if teams:
            teams_result = await attach_teams_to_channel(
                session, channel_id, teams, resolved_guild_id, resolved_bot_id
            )
            result["teams_synced"] = teams_result["teams_synced"]
            result["teams_created"] = teams_result["teams_created"]
            result["roles_created"] = teams_result["roles_created"]
            result["warnings"] = teams_result["warnings"]

        # Set project_id/sensitivity after Discord API creates/updates the channel
        if project_id is not None or sensitivity is not None:
            channel_record = session.get(DiscordChannel, channel_id)
            if channel_record:
                if project_id is not None:
                    channel_record.project_id = project_id
                    result["project_id"] = project_id
                if sensitivity is not None:
                    channel_record.sensitivity = sensitivity
                    result["sensitivity"] = sensitivity

        session.commit()
        result["success"] = True

    return result


@discord_mcp.tool()
@visible_when(require_scopes("discord-admin"), has_discord_bots)
async def upsert_category(
    name: str,
    guild: int | str | None = None,
    teams: list[int | str] | None = None,
    bot_id: int | None = None,
) -> dict[str, Any]:
    """
    Create or find a Discord category.

    If a category with the given name exists, returns it. Otherwise creates it.

    If teams are specified, the category is made private and those teams get access.
    Teams can be internal team slugs/IDs or Discord role names/IDs:
    - Internal teams will have Discord roles created if needed
    - Discord roles not linked to teams will have teams created for them

    Args:
        name: Category name
        guild: Discord server - can be numeric ID or server name
        teams: List of team slugs/IDs or Discord role names/IDs for access control.
               If provided, category becomes private with only these teams having access.
        bot_id: Optional specific bot ID to use (defaults to user's first bot)

    Returns:
        Dict with success status, category info, and sync details
    """
    resolved_bot_id = resolve_bot_id(bot_id)

    result: dict[str, Any] = {
        "success": False,
        "category": None,
        "teams_synced": [],
        "teams_created": [],
        "roles_created": [],
        "warnings": [],
    }

    with make_session() as session:
        resolved_guild_id = resolve_guild_id(session, guild)

        # Create or find the category
        category_result = await asyncio.to_thread(
            discord_client.upsert_category,
            resolved_bot_id,
            resolved_guild_id,
            name,
        )

        if not category_result or not category_result.get("success"):
            result["error"] = category_result.get("error") if category_result else "Unknown error"
            return result

        result["category"] = category_result.get("category")
        result["action"] = category_result.get("action")
        category_id = int(category_result["category"]["id"])

        # Handle teams/permissions
        if teams:
            teams_result = await attach_teams_to_channel(
                session, category_id, teams, resolved_guild_id, resolved_bot_id
            )
            result["teams_synced"] = teams_result["teams_synced"]
            result["teams_created"] = teams_result["teams_created"]
            result["roles_created"] = teams_result["roles_created"]
            result["warnings"] = teams_result["warnings"]

        session.commit()
        result["success"] = True

    return result


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
    # Categories are just a type of channel in Discord
    if category_id is None and category_name is None:
        raise ValueError("Must specify either category_id or category_name")

    if category_name is not None and guild is None:
        raise ValueError("guild is required when using category_name")

    resolved_bot_id = resolve_bot_id(bot_id)
    resolved_category_id = _to_snowflake(category_id) if category_id is not None else None

    resolved_guild_id = None
    if category_name is not None:
        with make_session() as session:
            resolved_guild_id = resolve_guild_id(session, guild)

    identifier = category_id or category_name
    return await _call_discord_api(
        discord_client.delete_channel,
        resolved_bot_id,
        error_msg=f"Failed to delete category {identifier}",
        channel_id=resolved_category_id,
        channel_name=category_name if resolved_category_id is None else None,
        guild_id=resolved_guild_id,
    )


