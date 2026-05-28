"""Async REST-only driver for backfilling historical Discord messages.

Reuses the live ingestion helpers (``ensure_message_entities``/``queue_message``)
so backfilled messages are stored identically to live ones.
"""
import logging
import random
from contextlib import asynccontextmanager

import discord
from sqlalchemy import func

from memory.common.celery_app import BACKFILL_DISCORD_CHANNEL
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordMessage
from memory.discord.ingest import (
    ensure_channel,
    ensure_message_entities,
    queue_message,
)

logger = logging.getLogger(__name__)


def oldest_stored_message_id(channel_id: int) -> int | None:
    """Smallest stored ``message_id`` for a channel — the backfill resume cursor.

    Snowflake IDs are time-monotonic, so backfill fetches messages *older* than
    this. ``None`` means nothing is stored yet (fetch from the newest message).
    """
    with make_session() as session:
        return (
            session.query(func.min(DiscordMessage.message_id))
            .filter(DiscordMessage.channel_id == channel_id)
            .scalar()
        )


@asynccontextmanager
async def discord_client(token: str):
    """Yield a logged-in REST-only client (no gateway); closed on exit."""
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    await client.login(token)
    try:
        yield client
    finally:
        await client.close()


async def backfill_channel_messages(
    token: str,
    channel_id: int,
    bot_id: int,
    celery_app,
    max_messages: int,
    before_id: int | None = None,
) -> dict:
    """Queue up to ``max_messages`` messages older than what we already have.

    Crawls **newest-first** — ``history()``'s default when ``before`` is given.
    Do NOT pass ``oldest_first=True``: the crawl resumes from a ``before`` cursor,
    and filling oldest-first would skip an unfetched region on the next run.

    The resume cursor is carried explicitly in ``before_id`` across continuation
    runs (the task passes back the oldest id it fetched). On the *first* run
    ``before_id`` is None, so we derive the live boundary from
    ``MIN(stored message_id)``. We do NOT re-derive from MIN on continuations:
    messages are stored asynchronously by ``ADD_DISCORD_MESSAGE`` on a separate
    queue, so re-reading MIN before that queue drains would yield the same window
    and spin (re-fetching the same page, burning Discord REST rate limit).

    Returns ``{"processed": int, "done": bool, "oldest_message_id": int | None}``
    where ``done`` means we reached the start of the channel (fewer than the
    budget came back), and ``oldest_message_id`` is the oldest id fetched this
    run (the next continuation's cursor), or None if nothing was fetched.
    """
    if before_id is None:
        before_id = oldest_stored_message_id(channel_id)
    before = discord.Object(id=before_id) if before_id is not None else None

    processed = 0
    # Newest-first crawl: each message is older than the last, so the final id
    # yielded is the oldest. Track it as the next continuation's cursor.
    oldest_seen: int | None = None
    async with discord_client(token) as client:
        try:
            channel = await client.fetch_channel(channel_id)
            if not hasattr(channel, "history"):
                # Non-messageable channel (e.g. a ForumChannel) — it has no own
                # message history; its content lives in threads, covered by
                # discover_threads. Treat a direct backfill as a clean no-op.
                logger.info(
                    "Channel %s is not messageable (%s); skipping direct backfill",
                    channel_id,
                    type(channel).__name__,
                )
            else:
                # NEWEST-FIRST (no oldest_first) — required for the before-cursor resume.
                async for message in channel.history(limit=max_messages, before=before):
                    with make_session() as session:
                        ensure_message_entities(session, message, bot_id)
                        session.commit()
                    queue_message(celery_app, message, bot_id)
                    oldest_seen = message.id
                    processed += 1
        except (discord.Forbidden, discord.NotFound) as e:
            # The bot can't read this channel (archived / not a member / deleted).
            # Skip cleanly so the weekly sweep doesn't fail on inaccessible channels.
            logger.info(
                "Cannot read channel %s (%s); skipping backfill",
                channel_id,
                type(e).__name__,
            )

    return {
        "processed": processed,
        "done": processed < max_messages,
        "oldest_message_id": oldest_seen,
    }


async def collect_private_archived_threads(channel, threads: dict) -> None:
    """Add private archived threads to ``threads``. Best-effort: skipped if the
    bot lacks Manage Threads (the channel itself is still accessible)."""
    try:
        async for thread in channel.archived_threads(
            limit=None, private=True, joined=False
        ):
            threads[thread.id] = thread
    except discord.Forbidden:
        logger.info("No access to private archived threads in %s", channel.id)


async def discover_channel_threads(
    token: str,
    channel_id: int,
    celery_app,
) -> list[int]:
    """Enumerate active + archived threads under a text/forum channel.

    Threads (and forum posts) are stored as their own ``DiscordChannel`` rows,
    so for each discovered thread we ``ensure_channel`` (so it inherits the
    server's collect/project settings and resolves a bot token) and dispatch a
    ``backfill_channel`` job. Returns the discovered thread IDs.
    """
    # Collect into a dict keyed by id directly — a thread can surface in more
    # than one listing (active vs archived); last write wins, same object.
    threads: dict[int, discord.Thread] = {}
    async with discord_client(token) as client:
        try:
            channel = await client.fetch_channel(channel_id)

            # Active threads are guild-wide; filter to this parent.
            for thread in await channel.guild.active_threads():
                if thread.parent_id == channel_id:
                    threads[thread.id] = thread

            # Public archived threads (REST async iterator).
            async for thread in channel.archived_threads(limit=None):
                threads[thread.id] = thread

            # Private archived threads — best-effort (needs Manage Threads).
            await collect_private_archived_threads(channel, threads)
        except (discord.Forbidden, discord.NotFound) as e:
            # Bot can't access this channel (archived / not a member / deleted).
            logger.info(
                "Cannot access channel %s for thread discovery (%s); skipping",
                channel_id,
                type(e).__name__,
            )

    if not threads:
        return []

    with make_session() as session:
        for thread in threads.values():
            ensure_channel(session, thread, thread.guild.id)
        session.commit()
    # Stagger dispatches: a forum with hundreds of archived threads would
    # otherwise fire that many backfill tasks at the same instant onto the
    # backfill queue. A small random countdown spreads them out.
    for thread_id in threads:
        celery_app.send_task(
            BACKFILL_DISCORD_CHANNEL,
            args=[thread_id],
            countdown=random.randint(0, 60),
        )

    return list(threads.keys())
