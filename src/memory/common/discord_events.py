"""Discord gateway event payloads (the ``d`` of MESSAGE_CREATE etc.) as rows
and ``ADD_DISCORD_MESSAGE`` kwargs.

Webhook Events and the REST catch-up deliver raw JSON, not discord.py objects,
so this mirrors ``memory.discord.ingest`` without importing discord.py.
"""

from typing import Any

from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert

from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
)

# Gateway message types that are ordinary messages: DEFAULT and REPLY.
ORDINARY_MESSAGE_TYPES = {0, 19}
CHANNEL_TYPE_NAMES = {
    0: "text",
    1: "dm",
    2: "voice",
    3: "group_dm",
    5: "news",
    10: "thread",
    11: "thread",
    12: "thread",
    15: "forum",
}


def snowflake(value: Any) -> int | None:
    return int(value) if value not in (None, "") else None


def is_image(attachment: dict) -> bool:
    return (attachment.get("content_type") or "").startswith("image/")


def message_type(data: dict) -> str:
    if data.get("message_reference"):
        return "reply"
    if data.get("thread"):
        return "thread_starter"
    if data.get("type", 0) not in ORDINARY_MESSAGE_TYPES:
        return "system"
    return "default"


def message_task_kwargs(data: dict, bot_id: int, is_edit: bool = False) -> dict:
    """A full gateway/REST message object as ``ADD_DISCORD_MESSAGE`` kwargs."""
    attachments = data.get("attachments") or []
    images = [a["url"] for a in attachments if is_image(a) and a.get("url")]
    files = [
        {
            "filename": a.get("filename"),
            "content_type": a.get("content_type"),
            "size": a.get("size"),
            "url": a.get("url"),
        }
        for a in attachments
        if not is_image(a)
    ]
    reference = data.get("message_reference") or {}
    return {
        "bot_id": bot_id,
        "message_id": int(data["id"]),
        "channel_id": int(data["channel_id"]),
        "server_id": snowflake(data.get("guild_id")),
        "author_id": int(data["author"]["id"]),
        "content": data.get("content") or "",
        "sent_at": data["timestamp"],
        "edited_at": data.get("edited_timestamp"),
        "reply_to_message_id": snowflake(reference.get("message_id")),
        "thread_id": snowflake((data.get("thread") or {}).get("id")),
        "message_type": message_type(data),
        "is_pinned": bool(data.get("pinned")),
        "images": images or None,
        "embeds": data.get("embeds") or None,
        "attachments": files or None,
        "is_edit": is_edit,
    }


def emoji_key(emoji: dict | None) -> str | None:
    """Unicode emojis by character; custom ones as ``name:id``, since names repeat."""
    if not emoji:
        return None
    return f"{emoji.get('name')}:{emoji['id']}" if emoji.get("id") else emoji.get("name")


def is_full_message(data: dict) -> bool:
    return bool(data.get("author") and data.get("timestamp"))


def ensure_server(session: DBSession, server_id: int, bot_id: int, collect: bool) -> DiscordServer:
    """A server row for a pushed event, named by its id. A pushed guild id is
    only as trustworthy as the bot's owner, so the row is unverified: it gives
    that owner nothing until a token-authenticated bot claims it."""
    server, _ = get_or_insert(
        session, DiscordServer, server_id,
        id=server_id, name=f"Server {server_id}", bot_id=bot_id, collect_messages=collect, verified=False,
    )
    return server


def insert_missing(session: DBSession, model: type, **values: Any) -> bool:
    """Insert a row unless one with its key exists; True if this call made it.
    The gateway loop, its sync threads, Celery and webhooks all create these
    rows, so a plain get-then-insert would raise on the loser of a race."""
    stmt = pg_insert(model).values(**values).on_conflict_do_nothing().returning(literal_column("1"))
    return session.execute(stmt).first() is not None


def get_or_insert[T](session: DBSession, model: type[T], key: Any, **values: Any) -> tuple[T, bool]:
    """The row with this key, inserted from ``values`` if missing; True if this call made it."""
    if (row := session.get(model, key)) is not None:
        return row, False
    created = insert_missing(session, model, **values)
    return session.get_one(model, key), created


def verify_server(
    session: DBSession, server_id: int, bot_id: int, collect: bool, name: str | None = None
) -> DiscordServer:
    """Record that a bot's own token lists this server. The bot claims a server
    no verified bot owns; one first claimed by a push, or left by its owning
    bot, starts over with default access settings. Only the owning bot renames it."""
    server, created = get_or_insert(
        session, DiscordServer, server_id,
        id=server_id, name=name or f"Server {server_id}", bot_id=bot_id, collect_messages=collect, verified=True,
    )
    if not created and (not server.verified or server.bot_id is None):
        server.bot_id, server.verified = bot_id, True
        server.project_id, server.sensitivity = None, "basic"
        server.name = name or server.name
    elif name and server.bot_id == bot_id:
        server.name = name
    insert_missing(session, DiscordBotServer, bot_id=bot_id, server_id=server_id)
    return server


def hand_off_server(session: DBSession, server: DiscordServer, leaving: DiscordBot) -> None:
    """Pass a server the leaving bot owns to another of its owner's bots still
    in it, else to none, and the next bot to claim it starts over with default
    access settings: ``bot_id`` decides who manages them, so they never carry
    over to another owner."""
    if server.bot_id != leaving.id:
        return
    server.bot_id = (
        session.query(DiscordBotServer.bot_id)
        .join(DiscordBot, DiscordBot.id == DiscordBotServer.bot_id)
        .filter(
            DiscordBotServer.server_id == server.id,
            DiscordBotServer.bot_id != leaving.id,
            DiscordBot.created_by_user_id == leaving.created_by_user_id,
        )
        .order_by(DiscordBotServer.bot_id)
        .limit(1)
        .scalar()
    )


def ensure_channel(
    session: DBSession,
    channel_id: int,
    server_id: int | None,
    bot_id: int,
    *,
    name: str | None = None,
    channel_type: str | None = None,
    collect: bool | None = None,
) -> DiscordChannel:
    """A channel row, named by its id until a name is known. ``collect`` is
    only set on creation, so later choices made in the UI stand; only the
    bot owning the channel (or its server) renames it."""
    channel, created = get_or_insert(
        session, DiscordChannel, channel_id,
        id=channel_id,
        server_id=server_id,
        bot_id=bot_id,
        name=name or f"Channel {channel_id}",
        channel_type=channel_type or ("text" if server_id else "dm"),
        collect_messages=collect,
    )
    if not created and name and owns_channel(channel, {bot_id}):
        channel.name = name
    return channel


def owns_channel(channel: DiscordChannel, bot_ids: set[int]) -> bool:
    """A server channel belongs to its server's verified bot; a DM to the bot
    that first delivered it."""
    if channel.server is not None:
        return channel.server.verified and channel.server.bot_id in bot_ids
    return channel.bot_id in bot_ids


def may_change(message: DiscordMessage, bot_ids: set[int]) -> bool:
    """Whether events from these bots may edit, delete or react to a stored message."""
    return message.bot_id in bot_ids or (
        message.channel is not None and owns_channel(message.channel, bot_ids)
    )


def owner_bot_ids(session: DBSession, bot: DiscordBot) -> set[int]:
    """The bots of this bot's owner; just this one for an unowned bot."""
    if bot.created_by_user_id is None:
        return {bot.id}
    rows = session.query(DiscordBot.id).filter(DiscordBot.created_by_user_id == bot.created_by_user_id)
    return {row[0] for row in rows}


def ensure_user(session: DBSession, author: dict) -> DiscordUser:
    """The author's row, created if missing. Pushed names never overwrite stored ones."""
    user_id = int(author["id"])
    username = author.get("username") or str(user_id)
    user, _ = get_or_insert(
        session, DiscordUser, user_id,
        id=user_id, username=username, display_name=author.get("global_name") or username,
    )
    return user


def ensure_message_entities(
    session: DBSession, data: dict, bot_id: int, push_only: bool
) -> DiscordChannel:
    """Server, channel and author rows for a message. Servers and DMs first
    seen through a push-only bot collect everything: that bot receives only
    what it was sent."""
    server_id = snowflake(data.get("guild_id"))
    if server_id is not None:
        ensure_server(session, server_id, bot_id, collect=push_only)
    channel = ensure_channel(
        session,
        int(data["channel_id"]),
        server_id,
        bot_id,
        collect=True if push_only and server_id is None else None,
    )
    ensure_user(session, data["author"])
    return channel


def ensure_listed_channel(
    session: DBSession, data: dict, server_id: int | None, bot_id: int
) -> DiscordChannel:
    """A channel or thread object from the API, with its name and type."""
    channel_type = CHANNEL_TYPE_NAMES.get(data.get("type", 0), "text")
    existed = session.get(DiscordChannel, int(data["id"])) is not None
    channel = ensure_channel(
        session, int(data["id"]), server_id, bot_id, name=data.get("name"), channel_type=channel_type
    )
    parent_id = snowflake(data.get("parent_id"))
    if not existed and channel_type != "thread" and parent_id is not None:
        channel.category_id = parent_id
    return channel


def ensure_thread(session: DBSession, data: dict, bot_id: int, push_only: bool) -> DiscordChannel:
    server_id = snowflake(data.get("guild_id"))
    if server_id is not None:
        ensure_server(session, server_id, bot_id, collect=push_only)
    return ensure_listed_channel(session, data | {"type": data.get("type", 11)}, server_id, bot_id)
