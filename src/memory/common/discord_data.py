"""
Shared data fetching functions for Discord.

Used by both REST API and MCP endpoints to avoid duplication.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Query

from memory.common.access_control import UserLike, has_admin_scope
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    User,
)


def discord_channel_for_target(
    session: DBSession, target: str | int
) -> DiscordChannel | None:
    """Return the DiscordChannel a target id refers to, or None.

    Discord channel and user ids are indistinguishable snowflakes, so a target
    is a channel iff it's a known DiscordChannel row. Shared by upsert-time
    validation and dispatch so the two never classify the same id differently.
    Accepts str or int: a raw snowflake from a celery JSON boundary stays int,
    and this is the one place that classifies it.
    """
    target = str(target)
    if not target.isdigit():
        return None
    return session.get(DiscordChannel, int(target))


def discord_target_is_channel(session: DBSession, target: str | int) -> bool:
    """True if the target id is a known Discord channel (else a user DM)."""
    return discord_channel_for_target(session, target) is not None


def caller_can_see_discord_user(
    db: DBSession, caller: UserLike, discord_user_id: int
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
        .filter(
            DiscordMessage.author_id == discord_user_id,
            DiscordBot.created_by_user_id == caller.id,
        )
        .first()
    )
    return seen is not None


def visible_channels(db: DBSession, caller: UserLike) -> Query[DiscordChannel]:
    """Channels in servers owned by one of the caller's bots; admins see all.

    Mirrors the Discord user gate and the Slack shared-workspace requirement so
    a caller can't address a server they have no bot in. Server-less channels
    (DMs) and servers without a bot are admin-only.
    """
    query = db.query(DiscordChannel)
    if has_admin_scope(caller):
        return query
    return query.join(DiscordServer, DiscordChannel.server_id == DiscordServer.id).join(
        DiscordBot, DiscordServer.bot_id == DiscordBot.id
    ).filter(DiscordBot.created_by_user_id == caller.id)


def caller_can_see_discord_channel(
    db: DBSession, caller: UserLike, channel: DiscordChannel
) -> bool:
    return visible_channels(db, caller).filter(DiscordChannel.id == channel.id).first() is not None


def find_visible_channel(
    db: DBSession, caller: UserLike, channel_id: str | None, channel_name: str | None
) -> DiscordChannel:
    """The caller-visible channel with this id (else name), or ValueError."""
    query = visible_channels(db, caller)
    if channel_id is not None:
        channel = query.filter(DiscordChannel.id == int(channel_id)).first()
    else:
        channel = query.filter(DiscordChannel.name == channel_name).first()
    if channel is None:
        raise ValueError(f"Discord channel '{channel_id or channel_name}' not found or not accessible")
    return channel


def get_user_bots(session: DBSession, user_id: int) -> list[DiscordBot]:
    """Get all Discord bots authorized for a user."""
    return (
        session.query(DiscordBot)
        .filter(DiscordBot.created_by_user_id == user_id)
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
    caller: UserLike,
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
        caller: Whose bots decide which channels are readable
        channel_id: Discord channel ID (snowflake, can be string or int)
        channel_name: Discord channel name
        before_dt: Only get messages before this time
        after_dt: Only get messages after this time
        limit: Maximum number of messages to return

    Returns:
        Dict with channel info, messages list, and metadata
    """
    channel = find_visible_channel(session, caller, channel_id, channel_name)
    channel_info = {"id": str(channel.id), "name": channel.name}

    # Build query
    query = session.query(DiscordMessage).filter(
        DiscordMessage.channel_id == channel.id
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


CHANNEL_FIELDS: dict[str, Callable[[DiscordChannel], Any]] = {
    # Snowflakes as strings to avoid JS precision loss
    "id": lambda ch: str(ch.id),
    "name": lambda ch: ch.name,
    "type": lambda ch: ch.channel_type,
    "server_id": lambda ch: str(ch.server_id) if ch.server_id else None,
    "category_id": lambda ch: str(ch.category_id) if ch.category_id else None,
    "collect_messages": lambda ch: ch.should_collect,
    "project_id": lambda ch: ch.project_id,
    "sensitivity": lambda ch: ch.sensitivity,
}

DEFAULT_CHANNEL_FIELDS = ["id", "name", "type", "server_id"]


def fetch_channels(
    session: DBSession,
    caller: UserLike,
    server: str | None,
    include_dms: bool,
    name: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """List the Discord channels visible to ``caller`` (see ``visible_channels``).

    ``server`` is a numeric id or name, resolved only among visible servers so
    another user's server reads as not found. ``name`` is a case-insensitive
    substring match.
    """
    fields = fields or DEFAULT_CHANNEL_FIELDS
    invalid = set(fields) - CHANNEL_FIELDS.keys()
    if invalid:
        raise ValueError(f"Invalid fields: {sorted(invalid)}. Available: {sorted(CHANNEL_FIELDS)}")

    query = visible_channels(session, caller)

    if server is not None:
        servers = fetch_servers(session, user_id=None if has_admin_scope(caller) else caller.id)
        match = next((s for s in servers if server in (str(s.id), s.name)), None)
        if match is None:
            return {"channels": [], "count": 0, "error": f"Server '{server}' not found"}
        query = query.filter(DiscordChannel.server_id == match.id)

    if not include_dms:
        query = query.filter(DiscordChannel.channel_type != "dm")
    if name:
        query = query.filter(DiscordChannel.name.ilike(f"%{name}%"))

    formatted = [{f: CHANNEL_FIELDS[f](ch) for f in fields} for ch in query.order_by(DiscordChannel.name)]
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
