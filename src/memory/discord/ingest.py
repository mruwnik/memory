# pyright: reportAttributeAccessIssue=false
"""Discord ingestion helpers shared by the live collector and the history
backfill: turn discord.py entities/messages into DB rows or queued tasks.

Holds no gateway/bot code (no ``discord.ext.commands``), so it's safe to import
from the Celery worker as well as from the collector.
"""
from celery import Celery

from discord import (
    DMChannel,
    GroupChannel,
    Guild,
    Message,
    MessageType,
    TextChannel,
    Thread,
    VoiceChannel,
    abc,
)

from memory.common.celery_app import ADD_DISCORD_MESSAGE
from memory.common.db.connection import DBSession
from memory.common.db.models import DiscordBot, DiscordBotServer, DiscordChannel, DiscordServer, DiscordUser
from memory.common.db.upsert import get_or_insert, insert_missing
from memory.common.discord_events import claim_dm, knows_user, move_to_server, set_thread_parent, verify_server


def get_channel_type(channel: abc.Messageable) -> str:
    """Determine the type of a Discord channel."""
    if isinstance(channel, DMChannel):
        return "dm"
    if isinstance(channel, GroupChannel):
        return "group_dm"
    if isinstance(channel, Thread):
        return "thread"
    if isinstance(channel, VoiceChannel):
        return "voice"
    if isinstance(channel, TextChannel):
        return "text"
    return getattr(getattr(channel, "type", None), "name", "unknown")


def update_server(session: DBSession, server: DiscordServer, guild: Guild, bot_id: int | None) -> None:
    if bot_id is not None and (not server.verified or server.bot_id is None):
        # A gateway connection proves membership; a server claimed only by
        # pushed events, or left by its owning bot, starts over under this bot.
        verify_server(session, guild.id, bot_id, collect=server.collect_messages, name=guild.name)
        return
    if guild.name and server.name != guild.name:
        server.name = guild.name
    description = getattr(guild, "description", None)
    if description and server.description != description:
        server.description = description
    member_count = getattr(guild, "member_count", None)
    if member_count is not None:
        server.member_count = member_count


def ensure_server(session: DBSession, guild: Guild, bot_id: int | None = None) -> DiscordServer:
    """Ensure a Discord server record exists.

    Args:
        session: Database session.
        guild: Discord Guild object.
        bot_id: ID of the bot that is registering this server.  Stored on
            first creation so that the API can scope visibility to the
            servers each user's bots are in.
    """
    server, created = get_or_insert(
        session,
        DiscordServer,
        guild.id,
        id=guild.id,
        name=guild.name or f"Server {guild.id}",
        description=getattr(guild, "description", None),
        member_count=getattr(guild, "member_count", None),
        bot_id=bot_id,
        verified=bot_id is not None,
    )
    if not created:
        update_server(session, server, guild, bot_id)
    if bot_id is not None:
        insert_missing(session, DiscordBotServer, bot_id=bot_id, server_id=guild.id)
    return server


def channel_name(channel: abc.Messageable) -> str | None:
    """A DM has no name of its own, so it is named after the other end."""
    if isinstance(channel, DMChannel):
        return f"DM with {channel.recipient.name}" if channel.recipient else None
    return getattr(channel, "name", None)


def untracked_dm(session: DBSession, channel: abc.Messageable, bot_id: int) -> bool:
    """A DM not stored yet whose other end shares no server with the bot, as
    the gateway's member cache or the authors stored in its servers show:
    strangers' DMs stay unseen."""
    if not isinstance(channel, (DMChannel, GroupChannel)) or session.get(DiscordChannel, channel.id) is not None:
        return False
    people = [channel.recipient] if isinstance(channel, DMChannel) else list(channel.recipients)
    people = [person for person in people if person is not None]
    if any(getattr(person, "mutual_guilds", None) for person in people):
        return False
    return not knows_user(session, bot_id, [person.id for person in people])


def ensure_channel(
    session: DBSession,
    channel: abc.Messageable,
    guild_id: int | None,
    bot_id: int | None = None,
) -> DiscordChannel:
    """Ensure a Discord channel record exists; a new one records the bot that saw it."""
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        raise ValueError("Channel is missing an identifier.")
    bot = session.get(DiscordBot, bot_id) if bot_id is not None else None
    if isinstance(channel, (DMChannel, GroupChannel)) and bot is not None:
        # The caller checks the result is this bot's: another owner's DM stays theirs.
        return claim_dm(session, channel_id, bot, name=channel_name(channel), channel_type=get_channel_type(channel))[0]

    # Get category_id if available (TextChannel, VoiceChannel have it)
    category_id = getattr(channel, "category_id", None)
    parent_id = channel.parent_id if isinstance(channel, Thread) else None

    channel_model, created = get_or_insert(
        session,
        DiscordChannel,
        channel_id,
        id=channel_id,
        server_id=guild_id,
        bot_id=bot_id,
        category_id=category_id,
        parent_id=parent_id,
        name=channel_name(channel) or f"Channel {channel_id}",
        channel_type=get_channel_type(channel),
    )
    if created:
        return channel_model
    if guild_id is not None and channel_model.server_id != guild_id:
        # The gateway's word beats a push that stored this id as a DM or under another server.
        move_to_server(session, channel_model, guild_id, bot_id, get_channel_type(channel))
    name = channel_name(channel)
    if name and channel_model.name != name:
        channel_model.name = name
    if category_id is not None and channel_model.category_id != category_id:
        channel_model.category_id = category_id
    set_thread_parent(session, channel_model, parent_id)
    return channel_model


def ensure_user(session: DBSession, discord_user: abc.User) -> DiscordUser:
    """Ensure a Discord user record exists."""
    display_name = getattr(discord_user, "display_name", discord_user.name)
    user, created = get_or_insert(
        session, DiscordUser, discord_user.id, id=discord_user.id, username=discord_user.name, display_name=display_name
    )
    if created:
        return user
    if user.username != discord_user.name:
        user.username = discord_user.name
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    return user


def should_collect(channel: DiscordChannel) -> bool:
    """Check if messages should be collected for this channel."""
    return channel.should_collect


def get_message_type(message: Message) -> str:
    """Classify a message. Shared by live ingestion and history backfill."""
    if message.reference:
        return "reply"
    if getattr(message, "thread", None):
        return "thread_starter"
    if message.type != MessageType.default:
        return "system"
    return "default"


def build_message_task_kwargs(message: Message, bot_id: int, is_edit: bool = False) -> dict:
    """Reduce a discord.py Message to the plain kwargs ``ADD_DISCORD_MESSAGE`` expects.

    Shared by the live collector (``on_message``) and the history backfill so the
    two paths can never drift in how a Message maps to a stored row.
    """
    images = [
        a.url
        for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]
    embeds = [embed.to_dict() for embed in message.embeds] if message.embeds else None
    attachments = [
        {
            "filename": a.filename,
            "content_type": a.content_type,
            "size": a.size,
            "url": a.url,
        }
        for a in message.attachments
        if not (a.content_type and a.content_type.startswith("image/"))
    ]
    return {
        "bot_id": bot_id,
        "message_id": message.id,
        "channel_id": message.channel.id,
        "author_id": message.author.id,
        "content": message.content,
        "sent_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "reply_to_message_id": (
            message.reference.message_id if message.reference else None
        ),
        "thread_id": getattr(getattr(message, "thread", None), "id", None),
        "message_type": get_message_type(message),
        "is_pinned": message.pinned,
        "images": images or None,
        "embeds": embeds,
        "attachments": attachments or None,
        "is_edit": is_edit,
    }


def ensure_message_entities(session: DBSession, message: Message, bot_id: int | None = None) -> DiscordChannel:
    """Ensure server/channel/user rows exist for a message; return the channel.

    ``ADD_DISCORD_MESSAGE`` has NOT NULL FKs on ``channel_id``/``author_id`` and
    does not create those rows, so callers must run this (and commit) before
    queuing. Shared by live ingestion and backfill.
    """
    guild_id = message.guild.id if message.guild else None
    if message.guild:
        ensure_server(session, message.guild, bot_id=bot_id)
    channel_model = ensure_channel(session, message.channel, guild_id, bot_id)
    ensure_user(session, message.author)
    return channel_model


def queue_message(celery_app: Celery, message: Message, bot_id: int, is_edit: bool = False) -> None:
    """Queue a Message for storage/embedding via the shared ADD_DISCORD_MESSAGE task."""
    celery_app.send_task(
        ADD_DISCORD_MESSAGE,
        kwargs=build_message_task_kwargs(message, bot_id, is_edit),
    )
