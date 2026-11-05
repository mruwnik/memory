"""REST driver for the daily Discord catch-up and the history backfill.

Messages go through the same ``ADD_DISCORD_MESSAGE`` task as live ones, so a
message stored by a webhook, the gateway or this crawl is stored once.
"""

import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from memory.common import discord_events
from memory.common.celery_app import ADD_DISCORD_MESSAGE, BACKFILL_DISCORD_CHANNEL
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordChannel, DiscordMessage
from memory.discord.rest import PAGE_SIZE, BotAccess, DiscordAPIError, DiscordRest

logger = logging.getLogger(__name__)

DISCORD_EPOCH_MS = 1420070400000
# Channel types whose messages are collected: text, voice chat, news, stage, forum.
COLLECTED_CHANNEL_TYPES = frozenset({0, 2, 5, 13, 15})


def snowflake_at(moment: datetime) -> int:
    """The smallest snowflake Discord could mint at ``moment``."""
    return (int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22


def stored_message_id_bounds(channel_id: int) -> tuple[int | None, int | None]:
    with make_session() as session:
        low, high = (
            session.query(func.min(DiscordMessage.message_id), func.max(DiscordMessage.message_id))
            .filter(DiscordMessage.channel_id == channel_id)
            .one()
        )
        return low, high


def queue_messages(
    access: BotAccess, messages: list[dict], server_id: int | None, celery_app, is_edit: bool
) -> None:
    with make_session() as session:
        for message in messages:
            discord_events.ensure_message_entities(
                session, message | {"guild_id": server_id}, access.bot_id, access.push_only
            )
        session.commit()
    for message in messages:
        celery_app.send_task(
            ADD_DISCORD_MESSAGE,
            kwargs=discord_events.message_task_kwargs(
                message | {"guild_id": server_id}, access.bot_id, is_edit
            ),
        )


async def crawl(
    rest: DiscordRest,
    access: BotAccess,
    channel_id: int,
    server_id: int | None,
    celery_app,
    budget: int,
    before: int | None,
    stop_at: int | None = None,
    is_edit: bool = False,
) -> tuple[int, int | None, bool]:
    """Queue messages newest-first from ``before`` (the newest if None) until
    ``stop_at``, the start of the channel, or the budget. ``is_edit`` rewrites
    stored messages whose content changed.

    Returns (processed, oldest id fetched, reached the end).
    """
    processed = 0
    oldest: int | None = None
    while processed < budget:
        page = await rest.messages(channel_id, before=before)
        fresh = [m for m in page if stop_at is None or int(m["id"]) > stop_at]
        taken = fresh[: budget - processed]
        queue_messages(access, taken, server_id, celery_app, is_edit)
        processed += len(taken)
        if taken:
            oldest = int(taken[-1]["id"])
        if len(page) < PAGE_SIZE or len(fresh) < len(page):
            return processed, oldest, True
        if len(taken) < len(fresh):
            break
        before = int(page[-1]["id"])
    return processed, oldest, False


async def backfill_channel_messages(
    access: BotAccess,
    channel_id: int,
    celery_app,
    max_messages: int,
    before_id: int | None = None,
    catch_up_days: int = 2,
) -> dict:
    """Catch up on recent messages, then queue up to ``max_messages`` older
    than what is stored. Each pass has its own budget of ``max_messages``.

    The first run (``before_id`` None) re-reads the last ``catch_up_days`` and
    everything after the newest stored message, storing messages a webhook
    missed and rewriting ones whose content changed (missed edits), then
    crawls history from the oldest stored message. Missed deletes and
    reactions are not repaired: a message listing doesn't show them per user. Continuation
    runs carry the history cursor in ``before_id``: messages are stored
    asynchronously, so re-reading MIN(stored) would refetch the same window.

    Returns ``{"processed", "done", "oldest_message_id"}``; ``done`` means the
    crawl reached the start of the channel.
    """
    processed = 0
    oldest_seen: int | None = None
    with make_session() as session:
        channel = session.get(DiscordChannel, channel_id)
        server_id = channel.server_id if channel else None
        is_forum = channel is not None and channel.channel_type == "forum"
    if is_forum:
        # A forum's posts are threads, which discover_channel_threads crawls.
        return {"processed": 0, "done": True, "oldest_message_id": None}
    try:
        async with DiscordRest(access) as rest:
            if before_id is None:
                low, high = stored_message_id_bounds(channel_id)
                if high is not None:
                    window = snowflake_at(datetime.now(timezone.utc) - timedelta(days=catch_up_days))
                    processed, _, _ = await crawl(
                        rest, access, channel_id, server_id, celery_app,
                        max_messages, before=None, stop_at=min(high, window), is_edit=True,
                    )
                before_id = low
            count, oldest_seen, reached_start = await crawl(
                rest, access, channel_id, server_id, celery_app,
                max_messages, before=before_id,
            )
            processed += count
            oldest_seen = oldest_seen or before_id
    except DiscordAPIError as e:
        if not e.skippable:
            raise
        logger.info("Cannot read Discord channel %s (%s); skipping backfill", channel_id, e)
        return {"processed": processed, "done": True, "oldest_message_id": None}

    return {
        "processed": processed,
        "done": reached_start,
        "oldest_message_id": oldest_seen,
    }


async def discover_channel_threads(access: BotAccess, channel_id: int, celery_app) -> list[int]:
    """Store the active and archived threads (and forum posts) under a channel
    as channels, and dispatch a backfill for each. Private archived threads
    need Manage Threads and are skipped without it."""
    threads: dict[int, dict] = {}
    with make_session() as session:
        channel = session.get(DiscordChannel, channel_id)
        server_id = channel.server_id if channel else None
    try:
        async with DiscordRest(access) as rest:
            if server_id is not None:
                for thread in await rest.active_threads(server_id):
                    if discord_events.snowflake(thread.get("parent_id")) == channel_id:
                        threads[int(thread["id"])] = thread
            for thread in await rest.archived_threads(channel_id, private=False):
                threads[int(thread["id"])] = thread
            try:
                for thread in await rest.archived_threads(channel_id, private=True):
                    threads[int(thread["id"])] = thread
            except DiscordAPIError as e:
                if not e.skippable:
                    raise
                logger.info("No access to private archived threads in %s", channel_id)
    except DiscordAPIError as e:
        if not e.skippable:
            raise
        logger.info("Cannot access Discord channel %s for thread discovery (%s)", channel_id, e)

    if not threads:
        return []
    with make_session() as session:
        for thread in threads.values():
            discord_events.ensure_listed_channel(session, thread, server_id, access.bot_id)
        session.commit()
    # Stagger dispatches: a forum can have hundreds of archived threads.
    for thread_id in threads:
        celery_app.send_task(BACKFILL_DISCORD_CHANNEL, args=[thread_id], countdown=random.randint(0, 60))
    return list(threads)


async def discover_bot_servers(access: BotAccess) -> int:
    """Store the servers a bot is in and their channels; returns the server count."""
    async with DiscordRest(access) as rest:
        guilds = await rest.my_guilds()
        listed: list[tuple[dict, list[dict]]] = []
        for guild in guilds:
            try:
                listed.append((guild, await rest.guild_channels(int(guild["id"]))))
            except DiscordAPIError as e:
                if not e.skippable:
                    raise
                logger.info("Cannot list channels of Discord server %s (%s)", guild.get("id"), e)
                listed.append((guild, []))

    with make_session() as session:
        for guild, channels in listed:
            server_id = int(guild["id"])
            discord_events.verify_server(
                session, server_id, access.bot_id, collect=access.push_only, name=guild.get("name")
            )
            for channel in channels:
                if channel.get("type") in COLLECTED_CHANNEL_TYPES:
                    discord_events.ensure_listed_channel(session, channel, server_id, access.bot_id)
        session.commit()
    return len(listed)
