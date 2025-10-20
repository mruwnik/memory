"""
Discord API server.

FastAPI server that owns and manages a Discord collector instance,
providing HTTP endpoints for sending Discord messages.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from memory.common import settings
from memory.discord.collector import MessageCollector
from memory.common.db.models.users import BotUser
from memory.common.db.connection import make_session

logger = logging.getLogger(__name__)


class SendDMRequest(BaseModel):
    bot_id: int
    user: str  # Discord user ID or username
    message: str


class SendChannelRequest(BaseModel):
    bot_id: int
    channel_name: str  # Channel name (e.g., "memory-errors")
    message: str


class Collector:
    collector: MessageCollector
    collector_task: asyncio.Task
    bot_id: int
    bot_token: str
    bot_name: str

    def __init__(self, collector: MessageCollector, bot: BotUser):
        self.collector = collector
        self.collector_task = asyncio.create_task(collector.start(bot.api_key))
        self.bot_id = bot.id
        self.bot_token = bot.api_key
        self.bot_name = bot.name


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

    def make_collector(bot: BotUser):
        collector = MessageCollector()
        return Collector(collector=collector, bot=bot)

    with make_session() as session:
        app.bots = {bot.id: make_collector(bot) for bot in session.query(BotUser).all()}

    logger.info(f"Discord collectors started for {len(app.bots)} bots")

    yield

    # Cleanup
    for bot in app.bots.values():
        if not bot.collector.is_closed():
            await bot.collector.close()
        if bot.collector_task:
            bot.collector_task.cancel()
            try:
                await bot.collector_task
            except asyncio.CancelledError:
                pass
    logger.info(f"Discord collectors stopped for {len(app.bots)} bots")


# FastAPI app with lifespan management
app = FastAPI(title="Discord Collector API", version="1.0.0", lifespan=lifespan)


@app.post("/send_dm")
async def send_dm_endpoint(request: SendDMRequest):
    """Send a DM via the collector's Discord client"""
    collector = app.bots.get(request.bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        success = await collector.collector.send_dm(request.user, request.message)
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Failed to send DM: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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


@app.post("/send_channel")
async def send_channel_endpoint(request: SendChannelRequest):
    """Send a message to a channel via the collector's Discord client"""
    collector = app.bots.get(request.bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        success = await collector.collector.send_to_channel(
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
    if not app.bots:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    collector = app_state.collector
    return {
        collector.bot_name: {
            "status": "healthy",
            "connected": not bot.collector.is_closed(),
            "user": str(bot.collector.user) if bot.collector.user else None,
            "guilds": len(bot.collector.guilds) if bot.collector.guilds else 0,
        }
        for bot in app.bots.values()
    }


@app.post("/refresh_metadata")
async def refresh_metadata():
    """Refresh Discord server/channel/user metadata from Discord API"""
    if not app.bots:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    try:
        result = {
            bot.bot_name: await bot.collector.refresh_metadata()
            for bot in app.bots.values()
        }
        return {
            "success": True,
            "message": f"Metadata refreshed successfully for {len(app.bots)} bots",
            "results": result,
        }
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
