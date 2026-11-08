"""
Shared data fetching functions for Discord.

Used by both REST API and MCP endpoints to avoid duplication.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Query, joinedload

from memory.common.access_control import UserLike, has_admin_scope
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    User,
)
from memory.common.db.models.discord import DM_CHANNEL_TYPES


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
    """Channels in servers one of the caller's bots is verified in, and DMs
    one of them delivered first; admins see all.

    Mirrors the Discord user gate and the Slack shared-workspace requirement so
    a caller can't address a server they have no bot in. Servers without a bot
    and DMs without one are admin-only.
    """
    query = db.query(DiscordChannel)
    if has_admin_scope(caller):
        return query
    caller_bots = db.query(DiscordBot.id).filter(DiscordBot.created_by_user_id == caller.id)
    return query.filter(
        or_(
            DiscordChannel.server_id.in_(verified_server_ids(db, caller_bots)),
            and_(DiscordChannel.server_id.is_(None), DiscordChannel.bot_id.in_(caller_bots)),
        )
    )


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
    """A user's Discord bots, gateway bots first, so the default sender is stable."""
    return (
        session.query(DiscordBot)
        .filter(DiscordBot.created_by_user_id == user_id)
        .order_by(DiscordBot.realtime == "webhook", DiscordBot.id)
        .all()
    )


def get_bot_for_user(session: DBSession, bot_id: int, user: User) -> DiscordBot | None:
    """Get a bot by ID if the user is authorized to use it."""
    bot = session.get(DiscordBot, bot_id)
    if not bot or not bot.is_authorized(user):
        return None
    return bot


CHANNEL_FIELDS: dict[str, Callable[[DiscordChannel], Any]] = {
    # Snowflakes as strings to avoid JS precision loss
    "id": lambda ch: str(ch.id),
    "name": lambda ch: ch.name,
    "type": lambda ch: ch.channel_type,
    "server_id": lambda ch: str(ch.server_id) if ch.server_id else None,
    "server_name": lambda ch: ch.server.name if ch.server else None,
    "category_id": lambda ch: str(ch.category_id) if ch.category_id else None,
    "collect_messages": lambda ch: ch.collect_messages,
    "effective_collect": lambda ch: ch.should_collect,
    "project_id": lambda ch: ch.project_id,
    "sensitivity": lambda ch: ch.sensitivity,
}

DEFAULT_CHANNEL_FIELDS = ["id", "name", "type", "server_id", "server_name"]


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

    query = visible_channels(session, caller).options(joinedload(DiscordChannel.server))

    if server is not None:
        servers = fetch_servers(session, user_id=None if has_admin_scope(caller) else caller.id)
        match = next((s for s in servers if server in (str(s.id), s.name)), None)
        if match is None:
            return {"channels": [], "count": 0, "error": f"Server '{server}' not found"}
        query = query.filter(DiscordChannel.server_id == match.id)

    if not include_dms:
        query = query.filter(DiscordChannel.channel_type.notin_(DM_CHANNEL_TYPES))
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
        user_id: When provided, only return servers one of this user's bots
            is verified in: by owning it, or by its own token listing it.
            Pushed events alone never show a server.
    """
    query = session.query(DiscordServer)
    if user_id is not None:
        user_bot_ids = [bot.id for bot in get_user_bots(session, user_id)]
        if not user_bot_ids:
            return []
        query = query.filter(DiscordServer.id.in_(verified_server_ids(session, user_bot_ids)))
    return query.order_by(DiscordServer.name).all()


def verified_server_ids(session: DBSession, bot_ids) -> Query:
    """Servers these bots own as verified bots, or whose membership their tokens confirmed."""
    owned = session.query(DiscordServer.id).filter(
        DiscordServer.verified.is_(True), DiscordServer.bot_id.in_(bot_ids)
    )
    member = session.query(DiscordBotServer.server_id).filter(DiscordBotServer.bot_id.in_(bot_ids))
    return owned.union(member)


def server_bot_ids(session: DBSession, bot_ids) -> dict[int, list[int]]:
    """Which of these bots are verified in each server, by the same rule as verified_server_ids."""
    owned = session.query(DiscordServer.id, DiscordServer.bot_id).filter(
        DiscordServer.verified.is_(True), DiscordServer.bot_id.in_(bot_ids)
    )
    member = session.query(DiscordBotServer.server_id, DiscordBotServer.bot_id).filter(
        DiscordBotServer.bot_id.in_(bot_ids)
    )
    by_server: dict[int, list[int]] = {}
    for server_id, bot_id in sorted(owned.union(member).all()):
        by_server.setdefault(server_id, []).append(bot_id)
    return by_server
