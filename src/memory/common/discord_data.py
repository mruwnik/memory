"""
Shared data fetching functions for Discord.

Used by both REST API and MCP endpoints to avoid duplication.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import desc

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
    channel_id: int | None,
    channel_name: str | None,
    before_dt: datetime | None,
    after_dt: datetime | None,
    limit: int,
) -> dict[str, Any]:
    """
    Fetch message history from a Discord channel.

    Args:
        session: Database session
        channel_id: Discord channel ID (snowflake)
        channel_name: Discord channel name
        before_dt: Only get messages before this time
        after_dt: Only get messages after this time
        limit: Maximum number of messages to return

    Returns:
        Dict with channel info, messages list, and metadata
    """
    # Resolve channel
    if channel_id is not None:
        resolved_channel_id = channel_id
        channel = session.get(DiscordChannel, channel_id)
        channel_info = {
            "id": channel_id,
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
        channel_info = {"id": channel.id, "name": channel.name}

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

        formatted.append({
            "id": msg.message_id,
            "author": author_name,
            "author_id": msg.author_id,
            "content": msg.content,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
            "is_pinned": msg.is_pinned,
            "reactions": msg.reactions,
            "reply_to": msg.reply_to_message_id,
        })

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
    server_id: int | str | None,
    server_name: str | None,
    include_dms: bool,
) -> dict[str, Any]:
    """
    List Discord channels the bot has access to.

    Args:
        session: Database session
        server_id: Filter by server ID (accepts string for JavaScript compatibility)
        server_name: Filter by server name
        include_dms: Include DM channels

    Returns:
        Dict with channels list and count
    """
    query = session.query(DiscordChannel)

    # Filter by server if specified (convert string to int if needed)
    if server_id is not None:
        server_id_int = int(server_id) if isinstance(server_id, str) else server_id
        query = query.filter(DiscordChannel.server_id == server_id_int)
    elif server_name is not None:
        server = (
            session.query(DiscordServer)
            .filter(DiscordServer.name == server_name)
            .first()
        )
        if server:
            query = query.filter(DiscordChannel.server_id == server.id)
        else:
            return {"channels": [], "count": 0, "error": f"Server '{server_name}' not found"}

    # Filter out DMs unless requested
    if not include_dms:
        query = query.filter(DiscordChannel.channel_type != "dm")

    channels = query.all()

    formatted = []
    for ch in channels:
        formatted.append({
            "id": str(ch.id),  # String to avoid JS precision loss
            "name": ch.name,
            "type": ch.channel_type,
            "server_id": str(ch.server_id) if ch.server_id else None,
            "category_id": str(ch.category_id) if ch.category_id else None,
            "collect_messages": ch.should_collect,
            "project_id": ch.project_id,
        })

    return {"channels": formatted, "count": len(formatted)}


def fetch_servers(session: DBSession) -> list[DiscordServer]:
    """Fetch all Discord servers ordered by name."""
    return session.query(DiscordServer).order_by(DiscordServer.name).all()
