"""
Shared data fetching functions for Discord.

Used by both REST API and MCP endpoints to avoid duplication.
"""

from sqlalchemy import ColumnElement, SQLColumnExpression, and_, exists, or_
from sqlalchemy.orm import Query

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


def discord_user_seen_by(
    caller_id: int, discord_user_id: int | SQLColumnExpression[int]
) -> ColumnElement[bool]:
    """SQL condition: one of user ``caller_id``'s bots stored a message authored by
    ``discord_user_id`` (a value or a column, so it can correlate inside a query).

    The non-admin visibility rule for DiscordUser rows, shared by
    ``caller_can_see_discord_user`` and searches that match on Discord handles.
    """
    return exists().where(
        DiscordMessage.author_id == discord_user_id,
        DiscordMessage.bot_id == DiscordBot.id,
        DiscordBot.created_by_user_id == caller_id,
    )


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
    return bool(db.query(discord_user_seen_by(caller.id, discord_user_id)).scalar())


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
