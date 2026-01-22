"""
Discord collector API server.

Provides HTTP endpoints for sending messages via the Discord collector bots.
This runs alongside the collector and exposes its functionality via REST API.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from memory.common.celery_app import app as celery_app
from memory.discord.collector import CollectorManager

logger = logging.getLogger(__name__)

# Global collector manager
manager: CollectorManager | None = None


class SendDMRequest(BaseModel):
    bot_id: int
    user: str | int  # User ID or username
    message: str


class SendChannelRequest(BaseModel):
    bot_id: int
    channel: str | int  # Channel ID or name
    message: str


class TypingRequest(BaseModel):
    bot_id: int
    user: str | int | None = None
    channel: str | int | None = None


class ReactionRequest(BaseModel):
    bot_id: int
    channel: str | int
    message_id: int
    emoji: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage collector lifecycle."""
    global manager

    manager = CollectorManager(celery_app)

    # Start all bots
    await manager.start_all()
    logger.info("Discord collector API started")

    yield

    # Stop all bots
    await manager.stop_all()
    logger.info("Discord collector API stopped")


app = FastAPI(title="Discord Collector API", lifespan=lifespan)


def get_manager() -> CollectorManager:
    """Get the collector manager."""
    if manager is None:
        raise HTTPException(status_code=503, detail="Collector not initialized")
    return manager


async def resolve_channel_id(channel: str | int) -> int:
    """Resolve a channel name or ID to an ID."""
    if isinstance(channel, int):
        return channel

    # Try to parse as int first
    try:
        return int(channel)
    except ValueError:
        pass

    # Look up by name in database
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordChannel

    with make_session() as session:
        ch = session.query(DiscordChannel).filter_by(name=channel).first()
        if ch:
            return ch.id

    raise HTTPException(status_code=404, detail=f"Channel '{channel}' not found")


async def resolve_user_id(user: str | int, mgr: CollectorManager | None = None) -> int:
    """Resolve a username or ID to an ID.

    Tries in order:
    1. Parse as integer ID
    2. Look up in local database
    3. Search Discord guild members (if manager provided)
    """
    if isinstance(user, int):
        return user

    # Try to parse as int first
    try:
        return int(user)
    except ValueError:
        pass

    # Look up by username in database
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordUser

    with make_session() as session:
        u = session.query(DiscordUser).filter_by(username=user).first()
        if u:
            return u.id

    # Fall back to Discord API - search guild members
    if mgr:
        for collector in mgr.collectors.values():
            for guild in collector.guilds:
                # Search by username (case-insensitive)
                member = guild.get_member_named(user)
                if member:
                    return member.id

    raise HTTPException(status_code=404, detail=f"User '{user}' not found")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Check health of all bots."""
    mgr = get_manager()
    result = {}
    for bot_id, collector in mgr.collectors.items():
        result[str(bot_id)] = {
            "connected": collector.is_ready(),
            "name": collector.bot_info.name,
        }
    return result


@app.post("/send_dm")
async def send_dm(request: SendDMRequest) -> dict[str, Any]:
    """Send a DM to a user."""
    mgr = get_manager()
    user_id = await resolve_user_id(request.user, mgr)
    success = await mgr.send_dm(request.bot_id, user_id, request.message)
    return {"success": success}


@app.post("/send_channel")
async def send_channel(request: SendChannelRequest) -> dict[str, Any]:
    """Send a message to a channel."""
    mgr = get_manager()
    channel_id = await resolve_channel_id(request.channel)
    success = await mgr.send_message(request.bot_id, channel_id, request.message)
    return {"success": success}


@app.post("/typing/dm")
async def typing_dm(request: TypingRequest) -> dict[str, Any]:
    """Trigger typing indicator in a DM."""
    mgr = get_manager()
    if request.user is None:
        raise HTTPException(status_code=400, detail="user is required")

    user_id = await resolve_user_id(request.user, mgr)
    collector = mgr.get_collector(request.bot_id)
    if not collector:
        return {"success": False}

    try:
        user = collector.get_user(user_id)
        if user is None:
            user = await collector.fetch_user(user_id)
        if user:
            dm_channel = user.dm_channel or await user.create_dm()
            await dm_channel.typing()
            return {"success": True}
    except Exception:
        logger.exception(f"Failed to trigger typing for user {user_id}")

    return {"success": False}


@app.post("/typing/channel")
async def typing_channel(request: TypingRequest) -> dict[str, Any]:
    """Trigger typing indicator in a channel."""
    mgr = get_manager()
    if request.channel is None:
        raise HTTPException(status_code=400, detail="channel is required")

    channel_id = await resolve_channel_id(request.channel)
    collector = mgr.get_collector(request.bot_id)
    if not collector:
        return {"success": False}

    try:
        channel = collector.get_channel(channel_id)
        if channel is None:
            channel = await collector.fetch_channel(channel_id)
        if channel and hasattr(channel, "typing"):
            await channel.typing()
            return {"success": True}
    except Exception:
        logger.exception(f"Failed to trigger typing for channel {channel_id}")

    return {"success": False}


@app.post("/add_reaction")
async def add_reaction(request: ReactionRequest) -> dict[str, Any]:
    """Add a reaction to a message."""
    mgr = get_manager()
    channel_id = await resolve_channel_id(request.channel)
    collector = mgr.get_collector(request.bot_id)
    if not collector:
        return {"success": False}

    success = await collector.add_reaction(channel_id, request.message_id, request.emoji)
    return {"success": success}


@app.post("/refresh_metadata")
async def refresh_metadata() -> dict[str, Any]:
    """Refresh Discord metadata from API."""
    mgr = get_manager()

    from memory.common.db.connection import make_session
    from memory.discord.collector import ensure_channel, ensure_server, ensure_user

    updated = {"servers": 0, "channels": 0, "users": 0}

    for collector in mgr.collectors.values():
        with make_session() as session:
            # Update servers
            for guild in collector.guilds:
                ensure_server(session, guild)
                updated["servers"] += 1

                # Update channels
                for channel in guild.channels:
                    if hasattr(channel, "send"):  # Text-like channels
                        ensure_channel(session, channel, guild.id)
                        updated["channels"] += 1

                # Update users
                for member in guild.members:
                    ensure_user(session, member)
                    updated["users"] += 1

            session.commit()

    return {"success": True, "updated": updated}


if __name__ == "__main__":
    import uvicorn
    from memory.common import settings

    uvicorn.run(
        "memory.discord.api:app",
        host="0.0.0.0",
        port=settings.DISCORD_COLLECTOR_PORT,
        reload=False,
    )
