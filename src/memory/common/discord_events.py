"""Discord gateway event payloads (the ``d`` of MESSAGE_CREATE etc.) as rows
and ``ADD_DISCORD_MESSAGE`` kwargs.

Webhook Events and the REST catch-up deliver raw JSON, not discord.py objects,
so this mirrors ``memory.discord.ingest`` without importing discord.py.
"""

import logging
import re
from datetime import datetime
from typing import Any, Callable, Sequence

from sqlalchemy import select, update

from memory.common.celery_app import VERIFY_DISCORD_MESSAGES, app
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
    SourceItem,
)
from memory.common.db.models.discord import DM_CHANNEL_TYPES
from memory.common.db.models.events import NOT_COLLECTED, RAW
from memory.common.db.upsert import get_or_insert, insert_missing

logger = logging.getLogger(__name__)

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


# Discord ids are unsigned 64-bit, but none fills the top bit, so all fit a BIGINT.
SNOWFLAKE = re.compile(r"[0-9]{1,19}")
MAX_SNOWFLAKE = 2**63 - 1
ID_FIELDS = ("id", "channel_id", "guild_id", "parent_id", "message_id", "user_id")
NESTED_IDS = {"author": "id", "message_reference": "message_id", "thread": "id"}
TIMESTAMP_FIELDS = ("timestamp", "edited_timestamp")


def is_snowflake(value: Any) -> bool:
    return (
        isinstance(value, (str, int))
        and not isinstance(value, bool)
        and SNOWFLAKE.fullmatch(str(value)) is not None
        and int(value) <= MAX_SNOWFLAKE
    )


def snowflake(value: Any) -> int | None:
    return int(value) if is_snowflake(value) else None


def absent_or(value: Any, check: Callable[[Any], bool]) -> bool:
    return value in (None, "") or check(value)


def is_timestamp(value: Any) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return True


def is_text(value: Any) -> bool:
    return isinstance(value, (str, type(None)))


def texts(obj: dict, *fields: str) -> bool:
    return all(is_text(obj.get(field)) for field in fields)


def well_formed(data: Any) -> bool:
    """Whether a payload has the shape Discord gives its events: every id a
    snowflake, nested objects objects, names and types of their kind. A bot's
    key signs whatever its owner sends, so a push may be any JSON."""
    if not isinstance(data, dict):
        return False
    nested = [data.get(field) for field in NESTED_IDS]
    if not all(value is None or isinstance(value, dict) for value in nested):
        return False
    ids = data.get("ids")
    attachments = data.get("attachments")
    emoji = data.get("emoji")
    kind = data.get("type")
    return (
        all(absent_or(data.get(field), is_snowflake) for field in ID_FIELDS)
        and all(absent_or((data.get(f) or {}).get(key), is_snowflake) for f, key in NESTED_IDS.items())
        and texts(data.get("author") or {}, "username", "global_name")
        and (ids is None or (isinstance(ids, list) and all(is_snowflake(i) for i in ids)))
        and (attachments is None or (
            isinstance(attachments, list)
            and all(isinstance(a, dict) and texts(a, "filename", "content_type", "url") for a in attachments)
        ))
        and (emoji is None or (isinstance(emoji, dict) and texts(emoji, "name") and absent_or(emoji.get("id"), is_snowflake)))
        and (kind is None or (isinstance(kind, int) and not isinstance(kind, bool)))
        and texts(data, "content", "name")
        and all(absent_or(data.get(field), is_timestamp) for field in TIMESTAMP_FIELDS)
    )


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
    bot, starts over with default access settings, and what owners not in a
    server first claimed by a push pushed into it is disowned. Only the
    owning bot renames it."""
    server, created = get_or_insert(
        session, DiscordServer, server_id,
        id=server_id, name=name or f"Server {server_id}", bot_id=bot_id, collect_messages=collect, verified=True,
    )
    pushed = not created and not server.verified
    if not created and (pushed or server.bot_id is None):
        server.bot_id, server.verified = bot_id, True
        server.project_id, server.sensitivity = None, "basic"
        server.name = name or server.name
    elif name and server.bot_id == bot_id:
        server.name = name
    insert_missing(session, DiscordBotServer, bot_id=bot_id, server_id=server_id)
    if pushed:
        session.flush()
        disown_outsiders(session, server_id, bot_id)
    return server


def disown_outsiders(session: DBSession, server_id: int, bot_id: int | None, channel_id: int | None = None) -> None:
    """Take the messages in this server (or in one channel of it) that bots
    whose owner may not push into it delivered from that owner: it is no
    longer their creator, nor can it change them. They are what pushes filed
    there before a token showed whose server, or channel, it is, but also
    what a bot that has since left delivered, so they are kept until
    ``bot_id``'s token, which showed it, checks them."""
    channels = select(DiscordChannel.id).where(DiscordChannel.server_id == server_id)
    if channel_id is not None:
        channels = channels.where(DiscordChannel.id == channel_id)
    in_server = DiscordMessage.channel_id.in_(channels)
    delivered_by = session.scalars(
        select(DiscordMessage.bot_id).where(in_server, DiscordMessage.bot_id.is_not(None)).distinct()
    ).all()
    outsiders = [
        bot_id for bot_id in delivered_by
        if not may_push_into(session, server_id, owner_bot_ids(session, session.get_one(DiscordBot, bot_id)))
    ]
    if not outsiders:
        return
    ids = session.scalars(select(DiscordMessage.id).where(in_server, DiscordMessage.bot_id.in_(outsiders))).all()
    # creator_id is on source_item and bot_id on discord_message: one UPDATE per table.
    session.execute(update(SourceItem).where(SourceItem.id.in_(ids)).values(creator_id=None))
    session.execute(update(DiscordMessage).where(DiscordMessage.id.in_(ids)).values(bot_id=None))
    logger.info("Discord server %s channel %s: disowned %s messages of bots %s", server_id, channel_id, len(ids), outsiders)
    verify_disowned(bot_id, ids)


# Messages per verification task: each is one REST call.
VERIFY_CHUNK = 100


def verify_disowned(bot_id: int | None, item_ids: Sequence[int]) -> None:
    """Queue a check of disowned messages with the token of the bot that now
    holds them: their text is only what another owner pushed."""
    if bot_id is None:
        return
    for start in range(0, len(item_ids), VERIFY_CHUNK):
        app.send_task(VERIFY_DISCORD_MESSAGES, args=[bot_id, list(item_ids[start:start + VERIFY_CHUNK])])


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

    A push can store an id that is really another server's channel as a DM,
    or under the wrong server. Seen in a server by ``trusted`` data (the
    gateway or a token-read listing), the row becomes that server's channel,
    with the settings a new one has."""
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
    if trusted and server_id is not None and channel.server_id != server_id:
        move_to_server(session, channel, server_id, bot_id, channel_type or "text")
    if name and owns_channel(channel, {bot_id}):
        channel.name = name
    return channel


def reset_access(channel: DiscordChannel) -> None:
    channel.collect_messages, channel.project_id, channel.sensitivity = None, None, "basic"


def move_to_server(
    session: DBSession, channel: DiscordChannel, server_id: int, bot_id: int | None, channel_type: str
) -> None:
    """File a channel under the server trusted data shows it in, as a new one.
    Its stored parent came from the server it is leaving, and what owners who
    may not push into its server pushed into it is disowned. Server messages
    have no creator, so a DM owner's creator rights over its messages end."""
    if channel.server_id is None:
        in_channel = select(DiscordMessage.id).where(DiscordMessage.channel_id == channel.id)
        session.execute(update(SourceItem).where(SourceItem.id.in_(in_channel)).values(creator_id=None))
    channel.server_id, channel.bot_id, channel.channel_type, channel.parent_id = server_id, bot_id, channel_type, None
    reset_access(channel)
    session.execute(
        update(DiscordMessage).where(DiscordMessage.channel_id == channel.id).values(server_id=server_id)
    )
    session.flush()
    session.expire(channel, ["server", "parent"])
    disown_outsiders(session, server_id, bot_id, channel.id)


def claim_dm(
    session: DBSession, channel_id: int, bot: DiscordBot, *, name: str | None = None, channel_type: str = "dm"
) -> tuple[DiscordChannel, bool]:
    """A DM as trusted data (the gateway, or the bot's token) shows it, and
    whether it is this bot's. A row held as a server's channel becomes this
    bot's DM, with the settings a new one has; one another bot of the same
    owner holds passes to this one. A DM another owner holds stays theirs:
    owners who registered the same application share its DMs' ids. Messages
    other owners' bots delivered stay, but are no longer theirs to change."""
    channel, created = get_or_insert(
        session, DiscordChannel, channel_id,
        id=channel_id, server_id=None, bot_id=bot.id, name=name or f"Channel {channel_id}", channel_type=channel_type,
    )
    if not created and channel.server_id is not None:
        channel.server_id, channel.bot_id, channel.channel_type, channel.parent_id = None, bot.id, channel_type, None
        reset_access(channel)
        session.execute(update(DiscordMessage).where(DiscordMessage.channel_id == channel_id).values(server_id=None))
        session.flush()
        session.expire(channel, ["server", "parent", "bot"])
        disown_others(session, channel_id, bot)
    elif not created and channel.bot_id != bot.id:
        holder = channel.bot.created_by_user_id if channel.bot else None
        if holder is None or holder != bot.created_by_user_id:
            return channel, False
        channel.bot_id = bot.id
        session.flush()
        session.expire(channel, ["bot"])
        disown_others(session, channel_id, bot)
    if name:
        channel.name = name
    return channel, True


def disown_others(session: DBSession, channel_id: int, bot: DiscordBot) -> None:
    """Take the messages of a DM this bot now holds from other owners' bots,
    and have its token check them."""
    ids = session.scalars(
        update(DiscordMessage)
        .where(DiscordMessage.channel_id == channel_id, DiscordMessage.bot_id.not_in(owner_bot_ids(session, bot)))
        .values(bot_id=None)
        .returning(DiscordMessage.id)
    ).all()
    verify_disowned(bot.id, ids)


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


def holds_server(session: DBSession, server_id: int, bot_ids: set[int]) -> bool:
    """Whether the owner of these bots holds a server, or would by pushing
    into it first. Only the holder's pushes change a server: a member's push
    could forge its authors and text, or take ownership of a real message."""
    server = session.get(DiscordServer, server_id)
    return server is None or server.bot_id in bot_ids


def held_by_other(session: DBSession, message_ids: list[int], bot_ids: set[int]) -> bool:
    """Whether any of these stored messages is in a server another owner
    holds and these bots' owner may push into. To an outsider such a message
    stays unknown: ``may_change`` keeps it from them."""
    servers = session.scalars(
        select(DiscordChannel.server_id)
        .join(DiscordMessage, DiscordMessage.channel_id == DiscordChannel.id)
        .where(DiscordMessage.message_id.in_(message_ids))
        .distinct()
    ).all()
    return any(
        may_push_into(session, server_id, bot_ids) and not holds_server(session, server_id, bot_ids)
        for server_id in servers
        if server_id is not None
    )


def push_refusal(
    session: DBSession, channel_id: int, server_id: int | None, bot_ids: set[int], parent_id: int | None = None
) -> str | None:
    """Why a push about this channel (from the owner of ``bot_ids``) is
    refused, or None. A push is untrusted: it may only touch a stored row in a
    server it may push into, or a DM of its owner's, and only as that row
    stands (its stored server, never a pushed one); it may only create a row
    under a server it may push into, as a DM needs the bot's token to show it.
    Of a server it may push into, it may only touch one its owner holds. A
    thread's parent must be a channel of the same server."""
    channel = session.get(DiscordChannel, channel_id)
    if channel is not None and channel.server_id != server_id:
        return "guild_mismatch"
    if server_id is None and (channel is None or not channel.is_direct):
        return "no_server"
    if server_id is None and channel is not None and channel.bot_id not in bot_ids:
        return "not_owner"
    if server_id is not None and not may_push_into(session, server_id, bot_ids):
        return "not_member"
    if server_id is not None and not holds_server(session, server_id, bot_ids):
        return "held_by_other"
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


def may_change(session: DBSession, message: DiscordMessage, bot_ids: set[int]) -> bool:
    """Whether events from these bots may edit, delete or react to a stored
    message: one in a channel they own, or one they delivered, if in a server
    only while they may push into it."""
    channel = message.channel
    if channel is not None and owns_channel(channel, bot_ids):
        return True
    if message.bot_id not in bot_ids:
        return False
    return channel is None or channel.server_id is None or may_push_into(session, channel.server_id, bot_ids)


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
    session: DBSession,
    data: dict,
    bot_id: int,
    push_only: bool,
    *,
    trusted: bool = False,
    listed: dict | None = None,
) -> DiscordChannel:
    """Server, channel and author rows for a message; the channel as the
    bot's token ``listed`` it, when it did. Servers first seen through a
    push-only bot collect everything: that bot receives only what it was sent."""
    server_id = snowflake(data.get("guild_id"))
    if server_id is not None:
        ensure_server(session, server_id, bot_id, collect=push_only)
    if listed is not None:
        channel = ensure_listed_channel(session, listed, server_id, bot_id, trusted=True)
    else:
        channel = ensure_channel(session, int(data["channel_id"]), server_id, bot_id, trusted=trusted)
    ensure_user(session, data["author"])
    return channel


def ensure_listed_channel(
    session: DBSession, data: dict, server_id: int | None, bot_id: int, *, trusted: bool = False
) -> DiscordChannel:
    """A channel or thread object, from the API (``trusted``) or a push. Only
    trusted data retypes a stored channel; a push may re-parent only a thread
    its bot owns. Trusted data placing a channel in a server shows the bot is
    in it, so a push its token confirmed makes its owner a member."""
    channel_type = CHANNEL_TYPE_NAMES.get(data.get("type", 0), "text")
    if trusted and server_id is not None:
        insert_missing(session, DiscordBotServer, bot_id=bot_id, server_id=server_id)
    existed = session.get(DiscordChannel, int(data["id"])) is not None
    channel = ensure_channel(
        session, int(data["id"]), server_id, bot_id, name=data.get("name"), channel_type=channel_type, trusted=trusted
    )
    if existed and not trusted and not owns_channel(channel, {bot_id}):
        return channel
    if trusted:
        # A thread first stored from a message event was taken for a text channel.
        channel.channel_type = channel_type
    parent_id = snowflake(data.get("parent_id"))
    if channel.channel_type == "thread":
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
