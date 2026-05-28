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
from memory.common.db.models import DiscordChannel, DiscordServer, DiscordUser


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


def ensure_server(session: DBSession, guild: Guild, bot_id: int | None = None) -> DiscordServer:
    """Ensure a Discord server record exists.

    Args:
        session: Database session.
        guild: Discord Guild object.
        bot_id: ID of the bot that is registering this server.  Stored on
            first creation so that the API can scope visibility to the
            servers each user's bots are in.
    """
    server = session.get(DiscordServer, guild.id)
    if server is None:
        server = DiscordServer(
            id=guild.id,
            name=guild.name or f"Server {guild.id}",
            description=getattr(guild, "description", None),
            member_count=getattr(guild, "member_count", None),
            bot_id=bot_id,
        )
        session.add(server)
        session.flush()
    else:
        if guild.name and server.name != guild.name:
            server.name = guild.name
        description = getattr(guild, "description", None)
        if description and server.description != description:
            server.description = description
        member_count = getattr(guild, "member_count", None)
        if member_count is not None:
            server.member_count = member_count
        # Back-fill bot_id if not yet set
        if server.bot_id is None and bot_id is not None:
            server.bot_id = bot_id
    return server


def ensure_channel(
    session: DBSession,
    channel: abc.Messageable,
    guild_id: int | None,
) -> DiscordChannel:
    """Ensure a Discord channel record exists."""
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        raise ValueError("Channel is missing an identifier.")

    # Get category_id if available (TextChannel, VoiceChannel have it)
    category_id = getattr(channel, "category_id", None)

    channel_model = session.get(DiscordChannel, channel_id)
    if channel_model is None:
        channel_model = DiscordChannel(
            id=channel_id,
            server_id=guild_id,
            category_id=category_id,
            name=getattr(channel, "name", f"Channel {channel_id}"),
            channel_type=get_channel_type(channel),
        )
        session.add(channel_model)
        session.flush()
    else:
        name = getattr(channel, "name", None)
        if name and channel_model.name != name:
            channel_model.name = name
        # Update category_id if changed
        if category_id is not None and channel_model.category_id != category_id:
            channel_model.category_id = category_id
    return channel_model


def ensure_user(session: DBSession, discord_user: abc.User) -> DiscordUser:
    """Ensure a Discord user record exists."""
    user = session.get(DiscordUser, discord_user.id)
    display_name = getattr(discord_user, "display_name", discord_user.name)
    if user is None:
        user = DiscordUser(
            id=discord_user.id,
            username=discord_user.name,
            display_name=display_name,
        )
        session.add(user)
        session.flush()
    else:
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
    guild_id = message.guild.id if message.guild else None
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
        "server_id": guild_id,
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
    channel_model = ensure_channel(session, message.channel, guild_id)
    ensure_user(session, message.author)
    return channel_model


def queue_message(celery_app: Celery, message: Message, bot_id: int, is_edit: bool = False) -> None:
    """Queue a Message for storage/embedding via the shared ADD_DISCORD_MESSAGE task."""
    celery_app.send_task(
        ADD_DISCORD_MESSAGE,
        kwargs=build_message_task_kwargs(message, bot_id, is_edit),
    )
