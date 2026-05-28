"""Celery tasks for backfilling historical Discord messages.

Runs on the standard worker pool, which consumes the ``backfill`` queue
alongside its other queues. discord.py is in requirements-workers.txt, so the
crawl driver (``memory.discord.backfill``) is imported normally.
"""
import asyncio
import logging
import random
from typing import Any

from sqlalchemy import and_, or_

from memory.common import settings
from memory.common.celery_app import (
    BACKFILL_DISCORD_CHANNEL,
    DISCOVER_DISCORD_THREADS,
    SCHEDULE_DISCORD_BACKFILLS,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordChannel, DiscordServer
from memory.common.jobs import tracked_task
from memory.common.redis_lock import distributed_lock
from memory.discord.backfill import (
    backfill_channel_messages,
    discover_channel_threads,
)

logger = logging.getLogger(__name__)

# Safety timeout: if a run crashes mid-channel, the lock auto-expires so the
# next sweep can resume. Comfortably longer than one run's message budget.
BACKFILL_LOCK_TTL_SECONDS = 60 * 60

# Channel types that can contain threads / forum posts worth discovering.
THREAD_PARENT_TYPES = frozenset({"text", "forum", "news"})


def backfill_lock_key(channel_id: int) -> str:
    return f"memory:lock:discord-backfill:{channel_id}"


def resolve_channel_bot_token(channel_id: int) -> tuple[int, str] | None:
    """Return ``(bot_id, token)`` able to fetch this channel's history, or None.

    History is fetched with the bot that owns the channel's server. Returns
    None for channels we cannot fetch: no server (DM), no owning bot, or no
    stored token.
    """
    with make_session() as session:
        channel = session.get(DiscordChannel, channel_id)
        if channel is None or channel.server is None:
            return None
        bot = channel.server.bot
        if bot is None or not bot.token:
            return None
        return bot.id, bot.token


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
    creds = resolve_channel_bot_token(channel_id)
    if creds is None:
        logger.info("Skipping backfill for channel %s: no usable bot token", channel_id)
        return {"status": "skipped", "channel_id": channel_id, "reason": "no_token"}
    bot_id, token = creds

    # The per-channel lock stops two runs from crawling the SAME channel at once
    # (e.g. the sweep racing the channel-create trigger). It is acquired once for
    # the run and not extended, so a run exceeding BACKFILL_LOCK_TTL_SECONDS lets
    # the lock lapse — but the worst case is then idempotent duplicate work
    # (ADD_DISCORD_MESSAGE dedups on message_id and the continuation cursor is
    # carried in task args), not corruption. Different channels backfill in
    # parallel up to the worker's concurrency; each run uses its own short-lived
    # discord client and discord.py respects 429s, so the shared global rate
    # limit holds (just slower) under contention.
    with distributed_lock(
        backfill_lock_key(channel_id), BACKFILL_LOCK_TTL_SECONDS
    ) as lock:
        if lock is None:
            logger.info("Backfill for channel %s already running; skipping", channel_id)
            return {"status": "locked", "channel_id": channel_id}

        result = asyncio.run(
            backfill_channel_messages(
                token,
                channel_id,
                bot_id,
                app,
                settings.DISCORD_BACKFILL_MAX_MESSAGES_PER_RUN,
                before_id=before_id,
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
    else server inheritance, else excluded (serverless channels need an explicit
    True).
    """
    with make_session() as session:
        rows = (
            session.query(DiscordChannel.id, DiscordChannel.channel_type)
            .outerjoin(DiscordServer, DiscordChannel.server_id == DiscordServer.id)
            .filter(
                or_(
                    DiscordChannel.collect_messages.is_(True),
                    and_(
                        DiscordChannel.collect_messages.is_(None),
                        DiscordServer.collect_messages.is_(True),
                    ),
                )
            )
            .all()
        )
    return [(row[0], row[1]) for row in rows]


@app.task(name=SCHEDULE_DISCORD_BACKFILLS)
@tracked_task
def schedule_discord_backfills() -> dict[str, Any]:
    """Weekly sweep: dispatch a backfill job per collectible channel, spread out.

    Steady state is cheap — a fully-backfilled channel's job makes one empty
    history call and exits. Large channels resume across runs. Parent channels
    also get a thread-discovery job.
    """
    channels = collectible_channels()
    jitter = settings.DISCORD_BACKFILL_JITTER_SECONDS
    for channel_id, channel_type in channels:
        countdown = random.randint(0, jitter) if jitter > 0 else 0
        backfill_channel.apply_async(args=[channel_id], countdown=countdown)
        if channel_type in THREAD_PARENT_TYPES:
            discover_threads.apply_async(args=[channel_id], countdown=countdown)
    return {"status": "ok", "scheduled": len(channels)}


@app.task(name=DISCOVER_DISCORD_THREADS)
@tracked_task
def discover_threads(channel_id: int) -> dict[str, Any]:
    """Discover threads/forum posts under a parent channel and dispatch a
    backfill job for each.

    Expects a thread-parent channel type (text/forum/news): the schedule only
    dispatches this for those, and ``channel.archived_threads`` doesn't exist
    on other channel types.
    """
    creds = resolve_channel_bot_token(channel_id)
    if creds is None:
        return {"status": "skipped", "channel_id": channel_id, "reason": "no_token"}
    _, token = creds

    thread_ids = asyncio.run(
        discover_channel_threads(token, channel_id, app)
    )
    return {"status": "ok", "channel_id": channel_id, "threads": len(thread_ids)}
