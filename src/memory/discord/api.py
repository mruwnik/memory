"""
Discord collector API server.

Provides HTTP endpoints for sending messages via the Discord collector bots.
This runs alongside the collector and exposes its functionality via REST API.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, cast

import discord
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from memory.common.celery_app import app as celery_app
from memory.discord.collector import CollectorManager

logger = logging.getLogger(__name__)

# Global collector manager
manager: CollectorManager | None = None


class SendDMRequest(BaseModel):
    bot_id: int
    user: str | int  # User ID or username
    message: str


class SendChannelRequest(BaseModel):
    bot_id: int
    channel: str | int  # Channel ID or name
    message: str


class TypingRequest(BaseModel):
    bot_id: int
    user: str | int | None = None
    channel: str | int | None = None


class ReactionRequest(BaseModel):
    bot_id: int
    channel: str | int
    message_id: int
    emoji: str


class RoleMemberRequest(BaseModel):
    bot_id: int
    guild_id: int | str  # Accepts ID (int) or name (str)
    role_id: int | str  # Accepts ID as int or string
    user_id: int | str  # Accepts ID as int or string


class ChannelPermissionRequest(BaseModel):
    bot_id: int
    channel_id: int | str  # Accepts ID (int) or string
    role_id: int | str | None = None  # Accepts ID (int) or string
    user_id: int | str | None = None  # Accepts ID (int) or string
    allow: list[str] | None = None  # Permission names to allow
    deny: list[str] | None = None  # Permission names to deny


class CreateChannelRequest(BaseModel):
    bot_id: int
    guild_id: int | str  # Accepts ID (int) or name (str)
    name: str
    category_id: int | str | None = None  # Accepts ID (int or string)
    category_name: str | None = None  # Alternative: category name
    topic: str | None = None
    copy_permissions_from: int | str | None = None  # Channel ID to copy permissions from


class CreateCategoryRequest(BaseModel):
    bot_id: int
    guild_id: int | str  # Accepts ID (int) or name (str)
    name: str


class DeleteChannelRequest(BaseModel):
    bot_id: int
    channel_id: int | None = None
    channel_name: str | None = None
    guild_id: int | str | None = None  # Accepts ID (int) or name (str), required if using channel_name


class EditChannelRequest(BaseModel):
    bot_id: int
    channel_id: int | str | None = None  # Accepts ID (int or string)
    channel_name: str | None = None  # Alternative: channel name for lookup
    guild_id: int | str | None = None  # Required when using channel_name
    new_name: str | None = None  # New channel name
    new_topic: str | None = None  # New topic (use empty string to clear)
    category_id: int | str | None = None  # Move to category by ID
    category_name: str | None = None  # Move to category by name (use empty string to remove from category)


class CreateRoleRequest(BaseModel):
    bot_id: int
    name: str
    color: int | None = None  # RGB color integer
    permissions: int | None = None  # Permission bitfield
    mentionable: bool = False
    hoist: bool = False  # Whether the role should be displayed separately in member list


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage collector lifecycle."""
    global manager

    manager = CollectorManager(celery_app)

    # Start all bots
    await manager.start_all()
    logger.info("Discord collector API started")

    yield

    # Stop all bots
    await manager.stop_all()
    logger.info("Discord collector API stopped")


app = FastAPI(title="Discord Collector API", lifespan=lifespan)


def get_manager() -> CollectorManager:
    """Get the collector manager."""
    if manager is None:
        raise HTTPException(status_code=503, detail="Collector not initialized")
    return manager


# =============================================================================
# Lookup Helpers
# =============================================================================


def get_collector_or_404(mgr: CollectorManager, bot_id: int):
    """Get collector for bot_id or raise 404."""
    collector = mgr.get_collector(bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    return collector


def get_guild_or_404(collector, guild_id: int | str) -> discord.Guild:
    """Get guild by ID (int) or name (str), or raise 404.

    Note: Numeric strings are always treated as IDs, never names.
    Name matching prefers exact case match, falls back to case-insensitive.
    """
    # If it's a string that looks like an int, convert it
    if isinstance(guild_id, str):
        try:
            guild_id_int = int(guild_id)
            guild = collector.get_guild(guild_id_int)
        except ValueError:
            # It's a name, search by name (exact match first, then case-insensitive)
            guild = None
            case_insensitive_match = None
            for g in collector.client.guilds:
                if g.name == guild_id:
                    guild = g
                    break
                if case_insensitive_match is None and g.name.lower() == guild_id.lower():
                    case_insensitive_match = g
            else:
                guild = case_insensitive_match
    else:
        guild = collector.get_guild(guild_id)

    if not guild:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_id}' not found")
    return guild


async def get_channel_or_404(collector, channel_id: int) -> discord.abc.GuildChannel:
    """Get channel (with fetch fallback) or raise 404."""
    channel = collector.get_channel(channel_id)
    if not channel:
        try:
            channel = await collector.fetch_channel(channel_id)
        except discord.NotFound:
            raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")
    if not isinstance(channel, discord.abc.GuildChannel):
        raise HTTPException(status_code=400, detail="Channel is not a guild channel")
    return channel


def get_role_or_404(guild: discord.Guild, role_id: int) -> discord.Role:
    """Get role or raise 404."""
    role = guild.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role {role_id} not found")
    return role


def get_member_or_404(guild: discord.Guild, user_id: int) -> discord.Member:
    """Get member or raise 404."""
    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found in guild")
    return member


@asynccontextmanager
async def discord_api_errors(action: str = "perform action"):
    """Context manager for Discord API error handling."""
    try:
        yield
    except discord.Forbidden:
        raise HTTPException(status_code=403, detail=f"Bot lacks permission to {action}")
    except discord.HTTPException as e:
        raise HTTPException(status_code=500, detail=str(e))


async def resolve_channel_id(channel: str | int) -> int:
    """Resolve a channel name or ID to an ID."""
    if isinstance(channel, int):
        return channel

    # Try to parse as int first
    try:
        return int(channel)
    except ValueError:
        pass

    # Look up by name in database
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordChannel

    with make_session() as session:
        ch = session.query(DiscordChannel).filter_by(name=channel).first()
        if ch:
            return ch.id

    raise HTTPException(status_code=404, detail=f"Channel '{channel}' not found")


async def resolve_user_id(user: str | int, mgr: CollectorManager | None = None) -> int:
    """Resolve a username or ID to an ID.

    Tries in order:
    1. Parse as integer ID
    2. Look up in local database
    3. Search Discord guild members (if manager provided)
    """
    if isinstance(user, int):
        return user

    # Try to parse as int first
    try:
        return int(user)
    except ValueError:
        pass

    # Look up by username or display_name in database
    from sqlalchemy import or_

    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordUser

    with make_session() as session:
        u = (
            session.query(DiscordUser)
            .filter(or_(DiscordUser.username == user, DiscordUser.display_name == user))
            .first()
        )
        if u:
            return u.id

    # Fall back to Discord API - search guild members
    if mgr:
        for collector in mgr.collectors.values():
            for guild in collector.guilds:
                # Search by username (case-insensitive)
                member = guild.get_member_named(user)
                if member:
                    return member.id

    raise HTTPException(status_code=404, detail=f"User '{user}' not found")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Check health of all bots."""
    mgr = get_manager()
    result = {}
    for bot_id, collector in mgr.collectors.items():
        result[str(bot_id)] = {
            "connected": collector.is_ready(),
            "name": collector.bot_info.name,
        }
    return result


@app.post("/send_dm")
async def send_dm(request: SendDMRequest) -> dict[str, Any]:
    """Send a DM to a user."""
    mgr = get_manager()
    user_id = await resolve_user_id(request.user, mgr)
    success = await mgr.send_dm(request.bot_id, user_id, request.message)
    return {"success": success}


@app.post("/send_channel")
async def send_channel(request: SendChannelRequest) -> dict[str, Any]:
    """Send a message to a channel."""
    mgr = get_manager()
    channel_id = await resolve_channel_id(request.channel)
    success = await mgr.send_message(request.bot_id, channel_id, request.message)
    return {"success": success}


@app.post("/typing/dm")
async def typing_dm(request: TypingRequest) -> dict[str, Any]:
    """Trigger typing indicator in a DM."""
    mgr = get_manager()
    if request.user is None:
        raise HTTPException(status_code=400, detail="user is required")

    user_id = await resolve_user_id(request.user, mgr)
    collector = mgr.get_collector(request.bot_id)
    if not collector:
        return {"success": False}

    try:
        user = collector.get_user(user_id)
        if user is None:
            user = await collector.fetch_user(user_id)
        if user:
            dm_channel = user.dm_channel or await user.create_dm()
            await dm_channel.typing()
            return {"success": True}
    except Exception:
        logger.exception(f"Failed to trigger typing for user {user_id}")

    return {"success": False}


@app.post("/typing/channel")
async def typing_channel(request: TypingRequest) -> dict[str, Any]:
    """Trigger typing indicator in a channel."""
    mgr = get_manager()
    if request.channel is None:
        raise HTTPException(status_code=400, detail="channel is required")

    channel_id = await resolve_channel_id(request.channel)
    collector = mgr.get_collector(request.bot_id)
    if not collector:
        return {"success": False}

    try:
        channel = collector.get_channel(channel_id)
        if channel is None:
            channel = await collector.fetch_channel(channel_id)
        if channel and hasattr(channel, "typing"):
            await channel.typing()  # type: ignore[union-attr]
            return {"success": True}
    except Exception:
        logger.exception(f"Failed to trigger typing for channel {channel_id}")

    return {"success": False}


@app.post("/add_reaction")
async def add_reaction(request: ReactionRequest) -> dict[str, Any]:
    """Add a reaction to a message."""
    mgr = get_manager()
    channel_id = await resolve_channel_id(request.channel)
    collector = mgr.get_collector(request.bot_id)
    if not collector:
        return {"success": False}

    success = await collector.add_reaction(channel_id, request.message_id, request.emoji)
    return {"success": success}


@app.post("/refresh_metadata")
async def refresh_metadata() -> dict[str, Any]:
    """Refresh Discord metadata from API."""
    mgr = get_manager()

    from memory.common.db.connection import make_session
    from memory.discord.collector import ensure_channel, ensure_server, ensure_user

    updated = {"servers": 0, "channels": 0, "users": 0}

    for collector in mgr.collectors.values():
        with make_session() as session:
            # Update servers
            for guild in collector.guilds:
                ensure_server(session, guild)
                updated["servers"] += 1

                # Update channels
                for channel in guild.channels:
                    if hasattr(channel, "send"):  # Text-like channels
                        ensure_channel(session, cast(discord.abc.Messageable, channel), guild.id)
                        updated["channels"] += 1

                # Update users
                for member in guild.members:
                    ensure_user(session, member)
                    updated["users"] += 1

            session.commit()

    return {"success": True, "updated": updated}


# =============================================================================
# Role Management Endpoints
# =============================================================================


@app.get("/guilds/{guild_id}/roles")
async def list_roles(guild_id: str, bot_id: int) -> dict[str, Any]:
    """List all roles in a guild (guild_id can be ID or name)."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, bot_id)
    guild = get_guild_or_404(collector, guild_id)

    roles = [
        {
            "id": str(role.id),  # String to preserve precision
            "name": role.name,
            "color": role.color.value,
            "position": role.position,
            "mentionable": role.mentionable,
            "member_count": len(role.members),
            "managed": role.managed,  # True if managed by integration (can't be assigned)
        }
        for role in guild.roles
        if not role.is_default()  # Skip @everyone
    ]
    return {"roles": sorted(roles, key=lambda r: -r["position"])}


@app.get("/guilds/{guild_id}/roles/{role_id}/members")
async def list_role_members(guild_id: str, role_id: str, bot_id: int) -> dict[str, Any]:
    """List all members with a specific role (guild_id can be ID or name)."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, bot_id)
    guild = get_guild_or_404(collector, guild_id)
    role = get_role_or_404(guild, int(role_id))

    members = [
        {
            "id": member.id,
            "username": member.name,
            "display_name": member.display_name,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        }
        for member in role.members
    ]
    return {"role": role.name, "members": members}


@app.post("/roles/add_member")
async def add_role_member(request: RoleMemberRequest) -> dict[str, Any]:
    """Add a user to a role."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)
    guild = get_guild_or_404(collector, request.guild_id)
    role_id = int(request.role_id) if isinstance(request.role_id, str) else request.role_id
    user_id = int(request.user_id) if isinstance(request.user_id, str) else request.user_id
    role = get_role_or_404(guild, role_id)
    member = get_member_or_404(guild, user_id)

    async with discord_api_errors("manage this role"):
        await member.add_roles(role, reason="Added via MCP")
        return {"success": True, "user": member.display_name, "role": role.name}


@app.post("/roles/remove_member")
async def remove_role_member(request: RoleMemberRequest) -> dict[str, Any]:
    """Remove a user from a role."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)
    guild = get_guild_or_404(collector, request.guild_id)
    role_id = int(request.role_id) if isinstance(request.role_id, str) else request.role_id
    user_id = int(request.user_id) if isinstance(request.user_id, str) else request.user_id
    role = get_role_or_404(guild, role_id)
    member = get_member_or_404(guild, user_id)

    async with discord_api_errors("manage this role"):
        await member.remove_roles(role, reason="Removed via MCP")
        return {"success": True, "user": member.display_name, "role": role.name}


@app.post("/guilds/{guild_id}/roles")
async def create_role(guild_id: str, request: CreateRoleRequest) -> dict[str, Any]:
    """Create a new role in a guild."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)
    guild = get_guild_or_404(collector, guild_id)

    # Build role kwargs
    kwargs: dict[str, Any] = {
        "name": request.name,
        "mentionable": request.mentionable,
        "hoist": request.hoist,
        "reason": "Created via MCP",
    }
    if request.color is not None:
        kwargs["color"] = discord.Color(request.color)
    if request.permissions is not None:
        kwargs["permissions"] = discord.Permissions(request.permissions)

    async with discord_api_errors("create roles"):
        role = await guild.create_role(**kwargs)
        return {
            "success": True,
            "role": {
                "id": str(role.id),
                "name": role.name,
                "color": role.color.value,
                "position": role.position,
                "mentionable": role.mentionable,
                "hoist": role.hoist,
            },
        }


# =============================================================================
# Channel Permission Endpoints
# =============================================================================


@app.get("/channels/{channel_id}/permissions")
async def get_channel_permissions(channel_id: int, bot_id: int) -> dict[str, Any]:
    """Get permission overwrites for a channel."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, bot_id)
    channel = await get_channel_or_404(collector, channel_id)

    overwrites = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        overwrites.append({
            "target_type": "role" if isinstance(target, discord.Role) else "member",
            "target_id": target.id,
            "target_name": target.name if isinstance(target, discord.Role) else target.display_name,
            "allow": [perm for perm, value in allow if value],
            "deny": [perm for perm, value in deny if value],
        })

    return {
        "channel": channel.name,
        "overwrites": overwrites,
    }


@app.post("/channels/set_permission")
async def set_channel_permission(request: ChannelPermissionRequest) -> dict[str, Any]:
    """Set permission overwrite for a role or user on a channel."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)

    # Convert string IDs to int
    channel_id = int(request.channel_id) if isinstance(request.channel_id, str) else request.channel_id
    role_id = int(request.role_id) if isinstance(request.role_id, str) else request.role_id
    user_id = int(request.user_id) if isinstance(request.user_id, str) else request.user_id

    channel = await get_channel_or_404(collector, channel_id)

    # Determine target (role or user)
    target: discord.Role | discord.Member
    if role_id:
        target = get_role_or_404(channel.guild, role_id)
    elif user_id:
        target = get_member_or_404(channel.guild, user_id)
    else:
        raise HTTPException(status_code=400, detail="Must specify role_id or user_id")

    # Build permission overwrite
    overwrite = discord.PermissionOverwrite()
    if request.allow:
        for perm in request.allow:
            if hasattr(overwrite, perm):
                setattr(overwrite, perm, True)
    if request.deny:
        for perm in request.deny:
            if hasattr(overwrite, perm):
                setattr(overwrite, perm, False)

    async with discord_api_errors("manage channel permissions"):
        await channel.set_permissions(target, overwrite=overwrite, reason="Set via MCP")
        return {
            "success": True,
            "channel": channel.name,
            "target": target.name if isinstance(target, discord.Role) else target.display_name,
        }


@app.delete("/channels/{channel_id}/permissions/{target_id}")
async def remove_channel_permission(
    channel_id: int, target_id: int, bot_id: int, target_type: str = "role"
) -> dict[str, Any]:
    """Remove permission overwrite for a role or user from a channel."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, bot_id)
    channel = await get_channel_or_404(collector, channel_id)

    # Determine target
    target: discord.Role | discord.Member
    if target_type == "role":
        target = get_role_or_404(channel.guild, target_id)
    else:
        target = get_member_or_404(channel.guild, target_id)

    async with discord_api_errors("manage channel permissions"):
        await channel.set_permissions(target, overwrite=None, reason="Removed via MCP")
        return {"success": True, "channel": channel.name}


# =============================================================================
# Channel/Category Creation Endpoints
# =============================================================================


@app.get("/guilds/{guild_id}/categories")
async def list_categories(guild_id: str, bot_id: int) -> dict[str, Any]:
    """List all categories in a guild (guild_id can be ID or name)."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, bot_id)
    guild = get_guild_or_404(collector, guild_id)

    categories = [
        {
            "id": str(cat.id),  # String to preserve precision
            "name": cat.name,
            "position": cat.position,
            "channel_count": len(cat.channels),
            "channels": [{"id": str(ch.id), "name": ch.name} for ch in cat.channels],
        }
        for cat in guild.categories
    ]
    return {"categories": sorted(categories, key=lambda c: c["position"])}


@app.post("/channels/create")
async def create_channel(request: CreateChannelRequest) -> dict[str, Any]:
    """Create a new text channel."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)
    guild = get_guild_or_404(collector, request.guild_id)

    # Get category if specified (by ID or name)
    category = None
    if request.category_id is not None:
        cat_id = int(request.category_id) if isinstance(request.category_id, str) else request.category_id
        category = guild.get_channel(cat_id)
        if not isinstance(category, discord.CategoryChannel):
            raise HTTPException(status_code=400, detail=f"Channel {request.category_id} is not a category")
    elif request.category_name is not None:
        for cat in guild.categories:
            if cat.name.lower() == request.category_name.lower():
                category = cat
                break
        if category is None:
            raise HTTPException(status_code=404, detail=f"Category '{request.category_name}' not found")

    # Get permissions to copy if specified
    overwrites = None
    if request.copy_permissions_from:
        source_channel = guild.get_channel(request.copy_permissions_from)
        if source_channel and isinstance(source_channel, discord.abc.GuildChannel):
            overwrites = dict(source_channel.overwrites)

    async with discord_api_errors("create channels"):
        kwargs: dict[str, Any] = {
            "name": request.name,
            "category": category,
            "topic": request.topic,
            "reason": "Created via MCP",
        }
        if overwrites is not None:
            kwargs["overwrites"] = overwrites
        channel = await guild.create_text_channel(**kwargs)
        return {
            "success": True,
            "channel": {
                "id": channel.id,
                "name": channel.name,
                "category": category.name if category else None,
            },
        }


@app.post("/categories/create")
async def create_category(request: CreateCategoryRequest) -> dict[str, Any]:
    """Create a new category."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)
    guild = get_guild_or_404(collector, request.guild_id)

    async with discord_api_errors("create categories"):
        category = await guild.create_category(
            name=request.name,
            reason="Created via MCP",
        )
        return {
            "success": True,
            "category": {
                "id": category.id,
                "name": category.name,
            },
        }


@app.post("/channels/delete")
async def delete_channel(request: DeleteChannelRequest) -> dict[str, Any]:
    """Delete a channel or category by ID or name."""
    if request.channel_id is None and request.channel_name is None:
        raise HTTPException(status_code=400, detail="Must specify either channel_id or channel_name")

    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)

    channel = None

    if request.channel_id is not None:
        # Find by ID across all guilds
        for guild in collector.client.guilds:
            channel = guild.get_channel(request.channel_id)
            if channel:
                break
    else:
        # Find by name - requires guild_id to avoid ambiguity
        if request.guild_id is None:
            raise HTTPException(
                status_code=400,
                detail="guild_id is required when using channel_name"
            )
        guild = get_guild_or_404(collector, request.guild_id)

        # Search all channels (including categories) by name
        # Prefer exact case match, fall back to case-insensitive
        case_insensitive_match = None
        for ch in guild.channels:
            if ch.name == request.channel_name:
                channel = ch
                break
            if case_insensitive_match is None and ch.name.lower() == request.channel_name.lower():
                case_insensitive_match = ch
        else:
            # No exact match found, use case-insensitive match if available
            channel = case_insensitive_match

    if not channel:
        identifier = request.channel_id or request.channel_name
        raise HTTPException(status_code=404, detail=f"Channel '{identifier}' not found")

    channel_id = channel.id
    channel_name = channel.name
    if isinstance(channel, discord.CategoryChannel):
        channel_type = "category"
    elif isinstance(channel, discord.VoiceChannel):
        channel_type = "voice"
    elif isinstance(channel, discord.ForumChannel):
        channel_type = "forum"
    elif isinstance(channel, discord.StageChannel):
        channel_type = "stage"
    else:
        channel_type = "text"

    async with discord_api_errors("delete channels"):
        await channel.delete(reason="Deleted via MCP")
        return {
            "success": True,
            "deleted": {
                "id": channel_id,
                "name": channel_name,
                "type": channel_type,
            },
        }


@app.post("/channels/edit")
async def edit_channel(request: EditChannelRequest) -> dict[str, Any]:
    """Edit a channel's properties (name, topic, category)."""
    mgr = get_manager()
    collector = get_collector_or_404(mgr, request.bot_id)

    # Resolve channel
    channel = None
    if request.channel_id is not None:
        chan_id = int(request.channel_id) if isinstance(request.channel_id, str) else request.channel_id
        channel = collector.client.get_channel(chan_id)
        if channel is None:
            try:
                channel = await collector.client.fetch_channel(chan_id)
            except discord.NotFound:
                pass
    elif request.channel_name is not None:
        if request.guild_id is None:
            raise HTTPException(status_code=400, detail="guild_id required when using channel_name")
        guild = get_guild_or_404(collector, request.guild_id)
        # First try cache
        for ch in guild.channels:
            if ch.name.lower() == request.channel_name.lower():
                channel = ch
                break
        # If not in cache, fetch all channels from Discord
        if channel is None:
            fetched_channels = await guild.fetch_channels()
            for ch in fetched_channels:
                if ch.name.lower() == request.channel_name.lower():
                    channel = ch
                    break

    if channel is None:
        identifier = request.channel_id or request.channel_name
        raise HTTPException(status_code=404, detail=f"Channel '{identifier}' not found")

    # Only text channels can be edited this way
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(status_code=400, detail="Only text channels can be edited")

    # Build edit kwargs
    edit_kwargs: dict[str, Any] = {"reason": "Edited via MCP"}

    if request.new_name is not None:
        edit_kwargs["name"] = request.new_name

    if request.new_topic is not None:
        edit_kwargs["topic"] = request.new_topic if request.new_topic else None

    # Handle category change
    if request.category_id is not None or request.category_name is not None:
        guild = channel.guild
        new_category = None

        if request.category_id is not None:
            if request.category_id == "" or request.category_id == 0:
                # Remove from category
                new_category = None
            else:
                cat_id = int(request.category_id) if isinstance(request.category_id, str) else request.category_id
                new_category = guild.get_channel(cat_id)
                if not isinstance(new_category, discord.CategoryChannel):
                    raise HTTPException(status_code=400, detail=f"Channel {request.category_id} is not a category")
        elif request.category_name is not None:
            if request.category_name == "":
                # Remove from category
                new_category = None
            else:
                for cat in guild.categories:
                    if cat.name.lower() == request.category_name.lower():
                        new_category = cat
                        break
                if new_category is None and request.category_name != "":
                    raise HTTPException(status_code=404, detail=f"Category '{request.category_name}' not found")

        edit_kwargs["category"] = new_category

    if len(edit_kwargs) == 1:  # Only has "reason"
        raise HTTPException(status_code=400, detail="No changes specified")

    async with discord_api_errors("edit channel"):
        await channel.edit(**edit_kwargs)
        return {
            "success": True,
            "channel": {
                "id": str(channel.id),
                "name": channel.name,
                "topic": channel.topic,
                "category": channel.category.name if channel.category else None,
            },
        }


if __name__ == "__main__":
    import uvicorn
    from memory.common import settings

    uvicorn.run(
        "memory.discord.api:app",
        host="0.0.0.0",
        port=settings.DISCORD_COLLECTOR_PORT,
        reload=False,
    )
