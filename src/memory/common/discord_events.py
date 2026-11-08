"""Discord gateway event payloads (the ``d`` of MESSAGE_CREATE etc.) as rows
and ``ADD_DISCORD_MESSAGE`` kwargs.

Webhook Events and the REST catch-up deliver raw JSON, not discord.py objects,
so this mirrors ``memory.discord.ingest`` without importing discord.py.
"""

from typing import Any

from sqlalchemy import select, update

from memory.common.content_processing import clear_item_chunks
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    Event,
)
from memory.common.db.models.discord import DM_CHANNEL_TYPES
from memory.common.db.models.events import NOT_COLLECTED, RAW
from memory.common.db.upsert import get_or_insert, insert_missing

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
    trusted: bool = False,
) -> DiscordChannel:
    """A channel row, named by its id until a name is known. ``collect`` is
    only set on creation, so later choices made in the UI stand; only the
    bot owning the channel (or its server) renames it.

    A DM row can sit on an id that is really a server's channel. Seen in a
    server by ``trusted`` data (the gateway or a token-read listing), the row
    becomes that server's channel, with the settings a new one has."""
    channel, created = get_or_insert(
        session, DiscordChannel, channel_id,
        id=channel_id,
        server_id=server_id,
        bot_id=bot_id,
        name=name or f"Channel {channel_id}",
        channel_type=channel_type or ("text" if server_id else "dm"),
        collect_messages=collect,
    )
    if created:
        return channel
    if trusted and server_id is not None and channel.server_id is None:
        move_to_server(session, channel, server_id, bot_id, channel_type or "text")
    if name and owns_channel(channel, {bot_id}):
        channel.name = name
    return channel


def reset_access(channel: DiscordChannel) -> None:
    channel.collect_messages, channel.project_id, channel.sensitivity = None, None, "basic"


def move_to_server(
    session: DBSession, channel: DiscordChannel, server_id: int, bot_id: int | None, channel_type: str
) -> None:
    channel.server_id, channel.bot_id, channel.channel_type = server_id, bot_id, channel_type
    reset_access(channel)
    session.flush()
    session.expire(channel, ["server"])


def claim_dm(
    session: DBSession, channel_id: int, bot: DiscordBot, *, name: str | None = None, channel_type: str = "dm"
) -> tuple[DiscordChannel, bool]:
    """A DM as trusted data (the gateway, or the bot's token) shows it, and
    whether it is this bot's. A row held as a server's channel becomes this
    bot's DM, with the settings a new one has; one another bot of the same
    owner holds passes to this one. A DM another owner holds stays theirs:
    owners who registered the same application share its DMs' ids."""
    channel, created = get_or_insert(
        session, DiscordChannel, channel_id,
        id=channel_id, server_id=None, bot_id=bot.id, name=name or f"Channel {channel_id}", channel_type=channel_type,
    )
    if not created and channel.server_id is not None:
        channel.server_id, channel.bot_id, channel.channel_type, channel.parent_id = None, bot.id, channel_type, None
        reset_access(channel)
        session.flush()
        session.expire(channel, ["server", "parent", "bot"])
    elif not created and channel.bot_id != bot.id:
        holder = channel.bot.created_by_user_id if channel.bot else None
        if holder is None or holder != bot.created_by_user_id:
            return channel, False
        channel.bot_id = bot.id
        session.flush()
        session.expire(channel, ["bot"])
    if name:
        channel.name = name
    return channel, True


def may_push_into(session: DBSession, server_id: int, bot_ids: set[int]) -> bool:
    """Whether pushes from the owner of these bots may add to a server: a new
    one, one their bot holds, or one a token lists one of their bots in."""
    server = session.get(DiscordServer, server_id)
    if server is None or server.bot_id in bot_ids:
        return True
    member = session.query(DiscordBotServer).filter(
        DiscordBotServer.server_id == server_id, DiscordBotServer.bot_id.in_(bot_ids)
    )
    return member.first() is not None


def push_refusal(
    session: DBSession, channel_id: int, server_id: int | None, bot_ids: set[int], parent_id: int | None = None
) -> str | None:
    """Why a push about this channel (from the owner of ``bot_ids``) is
    refused, or None. A push is untrusted: it may only touch a stored row in a
    server it may push into, or a DM of its owner's, and only as that row
    stands (its stored server, never a pushed one); it may only create a row
    under a server it may push into, as a DM needs the bot's token to show it.
    A thread's parent must be a channel of the same server."""
    channel = session.get(DiscordChannel, channel_id)
    if channel is not None and channel.server_id != server_id:
        return "guild_mismatch"
    if server_id is None and (channel is None or not channel.is_direct):
        return "no_server"
    if server_id is None and channel is not None and channel.bot_id not in bot_ids:
        return "not_owner"
    if server_id is not None and not may_push_into(session, server_id, bot_ids):
        return "not_member"
    parent = session.get(DiscordChannel, parent_id) if parent_id is not None else None
    if parent is not None and parent.server_id != server_id:
        return "guild_mismatch"
    return None


def knows_user(session: DBSession, bot_id: int, user_ids: list[int]) -> bool:
    """Whether any of these users has posted in a server the bot is in, by the
    stored channels' servers: only they may start a DM Memory collects."""
    servers = select(DiscordBotServer.server_id).where(DiscordBotServer.bot_id == bot_id)
    posted = (
        session.query(DiscordMessage.id)
        .join(DiscordChannel, DiscordChannel.id == DiscordMessage.channel_id)
        .filter(DiscordMessage.author_id.in_(user_ids), DiscordChannel.server_id.in_(servers))
    )
    return posted.first() is not None


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
    session: DBSession, data: dict, bot_id: int, push_only: bool, *, trusted: bool = False
) -> DiscordChannel:
    """Server, channel and author rows for a message. Servers first seen
    through a push-only bot collect everything: that bot receives only what
    it was sent."""
    server_id = snowflake(data.get("guild_id"))
    if server_id is not None:
        ensure_server(session, server_id, bot_id, collect=push_only)
    channel = ensure_channel(session, int(data["channel_id"]), server_id, bot_id, trusted=trusted)
    ensure_user(session, data["author"])
    return channel


def ensure_listed_channel(
    session: DBSession, data: dict, server_id: int | None, bot_id: int, *, trusted: bool = False
) -> DiscordChannel:
    """A channel or thread object from the API, with its name and type."""
    channel_type = CHANNEL_TYPE_NAMES.get(data.get("type", 0), "text")
    existed = session.get(DiscordChannel, int(data["id"])) is not None
    channel = ensure_channel(
        session, int(data["id"]), server_id, bot_id, name=data.get("name"), channel_type=channel_type, trusted=trusted
    )
    # A thread first stored from a message event was taken for a text channel.
    channel.channel_type = channel_type
    parent_id = snowflake(data.get("parent_id"))
    if channel_type == "thread":
        # A thread first stored from a message event has no parent yet.
        set_thread_parent(session, channel, parent_id)
    elif not existed and parent_id is not None:
        channel.category_id = parent_id
    return channel


def set_thread_parent(session: DBSession, thread: DiscordChannel, parent_id: int | None) -> None:
    """Record a thread's parent. Its first messages can be stored before the
    parent is known; if the parent leaves the thread not collected, they are
    removed, as if never collected, and the webhook deliveries about it become
    bare not_collected stubs."""
    if parent_id is None or thread.parent_id == parent_id:
        return
    thread.parent_id = parent_id
    session.flush()
    session.expire(thread, ["parent"])
    if thread.should_collect:
        return
    for message in session.query(DiscordMessage).filter(DiscordMessage.channel_id == thread.id):
        clear_item_chunks(message, session)
        session.delete(message)
    # Only the server's bots can have delivered events about the thread; naming
    # them lets the update use events_account_idx.
    server_bots = select(DiscordBotServer.bot_id).where(DiscordBotServer.server_id == thread.server_id)
    owner_bot = select(DiscordServer.bot_id).where(DiscordServer.id == thread.server_id)
    bots = {thread.bot_id, *session.scalars(server_bots), *session.scalars(owner_bot)} - {None}
    session.execute(
        update(Event)
        .where(
            Event.kind == RAW,
            Event.source == "discord",
            Event.account_id.in_(bots),
            Event.container == str(thread.id),
        )
        .values(outcome=NOT_COLLECTED, container=None, payload=None)
    )


def ensure_thread(
    session: DBSession, data: dict, bot_id: int, push_only: bool, *, trusted: bool = False
) -> DiscordChannel:
    server_id = snowflake(data.get("guild_id"))
    if server_id is not None:
        ensure_server(session, server_id, bot_id, collect=push_only)
    return ensure_listed_channel(session, data | {"type": data.get("type", 11)}, server_id, bot_id, trusted=trusted)


def reclaim_dms(session: DBSession, bot: DiscordBot) -> None:
    """Give this bot its owner's DMs whose bot was deleted: those holding
    messages that are the owner's."""
    if bot.created_by_user_id is None:
        return
    owned = select(DiscordMessage.channel_id).where(DiscordMessage.creator_id == bot.created_by_user_id)
    session.execute(
        update(DiscordChannel)
        .where(
            DiscordChannel.bot_id.is_(None),
            DiscordChannel.server_id.is_(None),
            DiscordChannel.channel_type.in_(DM_CHANNEL_TYPES),
            DiscordChannel.id.in_(owned),
        )
        .values(bot_id=bot.id)
    )
