# pyright: reportAttributeAccessIssue=false
"""
Discord collector API server.

Runs the gateway collectors and reports their health; sending goes over the
REST API, not through here.
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import Any, cast

from discord import abc
from fastapi import FastAPI, HTTPException

from memory.common.celery_app import app as celery_app
from memory.common.db.connection import make_session
from memory.discord.collector import CollectorManager
from memory.discord.ingest import ensure_channel, ensure_server, ensure_user

logger = logging.getLogger(__name__)

# Global collector manager
manager: CollectorManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage collector lifecycle."""
    global manager

    manager = CollectorManager(celery_app)
    await manager.sync()
    syncing = asyncio.create_task(manager.keep_synced())
    logger.info("Discord collector API started")

    yield

    syncing.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await syncing
    await manager.stop_all()
    logger.info("Discord collector API stopped")


app = FastAPI(title="Discord Collector API", lifespan=lifespan)


def get_manager() -> CollectorManager:
    """Get the collector manager."""
    if manager is None:
        raise HTTPException(status_code=503, detail="Collector not initialized")
    return manager


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


@app.post("/refresh_metadata")
async def refresh_metadata() -> dict[str, Any]:
    """Refresh Discord metadata from API."""
    mgr = get_manager()
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
                        ensure_channel(session, cast(abc.Messageable, channel), guild.id)
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
