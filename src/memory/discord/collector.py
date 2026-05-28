# pyright: reportAttributeAccessIssue=false
"""
Discord message collector bot.

This module provides a Discord bot that:
- Listens for messages in configured channels/servers
- Queues messages for storage and embedding via Celery
- Provides methods for sending messages (used by MCP tools)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discord import (
    Intents,
    Message,
    RawReactionActionEvent,
    abc,
)
from discord.ext import commands
from sqlalchemy.exc import IntegrityError

from memory.common.celery_app import BACKFILL_DISCORD_CHANNEL, UPDATE_REACTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordBot, DiscordChannel
from memory.discord.ingest import (
    ensure_channel,
    ensure_message_entities,
    ensure_server,
    ensure_user,
    queue_message,
    should_collect,
)

if TYPE_CHECKING:
    from celery import Celery


@dataclass
class BotInfo:
    """Simple data holder for bot info to avoid SQLAlchemy session issues."""

    id: int
    name: str
    token: str

logger = logging.getLogger(__name__)


class MessageCollector(commands.Bot):
    """Discord bot that collects messages for the knowledge base.

    This bot:
    - Listens for messages in channels where collection is enabled
    - Creates/updates Discord entity records (servers, channels, users)
    - Queues messages for async processing via Celery
    """

    def __init__(self, bot_info: BotInfo, celery_app: Celery):
        intents = Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.reactions = True

        super().__init__(command_prefix="!", intents=intents)

        self.bot_info = bot_info
        self.celery_app = celery_app
        self._ready = asyncio.Event()

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        logger.info(f"Bot {self.user} (ID: {self.bot_info.id}) is ready")
        self._ready.set()

        # Sync all guild members to database
        await self._sync_guild_metadata()

    async def wait_until_ready(self) -> None:
        """Wait until the bot is ready."""
        await self._ready.wait()

    async def _sync_guild_metadata(self) -> None:
        """Sync all servers, channels, and users to the database."""
        user_count = 0
        server_count = 0
        channel_count = 0

        with make_session() as session:
            for guild in self.guilds:
                ensure_server(session, guild, bot_id=self.bot_info.id)
                server_count += 1

                for channel in guild.channels:
                    if hasattr(channel, "send"):  # Text-like channels
                        ensure_channel(session, channel, guild.id)  # type: ignore[arg-type]
                        channel_count += 1

                for member in guild.members:
                    ensure_user(session, member)
                    user_count += 1

            session.commit()

        logger.info(
            f"Synced metadata: {server_count} servers, "
            f"{channel_count} channels, {user_count} users"
        )

    async def on_message(self, message: Message) -> None:
        """Handle incoming messages (including our own for complete history)."""
        try:
            await self._process_message(message)
        except Exception:
            logger.exception(f"Error processing message {message.id}")

    async def on_message_edit(
        self, _before: Message, after: Message
    ) -> None:
        """Handle message edits (including our own for complete history)."""
        try:
            await self._process_message(after, is_edit=True)
        except Exception:
            logger.exception(f"Error processing message edit {after.id}")

    async def on_guild_channel_create(self, channel: abc.GuildChannel) -> None:
        """Persist newly-created text-like channels so name-based lookups work
        before any message lands, and immediately backfill if the channel is
        collectible (usually a no-op for brand-new channels, but pulls full
        history when collection is enabled on a channel that already existed on
        Discord). Categories are intentionally skipped.
        """
        if not hasattr(channel, "send"):
            return
        collect = False
        try:
            with make_session() as session:
                channel_model = ensure_channel(session, channel, channel.guild.id)  # type: ignore[arg-type]
                collect = should_collect(channel_model)
                session.commit()
        except IntegrityError:
            # Race with MCP's eager ensure_channel_record — the row is already
            # present, no work to do. Don't let it surface as an error.
            logger.debug(f"Channel {channel.id} already persisted (race with MCP)")
            return
        except Exception:
            logger.exception(f"Failed to persist created channel {channel.id}")
            return

        if collect:
            self.celery_app.send_task(
                BACKFILL_DISCORD_CHANNEL, args=[channel.id]
            )

    async def on_guild_channel_update(
        self, _before: abc.GuildChannel, after: abc.GuildChannel
    ) -> None:
        """Reflect renames and category-moves into the local DB so name-based
        lookups stay accurate."""
        if not hasattr(after, "send"):
            return
        try:
            with make_session() as session:
                ensure_channel(session, after, after.guild.id)  # type: ignore[arg-type]
                session.commit()
        except Exception:
            logger.exception(f"Failed to update channel {after.id}")

    async def on_guild_channel_delete(self, channel: abc.GuildChannel) -> None:
        """Drop the local row when a channel disappears on Discord.

        Channels with collected messages cannot be removed because
        ``DiscordMessage.channel_id`` has no ``ondelete`` rule — those rows
        are left in place (the messages they reference are still valuable
        history). Without an explicit soft-delete column, this is the
        conservative behavior.
        """
        try:
            with make_session() as session:
                record = session.get(DiscordChannel, channel.id)
                if record is None:
                    return
                session.delete(record)
                session.commit()
        except IntegrityError:
            logger.info(
                f"Channel {channel.id} kept in DB after Discord delete "
                f"(referenced by collected messages)"
            )
        except Exception:
            logger.exception(f"Failed to delete channel {channel.id}")

    async def on_raw_reaction_add(self, payload: RawReactionActionEvent) -> None:
        """Handle reaction additions."""
        await self._process_reaction_update(payload)

    async def on_raw_reaction_remove(
        self, payload: RawReactionActionEvent
    ) -> None:
        """Handle reaction removals."""
        await self._process_reaction_update(payload)

    async def _process_reaction_update(
        self, payload: RawReactionActionEvent
    ) -> None:
        """Fetch current reactions and queue update."""
        try:
            channel = self.get_channel(payload.channel_id)
            if channel is None:
                channel = await self.fetch_channel(payload.channel_id)
            if channel is None or not hasattr(channel, "fetch_message"):
                return

            message = await channel.fetch_message(payload.message_id)  # type: ignore[union-attr]

            # Build reactions list from message
            reactions = []
            for reaction in message.reactions:
                emoji_str = (
                    str(reaction.emoji)
                    if isinstance(reaction.emoji, str)
                    else reaction.emoji.name
                )
                reactions.append(
                    {
                        "emoji": emoji_str,
                        "count": reaction.count,
                    }
                )

            self.celery_app.send_task(
                UPDATE_REACTIONS,
                kwargs={
                    "message_id": payload.message_id,
                    "reactions": reactions,
                },
            )
        except Exception:
            logger.exception(f"Error processing reaction update for {payload.message_id}")

    async def _process_message(
        self, message: Message, is_edit: bool = False
    ) -> None:
        """Process a message - check collection settings and queue for storage."""
        with make_session() as session:
            channel_model = ensure_message_entities(
                session, message, self.bot_info.id
            )
            if not should_collect(channel_model):
                return
            session.commit()

        queue_message(self.celery_app, message, self.bot_info.id, is_edit)
        logger.debug(f"Queued message {message.id} for processing")

    async def send_message(self, channel_id: int, content: str) -> bool:
        """Send a message to a channel."""
        try:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            if channel and hasattr(channel, "send"):
                await channel.send(content)  # type: ignore[union-attr]
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
                message = await channel.fetch_message(message_id)  # type: ignore[union-attr]
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
