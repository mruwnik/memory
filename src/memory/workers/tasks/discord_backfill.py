"""Celery tasks for the daily Discord catch-up and history backfill.

Runs on the standard worker pool, which consumes the ``backfill`` queue
alongside its other queues. Each bot is read through its own REST API URL.
"""
import asyncio
import logging
import random
from typing import Any


from sqlalchemy import and_, func
from sqlalchemy.orm import aliased

from memory.common import settings
from memory.common.celery_app import (
    BACKFILL_DISCORD_CHANNEL,
    DISCOVER_DISCORD_SERVERS,
    DISCOVER_DISCORD_THREADS,
    SCHEDULE_DISCORD_BACKFILLS,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordServer,
)
from memory.common.db.models.discord import DM_CHANNEL_TYPES
from memory.common.jobs import tracked_task
from memory.common.redis_lock import distributed_lock
from memory.discord.backfill import (
    backfill_channel_messages,
    discover_bot_servers,
    discover_channel_threads,
)
from memory.discord.rest import BotAccess, DiscordAPIError, bot_access

logger = logging.getLogger(__name__)

# Safety timeout: if a run crashes mid-channel, the lock auto-expires so the
# next sweep can resume. Comfortably longer than one run's message budget.
BACKFILL_LOCK_TTL_SECONDS = 60 * 60

# Channel types that can contain threads / forum posts worth discovering.
THREAD_PARENT_TYPES = frozenset({"text", "forum", "news"})


def backfill_lock_key(channel_id: int) -> str:
    return f"memory:lock:discord-backfill:{channel_id}"


def resolve_channel_bot(channel_id: int) -> BotAccess | None:
    """A usable bot to read this channel's history, or None.

    A DM is read only with bots of the owner of the bot that first delivered
    it. A server channel with its verified server bot, that bot owner's other
    bots, then bots whose own token lists the server. Pushed deliveries never
    pick the bot.
    """
    with make_session() as session:
        channel = session.get(DiscordChannel, channel_id)
        if channel is None:
            return None
        server = channel.server
        if server is None:
            first = channel.bot
        else:
            first = server.bot if server.verified else None
        owner_bots = (
            session.query(DiscordBot)
            .filter(DiscordBot.created_by_user_id == first.created_by_user_id)
            .order_by(DiscordBot.id)
            .all()
            if first is not None and first.created_by_user_id is not None
            else []
        )
        members = (
            session.query(DiscordBot)
            .join(DiscordBotServer, DiscordBotServer.bot_id == DiscordBot.id)
            .filter(DiscordBotServer.server_id == server.id)
            .order_by(DiscordBot.id)
            .all()
            if server is not None
            else []
        )
        candidates = [*([first] if first else []), *owner_bots, *members]
        return next(filter(None, map(bot_access, candidates)), None)


@app.task(name=BACKFILL_DISCORD_CHANNEL)
@tracked_task
def backfill_channel(channel_id: int, before_id: int | None = None) -> dict[str, Any]:
    """Backfill historical messages for one channel (or thread).

    Idempotent and resumable. On the first run (``before_id`` is None) it starts
    from ``MIN(message_id)`` for the channel; if a run fills the per-run budget
    it re-dispatches itself with ``before_id`` set to the oldest id it fetched.
    Carrying the cursor in the task args (rather than re-reading MIN) decouples
    continuation progress from the async ``ADD_DISCORD_MESSAGE`` queue, which
    stores rows on a *separate* worker — re-deriving MIN before that queue
    drains would re-fetch the same window and spin.
    """
    with make_session() as session:
        channel = session.get(DiscordChannel, channel_id)
        # Collection may have been paused since this run, or its continuation, was queued.
        if channel is not None and not channel.should_collect:
            return {"status": "skipped", "channel_id": channel_id, "reason": "not_collected"}

    access = resolve_channel_bot(channel_id)
    if access is None:
        logger.info("Skipping backfill for channel %s: no usable bot token", channel_id)
        return {"status": "skipped", "channel_id": channel_id, "reason": "no_token"}

    # The per-channel lock stops two runs from crawling the SAME channel at once
    # (e.g. the sweep racing the channel-create trigger). It is acquired once for
    # the run and not extended, so a run exceeding BACKFILL_LOCK_TTL_SECONDS lets
    # the lock lapse — but the worst case is then idempotent duplicate work
    # (ADD_DISCORD_MESSAGE dedups on message_id and the continuation cursor is
    # carried in task args), not corruption. Different channels backfill in
    # parallel up to the worker's concurrency; a run that stays rate limited
    # stops and leaves the channel for the next sweep.
    with distributed_lock(
        backfill_lock_key(channel_id), BACKFILL_LOCK_TTL_SECONDS
    ) as lock:
        if lock is None:
            logger.info("Backfill for channel %s already running; skipping", channel_id)
            return {"status": "locked", "channel_id": channel_id}

        result = asyncio.run(
            backfill_channel_messages(
                access,
                channel_id,
                app,
                settings.DISCORD_BACKFILL_MAX_MESSAGES_PER_RUN,
                before_id=before_id,
                catch_up_days=settings.DISCORD_CATCH_UP_DAYS,
            )
        )

    if not result["done"]:
        # Continue from the oldest id fetched this run; short countdown, lock
        # released. Carrying the cursor in task args (not re-reading MIN) keeps
        # progress independent of the async ADD queue.
        backfill_channel.apply_async(
            args=[channel_id],
            kwargs={"before_id": result["oldest_message_id"]},
            countdown=5,
        )

    return {"status": "ok", "channel_id": channel_id, **result}


def collectible_channels() -> list[tuple[int, str]]:
    """``(channel_id, channel_type)`` for every channel where collection is on.

    Mirrors ``DiscordChannel.should_collect`` in SQL: explicit channel override,
    else a thread's parent channel's override, else server inheritance, else
    only DMs. A thread shares its parent's server.
    """
    parent = aliased(DiscordChannel)
    is_dm = and_(DiscordChannel.server_id.is_(None), DiscordChannel.channel_type.in_(DM_CHANNEL_TYPES))
    with make_session() as session:
        rows = (
            session.query(DiscordChannel.id, DiscordChannel.channel_type)
            .outerjoin(parent, DiscordChannel.parent_id == parent.id)
            .outerjoin(DiscordServer, DiscordChannel.server_id == DiscordServer.id)
            .filter(
                func.coalesce(
                    DiscordChannel.collect_messages, parent.collect_messages, DiscordServer.collect_messages, is_dm
                ).is_(True)
            )
            .all()
        )
    return [(row[0], row[1]) for row in rows]


@app.task(name=SCHEDULE_DISCORD_BACKFILLS)
@tracked_task
def schedule_discord_backfills() -> dict[str, Any]:
    """Daily sweep: refresh each bot's servers and channels, then dispatch a
    catch-up and backfill job per collectible channel, spread out.

    Steady state is cheap — a caught-up channel's job reads its recent page
    and one empty history page. Large channels resume across runs. Parent
    channels also get a thread-discovery job.
    """
    discover_servers()
    channels = collectible_channels()
    jitter = settings.DISCORD_BACKFILL_JITTER_SECONDS
    for channel_id, channel_type in channels:
        countdown = random.randint(0, jitter) if jitter > 0 else 0
        backfill_channel.apply_async(
            args=[channel_id], countdown=countdown
        )
        if channel_type in THREAD_PARENT_TYPES:
            discover_threads.apply_async(
                args=[channel_id], countdown=countdown
            )
    return {"status": "ok", "scheduled": len(channels)}


def discover_servers() -> None:
    """Best effort: a bot whose API is down keeps last run's servers and channels."""
    with make_session() as session:
        accesses = [bot_access(bot) for bot in session.query(DiscordBot).all()]
    for access in filter(None, accesses):
        try:
            asyncio.run(discover_bot_servers(access))
        except DiscordAPIError as e:
            logger.info("Discord server discovery failed for bot %s (%s)", access.bot_id, e)
        except Exception:
            # A malformed listing from one bot's API must not stop everyone's sweep.
            logger.exception("Discord server discovery failed for bot %s", access.bot_id)


@app.task(name=DISCOVER_DISCORD_SERVERS)
@tracked_task
def discover_bot(bot_id: int) -> dict[str, Any]:
    """List one bot's servers and channels, as the daily sweep does for all."""
    with make_session() as session:
        access = bot_access(session.get(DiscordBot, bot_id))
    if access is None:
        return {"status": "skipped", "bot_id": bot_id, "reason": "no_token"}
    return {"status": "ok", "bot_id": bot_id, "servers": asyncio.run(discover_bot_servers(access))}


@app.task(name=DISCOVER_DISCORD_THREADS)
@tracked_task
def discover_threads(channel_id: int) -> dict[str, Any]:
    """Discover threads/forum posts under a parent channel and dispatch a
    backfill job for each.

    Expects a thread-parent channel type (text/forum/news): the schedule only
    dispatches this for those, and ``channel.archived_threads`` doesn't exist
    on other channel types.
    """
    access = resolve_channel_bot(channel_id)
    if access is None:
        return {"status": "skipped", "channel_id": channel_id, "reason": "no_token"}

    thread_ids = asyncio.run(discover_channel_threads(access, channel_id, app))
    return {"status": "ok", "channel_id": channel_id, "threads": len(thread_ids)}
