# pyright: reportAttributeAccessIssue=false
"""
Discord message collector bot.

This module provides a Discord bot that:
- Listens for messages in configured channels/servers
- Queues messages for storage and embedding via Celery

Sends go over the REST API (``memory.common.discord``), so they work whether or
not a bot's gateway connection is up.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from discord import (
    ConnectionClosed,
    Guild,
    Intents,
    LoginFailure,
    Member,
    Message,
    PrivilegedIntentsRequired,
    RawBulkMessageDeleteEvent,
    RawMessageDeleteEvent,
    RawMessageUpdateEvent,
    RawReactionActionEvent,
    RawReactionClearEmojiEvent,
    RawReactionClearEvent,
    abc,
)
import yarl
from discord.ext import commands
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.common.celery_app import BACKFILL_DISCORD_CHANNEL, HANDLE_DISCORD_EVENT, UPDATE_REACTIONS
from memory.common.db.connection import make_session
from memory.common.discord_events import emoji_key as event_emoji_key
from memory.common.db.models import DiscordBot, DiscordBotServer, DiscordChannel
from memory.common.db.models.discord import DEFAULT_DISCORD_API_BASE_URL
from memory.discord.ingest import (
    ensure_channel,
    ensure_message_entities,
    ensure_server,
    ensure_user,
    queue_message,
    should_collect,
)
from memory.common.chat_urls import chat_url_allowed
from memory.discord.proxied_http import ProxiedHTTPClient
from memory.discord.rest import bot_access

if TYPE_CHECKING:
    from celery import Celery


def emoji_key(emoji) -> str | None:
    return event_emoji_key({"id": emoji.id, "name": emoji.name})


@dataclass(frozen=True)
class BotInfo:
    """What a collector runs with, detached from the session; a change restarts it."""

    id: int
    name: str
    token: str
    api_base_url: str = DEFAULT_DISCORD_API_BASE_URL


logger = logging.getLogger(__name__)

# How often the collector picks up bots added, paused or switched between modes.
SYNC_INTERVAL_SECONDS = 60
MAX_RETRY_DELAY_SECONDS = 60 * 60
# Gateway closes that recur until the bot's owner changes something: auth
# failed, invalid shard, sharding required, invalid version or intents.
REFUSED_CLOSE_CODES = {4004, 4010, 4011, 4012, 4013}


@dataclass(frozen=True)
class Hold:
    """A bot not restarted before ``until`` unless its settings change.
    Every start costs an IDENTIFY, and Discord resets the token of a bot that
    sends 1000 in a day."""

    info: BotInfo
    until: float = math.inf
    crashes: int = 0


def refused(error: Exception) -> bool:
    if isinstance(error, LoginFailure):
        return True
    return isinstance(error, ConnectionClosed) and error.code in REFUSED_CLOSE_CODES


def gateway_allowed(bot_id: int, url: str) -> bool:
    """A gateway URL is held to the chat API allowlist, as the bot's owner
    stands now, like its API URL."""
    with make_session() as session:
        bot = session.get(DiscordBot, bot_id)
        if bot is None:
            return False
        return chat_url_allowed(str(yarl.URL(url).with_scheme("https")), bot.owner)


def gateway_bots(session: Session) -> list[BotInfo]:
    """Active gateway-mode bots with a token and an allowed API URL."""
    bots = session.query(DiscordBot).filter_by(is_active=True, realtime="gateway").order_by(DiscordBot.id)
    return [
        BotInfo(id=bot.id, name=bot.name, token=access.token, api_base_url=access.api_base_url)
        for bot in bots
        if (access := bot_access(bot))
    ]


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

        proxied = bot_info.api_base_url.rstrip("/") != DEFAULT_DISCORD_API_BASE_URL
        # READY waits for member chunks, which a proxy may serve slowly or never. Without them
        # members still come with each GUILD_CREATE (all of them in small servers) and as authors.
        super().__init__(command_prefix="!", intents=intents, chunk_guilds_at_startup=not proxied)
        if proxied:
            self.http = ProxiedHTTPClient(
                bot_info.api_base_url, lambda url: gateway_allowed(bot_info.id, url), self.http.loop
            )
            self._connection.http = self.http

        self.bot_info = bot_info
        self.celery_app = celery_app

    async def on_ready(self) -> None:
        # Each server's GUILD_CREATE, at startup too, records it (on_guild_available/on_guild_join).
        logger.info(f"Bot {self.user} (ID: {self.bot_info.id}) is ready")

    async def on_guild_available(self, guild: Guild) -> None:
        await self.sync_guilds([guild])

    async def on_guild_join(self, guild: Guild) -> None:
        await self.sync_guilds([guild])

    async def on_guild_remove(self, guild: Guild) -> None:
        await asyncio.to_thread(self.forget_guild, guild.id)

    async def sync_guild_metadata(self) -> dict[str, int]:
        return await self.sync_guilds(self.guilds)

    async def sync_guilds(self, guilds: Sequence[Guild]) -> dict[str, int]:
        """Record these servers as this bot's, with their channels and the members
        discord.py holds. The database work runs off the event loop that every bot
        and the health API share."""
        snapshot = [
            (guild, [c for c in guild.channels if isinstance(c, abc.Messageable)], guild.members) for guild in guilds
        ]
        return await asyncio.to_thread(self.store_guilds, snapshot)

    def store_guilds(self, guilds: Sequence[tuple[Guild, Sequence[abc.Messageable], Sequence[Member]]]) -> dict[str, int]:
        counts = {"servers": 0, "channels": 0, "users": 0}
        with make_session() as session:
            for guild, channels, members in guilds:
                ensure_server(session, guild, bot_id=self.bot_info.id)
                counts["servers"] += 1
                for channel in channels:
                    ensure_channel(session, channel, guild.id)
                    counts["channels"] += 1
                for member in members:
                    ensure_user(session, member)
                    counts["users"] += 1
            if bot := session.get(DiscordBot, self.bot_info.id):
                bot.last_sync_at = datetime.now(timezone.utc)
            session.commit()
        logger.info(f"Bot {self.bot_info.id} synced {counts}")
        return counts

    def forget_guild(self, guild_id: int) -> None:
        with make_session() as session:
            session.query(DiscordBotServer).filter_by(bot_id=self.bot_info.id, server_id=guild_id).delete()
            session.commit()

    async def on_message(self, message: Message) -> None:
        """Handle incoming messages (including our own for complete history)."""
        try:
            await self._process_message(message)
        except Exception:
            logger.exception(f"Error processing message {message.id}")

    async def on_raw_message_edit(self, payload: RawMessageUpdateEvent) -> None:
        self.queue_event("MESSAGE_UPDATE", payload.data)

    async def on_raw_message_delete(self, payload: RawMessageDeleteEvent) -> None:
        self.queue_event("MESSAGE_DELETE", {"id": str(payload.message_id)})

    async def on_raw_bulk_message_delete(self, payload: RawBulkMessageDeleteEvent) -> None:
        self.queue_event("MESSAGE_DELETE_BULK", {"ids": [str(i) for i in sorted(payload.message_ids)]})

    def queue_event(self, event_type: str, data: Mapping[str, Any]) -> None:
        """Hand a gateway event to the task webhook deliveries use, which handles
        partial and stale edits and only touches messages this bot's owner may change."""
        self.celery_app.send_task(
            HANDLE_DISCORD_EVENT, kwargs={"bot_id": self.bot_info.id, "event_type": event_type, "data": data}
        )

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
        self._queue_reaction(payload.message_id, "add", emoji_key(payload.emoji), payload.user_id)

    async def on_raw_reaction_remove(self, payload: RawReactionActionEvent) -> None:
        self._queue_reaction(payload.message_id, "remove", emoji_key(payload.emoji), payload.user_id)

    async def on_raw_reaction_clear(self, payload: RawReactionClearEvent) -> None:
        self._queue_reaction(payload.message_id, "clear")

    async def on_raw_reaction_clear_emoji(self, payload: RawReactionClearEmojiEvent) -> None:
        self._queue_reaction(payload.message_id, "clear_emoji", emoji_key(payload.emoji))

    def _queue_reaction(
        self,
        message_id: int,
        action: str,
        emoji: str | None = None,
        user_id: int | None = None,
    ) -> None:
        self.celery_app.send_task(
            UPDATE_REACTIONS,
            kwargs={
                "message_id": message_id,
                "action": action,
                "emoji": emoji,
                "user_id": str(user_id) if user_id is not None else None,
            },
        )

    async def _process_message(self, message: Message) -> None:
        """Process a message - check collection settings and queue for storage."""
        with make_session() as session:
            channel_model = ensure_message_entities(
                session, message, self.bot_info.id
            )
            if not should_collect(channel_model):
                return
            session.commit()

        queue_message(self.celery_app, message, self.bot_info.id)
        logger.debug(f"Queued message {message.id} for processing")


class CollectorManager:
    """Manages multiple Discord bot collectors."""

    def __init__(self, celery_app: Celery):
        self.celery_app = celery_app
        self.collectors: dict[int, MessageCollector] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self.held: dict[int, Hold] = {}

    async def sync(self) -> None:
        """Run exactly the bots that should be on the gateway, restarting any
        whose token, name or API URL changed."""
        with make_session() as session:
            wanted = {info.id: info for info in gateway_bots(session)}
        # Pausing and resuming a bot is how its owner asks for a retry.
        self.held = {bot_id: hold for bot_id, hold in self.held.items() if bot_id in wanted}
        for bot_id, collector in list(self.collectors.items()):
            if wanted.get(bot_id) != collector.bot_info:
                await self.stop_bot(bot_id)
        for bot_id, info in wanted.items():
            hold = self.held.get(bot_id)
            if bot_id in self.collectors or (hold and hold.info == info and monotonic() < hold.until):
                continue
            await self.start_bot(info)

    async def keep_synced(self) -> None:
        while True:
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)
            try:
                await self.sync()
            except Exception:
                logger.exception("Discord collector sync failed")

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
        except Exception as e:
            if isinstance(e, PrivilegedIntentsRequired):
                # Intents are switched on in the developer portal, which changes nothing we can see.
                logger.error(f"Bot {bot_info.id} lacks privileged intents; retrying in an hour")
                self.held[bot_info.id] = Hold(bot_info, monotonic() + MAX_RETRY_DELAY_SECONDS)
            elif refused(e):
                logger.error(f"Discord refused bot {bot_info.id}; waiting for its settings to change")
                self.held[bot_info.id] = Hold(bot_info)
            else:
                logger.exception(f"Bot {bot_info.id} crashed")
                # close() clears READY, so read it first.
                self.hold_after_crash(bot_info, collector.is_ready())
            # discord.py leaves its HTTP session open on login and intents failures.
            await collector.close()
        finally:
            # A restart may already have put a new collector under this id.
            if self.collectors.get(bot_info.id) is collector:
                self.collectors.pop(bot_info.id, None)
                self._tasks.pop(bot_info.id, None)

    def hold_after_crash(self, bot_info: BotInfo, was_ready: bool) -> None:
        """Back off exponentially, starting over once a bot has reached READY."""
        previous = self.held.get(bot_info.id)
        crashes = 1
        if previous and previous.info == bot_info and not was_ready:
            crashes = previous.crashes + 1
        delay = min(SYNC_INTERVAL_SECONDS * 2 ** (crashes - 1), MAX_RETRY_DELAY_SECONDS)
        self.held[bot_info.id] = Hold(bot_info, monotonic() + delay, crashes)

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
