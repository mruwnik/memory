"""
Discord message collector bot.

This module provides a Discord bot that:
- Listens for messages in configured channels/servers
- Queues messages for storage and embedding via Celery
- Provides methods for sending messages (used by MCP tools)
"""
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from sqlalchemy.orm import Session
from discord.ext import commands

from memory.common.db.connection import make_session
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    DiscordUser,
)
from memory.common.celery_app import ADD_DISCORD_MESSAGE

if TYPE_CHECKING:
    from celery import Celery


@dataclass
class BotInfo:
    """Simple data holder for bot info to avoid SQLAlchemy session issues."""

    id: int
    name: str
    token: str

logger = logging.getLogger(__name__)


def get_channel_type(channel: discord.abc.Messageable) -> str:
    """Determine the type of a Discord channel."""
    if isinstance(channel, discord.DMChannel):
        return "dm"
    if isinstance(channel, discord.GroupChannel):
        return "group_dm"
    if isinstance(channel, discord.Thread):
        return "thread"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.TextChannel):
        return "text"
    return getattr(getattr(channel, "type", None), "name", "unknown")


def ensure_server(session: Session, guild: discord.Guild) -> DiscordServer:
    """Ensure a Discord server record exists."""
    server = session.get(DiscordServer, guild.id)
    if server is None:
        server = DiscordServer(
            id=guild.id,
            name=guild.name or f"Server {guild.id}",
            description=getattr(guild, "description", None),
            member_count=getattr(guild, "member_count", None),
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
    return server


def ensure_channel(
    session: Session,
    channel: discord.abc.Messageable,
    guild_id: int | None,
) -> DiscordChannel:
    """Ensure a Discord channel record exists."""
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        raise ValueError("Channel is missing an identifier.")

    channel_model = session.get(DiscordChannel, channel_id)
    if channel_model is None:
        channel_model = DiscordChannel(
            id=channel_id,
            server_id=guild_id,
            name=getattr(channel, "name", f"Channel {channel_id}"),
            channel_type=get_channel_type(channel),
        )
        session.add(channel_model)
        session.flush()
    else:
        name = getattr(channel, "name", None)
        if name and channel_model.name != name:
            channel_model.name = name
    return channel_model


def ensure_user(session: Session, discord_user: discord.abc.User) -> DiscordUser:
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


class MessageCollector(commands.Bot):
    """Discord bot that collects messages for the knowledge base.

    This bot:
    - Listens for messages in channels where collection is enabled
    - Creates/updates Discord entity records (servers, channels, users)
    - Queues messages for async processing via Celery
    """

    def __init__(self, bot_info: BotInfo, celery_app: Celery):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)

        self.bot_info = bot_info
        self.celery_app = celery_app
        self._ready = asyncio.Event()

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        logger.info(f"Bot {self.user} (ID: {self.bot_info.id}) is ready")
        self._ready.set()

    async def wait_until_ready(self) -> None:
        """Wait until the bot is ready."""
        await self._ready.wait()

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        # Ignore our own messages
        if message.author.id == self.bot_info.id:
            return

        # Ignore bot messages (optional, could be configurable)
        if message.author.bot:
            return

        try:
            await self._process_message(message)
        except Exception:
            logger.exception(f"Error processing message {message.id}")

    async def on_message_edit(
        self, _before: discord.Message, after: discord.Message
    ) -> None:
        """Handle message edits."""
        if after.author.id == self.bot_info.id:
            return

        try:
            await self._process_message(after, is_edit=True)
        except Exception:
            logger.exception(f"Error processing message edit {after.id}")

    async def _process_message(
        self, message: discord.Message, is_edit: bool = False
    ) -> None:
        """Process a message - check collection settings and queue for storage."""
        with make_session() as session:
            # Ensure entities exist
            guild_id = message.guild.id if message.guild else None
            if message.guild:
                ensure_server(session, message.guild)

            channel_model = ensure_channel(session, message.channel, guild_id)
            ensure_user(session, message.author)

            # Check if we should collect for this channel
            if not should_collect(channel_model):
                return

            session.commit()

        # Extract image URLs from attachments
        images = [
            a.url
            for a in message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]

        # Extract embed data
        embeds = [embed.to_dict() for embed in message.embeds] if message.embeds else None

        # Extract non-image attachments
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

        # Queue for Celery processing
        self.celery_app.send_task(
            ADD_DISCORD_MESSAGE,
            kwargs={
                "bot_id": self.bot_info.id,
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
                "thread_id": (
                    message.thread.id
                    if hasattr(message, "thread") and message.thread
                    else None
                ),
                "message_type": self._get_message_type(message),
                "is_pinned": message.pinned,
                "images": images or None,
                "embeds": embeds,
                "attachments": attachments or None,
                "is_edit": is_edit,
            },
        )

        logger.debug(f"Queued message {message.id} for processing")

    def _get_message_type(self, message: discord.Message) -> str:
        """Determine the message type."""
        if message.reference:
            return "reply"
        if hasattr(message, "thread") and message.thread:
            return "thread_starter"
        if message.type != discord.MessageType.default:
            return "system"
        return "default"

    async def send_message(self, channel_id: int, content: str) -> bool:
        """Send a message to a channel."""
        try:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            if channel and hasattr(channel, "send"):
                await channel.send(content)
                return True
        except Exception:
            logger.exception(f"Failed to send message to channel {channel_id}")
        return False

    async def send_dm(self, user_id: int, content: str) -> bool:
        """Send a DM to a user."""
        try:
            user = self.get_user(user_id)
            if user is None:
                user = await self.fetch_user(user_id)
            if user:
                await user.send(content)
                return True
        except Exception:
            logger.exception(f"Failed to send DM to user {user_id}")
        return False

    async def add_reaction(
        self, channel_id: int, message_id: int, emoji: str
    ) -> bool:
        """Add a reaction to a message."""
        try:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            if channel and hasattr(channel, "fetch_message"):
                message = await channel.fetch_message(message_id)
                await message.add_reaction(emoji)
                return True
        except Exception:
            logger.exception(
                f"Failed to add reaction to message {message_id} in channel {channel_id}"
            )
        return False


class CollectorManager:
    """Manages multiple Discord bot collectors."""

    def __init__(self, celery_app: Celery):
        self.celery_app = celery_app
        self.collectors: dict[int, MessageCollector] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    async def start_all(self) -> None:
        """Start all active bots."""
        bot_infos: list[BotInfo] = []
        with make_session() as session:
            bots = session.query(DiscordBot).filter_by(is_active=True).all()
            for bot in bots:
                token = bot.token
                if token:
                    bot_infos.append(BotInfo(id=bot.id, name=bot.name, token=token))

        for bot_info in bot_infos:
            await self.start_bot(bot_info)

    async def start_bot(self, bot_info: BotInfo) -> None:
        """Start a single bot."""
        if bot_info.id in self.collectors:
            logger.warning(f"Bot {bot_info.id} already running")
            return

        collector = MessageCollector(bot_info, self.celery_app)
        self.collectors[bot_info.id] = collector

        # Start in background task
        task = asyncio.create_task(self._run_bot(bot_info, collector))
        self._tasks[bot_info.id] = task

        logger.info(f"Started bot {bot_info.id} ({bot_info.name})")

    async def _run_bot(self, bot_info: BotInfo, collector: MessageCollector) -> None:
        """Run a bot until stopped."""
        try:
            await collector.start(bot_info.token)
        except Exception:
            logger.exception(f"Bot {bot_info.id} crashed")
        finally:
            self.collectors.pop(bot_info.id, None)
            self._tasks.pop(bot_info.id, None)

    async def stop_bot(self, bot_id: int) -> None:
        """Stop a single bot."""
        collector = self.collectors.get(bot_id)
        if collector:
            await collector.close()
            self.collectors.pop(bot_id, None)

        task = self._tasks.get(bot_id)
        if task:
            task.cancel()
            self._tasks.pop(bot_id, None)

    async def stop_all(self) -> None:
        """Stop all bots."""
        for bot_id in list(self.collectors.keys()):
            await self.stop_bot(bot_id)

    def get_collector(self, bot_id: int) -> MessageCollector | None:
        """Get a collector by bot ID."""
        return self.collectors.get(bot_id)

    async def send_message(
        self, bot_id: int, channel_id: int, content: str
    ) -> bool:
        """Send a message using a specific bot."""
        collector = self.get_collector(bot_id)
        if collector:
            return await collector.send_message(channel_id, content)
        return False

    async def send_dm(self, bot_id: int, user_id: int, content: str) -> bool:
        """Send a DM using a specific bot."""
        collector = self.get_collector(bot_id)
        if collector:
            return await collector.send_dm(user_id, content)
        return False
