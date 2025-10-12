"""
Discord API server.

FastAPI server that owns and manages a Discord collector instance,
providing HTTP endpoints for sending Discord messages.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from memory.common import settings
from memory.workers.discord.collector import MessageCollector

logger = logging.getLogger(__name__)


class SendDMRequest(BaseModel):
    user: str  # Discord user ID or username
    message: str


class SendChannelRequest(BaseModel):
    channel_name: str  # Channel name (e.g., "memory-errors")
    message: str


# Application state
class AppState:
    def __init__(self):
        self.collector: MessageCollector | None = None
        self.collector_task: asyncio.Task | None = None


app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage Discord collector lifecycle"""
    if not settings.DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not configured")
        return

    # Create and start the collector
    app_state.collector = MessageCollector()
    app_state.collector_task = asyncio.create_task(
        app_state.collector.start(settings.DISCORD_BOT_TOKEN)
    )
    logger.info("Discord collector started")

    yield

    # Cleanup
    if app_state.collector and not app_state.collector.is_closed():
        await app_state.collector.close()

    if app_state.collector_task:
        app_state.collector_task.cancel()
        try:
            await app_state.collector_task
        except asyncio.CancelledError:
            pass

    logger.info("Discord collector stopped")


# FastAPI app with lifespan management
app = FastAPI(title="Discord Collector API", version="1.0.0", lifespan=lifespan)


@app.post("/send_dm")
async def send_dm_endpoint(request: SendDMRequest):
    """Send a DM via the collector's Discord client"""
    if not app_state.collector:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    try:
        success = await app_state.collector.send_dm(request.user, request.message)

        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to send DM to {request.user}",
            )
        return {
            "success": True,
            "message": f"DM sent to {request.user}",
            "user": request.user,
        }
    except Exception as e:
        logger.error(f"Failed to send DM: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send_channel")
async def send_channel_endpoint(request: SendChannelRequest):
    """Send a message to a channel via the collector's Discord client"""
    if not app_state.collector:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    try:
        success = await app_state.collector.send_to_channel(
            request.channel_name, request.message
        )

        if success:
            return {
                "success": True,
                "message": f"Message sent to channel {request.channel_name}",
                "channel": request.channel_name,
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to send message to channel {request.channel_name}",
            )

    except Exception as e:
        logger.error(f"Failed to send channel message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Check if the Discord collector is running and healthy"""
    if not app_state.collector:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    collector = app_state.collector
    return {
        "status": "healthy",
        "connected": not collector.is_closed(),
        "user": str(collector.user) if collector.user else None,
        "guilds": len(collector.guilds) if collector.guilds else 0,
    }


@app.post("/refresh_metadata")
async def refresh_metadata():
    """Refresh Discord server/channel/user metadata from Discord API"""
    if not app_state.collector:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    try:
        result = await app_state.collector.refresh_metadata()
        return {"success": True, "message": "Metadata refreshed successfully", **result}
    except Exception as e:
        logger.error(f"Failed to refresh metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def run_discord_api_server(host: str = "127.0.0.1", port: int = 8001):
    """Run the Discord API server"""
    uvicorn.run(app, host=host, port=port, log_level="debug")


if __name__ == "__main__":
    # For testing the API server standalone
    host = settings.DISCORD_COLLECTOR_SERVER_URL
    port = settings.DISCORD_COLLECTOR_PORT
    run_discord_api_server(host, port)
