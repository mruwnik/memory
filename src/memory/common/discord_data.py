"""
Shared data fetching functions for Discord.

Used by both REST API and MCP endpoints to avoid duplication.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import desc

from memory.common import discord as discord_client
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    User,
    discord_bot_users,
)


def discord_channel_for_target(
    session: DBSession, target: str
) -> DiscordChannel | None:
    """Return the DiscordChannel a target id refers to, or None.

    Discord channel and user ids are indistinguishable snowflakes, so a target
    is a channel iff it's a known DiscordChannel row. Shared by upsert-time
    validation and dispatch so the two never classify the same id differently.
    """
    if not target.isdigit():
        return None
    return session.get(DiscordChannel, int(target))


def discord_target_is_channel(session: DBSession, target: str) -> bool:
    """True if the target id is a known Discord channel (else a user DM)."""
    return discord_channel_for_target(session, target) is not None


def caller_can_see_discord_user(
    db: DBSession, caller: User, discord_user_id: int
) -> bool:
    """Return True if ``caller`` is allowed to read a particular DiscordUser row.

    A non-admin caller can only see Discord users they actually have a
    plausible reason to know about — i.e. one of their authorized bots
    has stored a message authored by that Discord user. Without this gate,
    any bot owner could enumerate every DiscordUser row in the system
    (including admin Discord IDs) and target them with the link endpoint.
    """
    if has_admin_scope(caller):
        return True

    seen = (
        db.query(DiscordMessage.id)
        .join(DiscordBot, DiscordMessage.bot_id == DiscordBot.id)
        .join(discord_bot_users, discord_bot_users.c.bot_id == DiscordBot.id)
        .filter(
            DiscordMessage.author_id == discord_user_id,
            discord_bot_users.c.user_id == caller.id,
        )
        .first()
    )
    return seen is not None


def caller_can_see_discord_channel(
    db: DBSession, caller: User, channel: DiscordChannel
) -> bool:
    """A channel is visible iff its server belongs to one of the caller's bots.

    Mirrors the Discord user gate and the Slack shared-workspace requirement so
    a caller can't address a server they have no bot in. Admins see everything.
    """
    if has_admin_scope(caller):
        return True
    if channel.server_id is None:
        return False
    server = db.get(DiscordServer, channel.server_id)
    if server is None or server.bot_id is None:
        return False
    return server.bot_id in {bot.id for bot in get_user_bots(db, caller.id)}


def get_user_bots(session: DBSession, user_id: int) -> list[DiscordBot]:
    """Get all Discord bots authorized for a user."""
    return (
        session.query(DiscordBot)
        .join(discord_bot_users)
        .filter(discord_bot_users.c.user_id == user_id)
        .all()
    )


def get_bot_for_user(session: DBSession, bot_id: int, user: User) -> DiscordBot | None:
    """Get a bot by ID if the user is authorized to use it."""
    bot = session.get(DiscordBot, bot_id)
    if not bot or not bot.is_authorized(user):
        return None
    return bot


def fetch_channel_history(
    session: DBSession,
    channel_id: str | None,
    channel_name: str | None,
    before_dt: datetime | None,
    after_dt: datetime | None,
    limit: int,
) -> dict[str, Any]:
    """
    Fetch message history from a Discord channel.

    Args:
        session: Database session
        channel_id: Discord channel ID (snowflake, can be string or int)
        channel_name: Discord channel name
        before_dt: Only get messages before this time
        after_dt: Only get messages after this time
        limit: Maximum number of messages to return

    Returns:
        Dict with channel info, messages list, and metadata
    """
    # Resolve channel
    if channel_id is not None:
        resolved_channel_id = int(channel_id)
        channel = session.get(DiscordChannel, resolved_channel_id)
        channel_info = {
            "id": str(resolved_channel_id),
            "name": channel.name if channel else "unknown",
        }
    else:
        channel = (
            session.query(DiscordChannel)
            .filter(DiscordChannel.name == channel_name)
            .first()
        )
        if not channel:
            raise ValueError(f"Channel '{channel_name}' not found")
        resolved_channel_id = channel.id
        channel_info = {"id": str(channel.id), "name": channel.name}

    # Build query
    query = session.query(DiscordMessage).filter(
        DiscordMessage.channel_id == resolved_channel_id
    )

    if before_dt:
        query = query.filter(DiscordMessage.sent_at < before_dt)
    if after_dt:
        query = query.filter(DiscordMessage.sent_at > after_dt)

    # Order by sent_at descending (newest first), then limit
    query = query.order_by(desc(DiscordMessage.sent_at)).limit(limit)

    messages = query.all()

    # Prefetch all authors in a single query to avoid N+1
    author_ids = {msg.author_id for msg in messages}
    authors = {
        u.id: u
        for u in session.query(DiscordUser).filter(DiscordUser.id.in_(author_ids)).all()
    }

    # Format messages
    formatted = []
    for msg in messages:
        # Get author info from prefetched cache
        author = authors.get(msg.author_id)
        author_name = author.name if author else f"user_{msg.author_id}"

        formatted.append(
            {
                "id": str(msg.message_id),
                "author": author_name,
                "author_id": str(msg.author_id),
                "content": msg.content,
                "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
                "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
                "is_pinned": msg.is_pinned,
                "reactions": msg.reactions,
                "reply_to": str(msg.reply_to_message_id)
                if msg.reply_to_message_id
                else None,
            }
        )

    # Reverse to get chronological order
    formatted.reverse()

    return {
        "channel": channel_info,
        "messages": formatted,
        "count": len(formatted),
        "limit": limit,
    }


def fetch_channels(
    session: DBSession,
    server: str | None,
    include_dms: bool,
) -> dict[str, Any]:
    """
    List Discord channels the bot has access to.

    Args:
        session: Database session
        server: Filter by server - can be numeric ID or server name
        include_dms: Include DM channels

    Returns:
        Dict with channels list and count
    """
    query = session.query(DiscordChannel)

    # Filter by server if specified
    if server is not None:
        resolved_server_id = discord_client.resolve_guild(server, session)
        if resolved_server_id is None:
            return {"channels": [], "count": 0, "error": f"Server '{server}' not found"}
        query = query.filter(DiscordChannel.server_id == resolved_server_id)

    # Filter out DMs unless requested
    if not include_dms:
        query = query.filter(DiscordChannel.channel_type != "dm")

    channels = query.all()

    formatted = []
    for ch in channels:
        formatted.append(
            {
                "id": str(ch.id),  # String to avoid JS precision loss
                "name": ch.name,
                "type": ch.channel_type,
                "server_id": str(ch.server_id) if ch.server_id else None,
                "category_id": str(ch.category_id) if ch.category_id else None,
                "collect_messages": ch.should_collect,
                "project_id": ch.project_id,
            }
        )

    return {"channels": formatted, "count": len(formatted)}


def fetch_servers(
    session: DBSession, user_id: int | None = None
) -> list[DiscordServer]:
    """Fetch Discord servers, optionally scoped to a user's bots.

    Args:
        session: Database session.
        user_id: When provided, only return servers whose ``bot_id`` belongs
            to a bot that this user is authorized for.  Servers with no
            ``bot_id`` set (legacy rows or orphaned after bot deletion) are
            excluded from per-user listings — they are admin-only.
    """
    query = session.query(DiscordServer)
    if user_id is not None:
        user_bot_ids = [bot.id for bot in get_user_bots(session, user_id)]
        if not user_bot_ids:
            return []
        query = query.filter(DiscordServer.bot_id.in_(user_bot_ids))
    return query.order_by(DiscordServer.name).all()
