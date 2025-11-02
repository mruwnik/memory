"""
Discord API server.

FastAPI server that owns and manages a Discord collector instance,
providing HTTP endpoints for sending Discord messages.
"""

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models.users import DiscordBotUser
from memory.discord.collector import MessageCollector
from memory.discord.oauth import complete_oauth_flow

logger = logging.getLogger(__name__)


class SendDMRequest(BaseModel):
    bot_id: int
    user: str  # Discord user ID or username
    message: str


class SendChannelRequest(BaseModel):
    bot_id: int
    channel: int | str  # Channel name or ID (ID supports threads)
    message: str


class TypingDMRequest(BaseModel):
    bot_id: int
    user: int | str


class TypingChannelRequest(BaseModel):
    bot_id: int
    channel: int | str  # Channel name or ID (ID supports threads)


class AddReactionRequest(BaseModel):
    bot_id: int
    channel: int | str  # Channel name or ID (ID supports threads)
    message_id: int
    emoji: str


class Collector:
    collector: MessageCollector
    collector_task: asyncio.Task
    bot_id: int
    bot_token: str
    bot_name: str

    def __init__(self, collector: MessageCollector, bot: DiscordBotUser):
        logger.error(f"Initialized collector for {bot.name} woth {bot.api_key}")
        self.collector = collector
        self.collector_task = asyncio.create_task(collector.start(str(bot.api_key)))
        self.bot_id = cast(int, bot.id)
        self.bot_token = str(bot.api_key)
        self.bot_name = str(bot.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage Discord collector lifecycle"""

    def make_collector(bot: DiscordBotUser):
        collector = MessageCollector()
        return Collector(collector=collector, bot=bot)

    with make_session() as session:
        bots = session.query(DiscordBotUser).all()
        app.bots = {bot.id: make_collector(bot) for bot in bots}

    logger.error(
        f"Discord collectors started for {len(app.bots)} bots: {app.bots.keys()}"
    )

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


@app.post("/typing/dm")
async def trigger_dm_typing(request: TypingDMRequest):
    """Trigger a typing indicator for a DM via the collector"""
    collector = app.bots.get(request.bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        success = await collector.collector.trigger_typing_dm(request.user)
    except Exception as e:
        logger.error(f"Failed to trigger DM typing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to trigger typing for {request.user}",
        )

    return {
        "success": True,
        "user": request.user,
        "message": f"Typing triggered for {request.user}",
    }


@app.post("/send_channel")
async def send_channel_endpoint(request: SendChannelRequest):
    """Send a message to a channel via the collector's Discord client"""
    collector = app.bots.get(request.bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        success = await collector.collector.send_to_channel(
            request.channel, request.message
        )
    except Exception as e:
        logger.error(f"Failed to send channel message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if success:
        return {
            "success": True,
            "message": f"Message sent to channel {request.channel}",
            "channel": request.channel,
        }

    raise HTTPException(
        status_code=400,
        detail=f"Failed to send message to channel {request.channel}",
    )


@app.post("/typing/channel")
async def trigger_channel_typing(request: TypingChannelRequest):
    """Trigger a typing indicator for a channel via the collector"""
    collector = app.bots.get(request.bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        success = await collector.collector.trigger_typing_channel(request.channel)
    except Exception as e:
        logger.error(f"Failed to trigger channel typing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to trigger typing for channel {request.channel}",
        )

    return {
        "success": True,
        "channel": request.channel,
        "message": f"Typing triggered for channel {request.channel}",
    }


@app.get("/health")
async def health_check():
    """Check if the Discord collector is running and healthy"""
    if not app.bots:
        raise HTTPException(status_code=503, detail="Discord collector not running")

    return {
        bot.bot_name: {
            "status": "healthy",
            "connected": not bot.collector.is_closed(),
            "user": str(bot.collector.user) if bot.collector.user else None,
            "guilds": len(bot.collector.guilds) if bot.collector.guilds else 0,
        }
        for bot in app.bots.values()
    }


@app.post("/add_reaction")
async def add_reaction_endpoint(request: AddReactionRequest):
    """Add a reaction to a message via the collector's Discord client"""
    collector = app.bots.get(request.bot_id)
    if not collector:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        success = await collector.collector.add_reaction(
            request.channel, request.message_id, request.emoji
        )
    except Exception as e:
        logger.error(f"Failed to add reaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to add reaction to message {request.message_id}",
        )

    return {
        "success": True,
        "channel": request.channel,
        "message_id": request.message_id,
        "emoji": request.emoji,
        "message": f"Added reaction {request.emoji} to message {request.message_id}",
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


@app.get("/oauth/callback/discord", response_class=HTMLResponse)
async def oauth_callback(request: Request):
    """Handle OAuth callback from MCP server after user authorization."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    logger.info(
        f"Received OAuth callback: code={code and code[:20]}..., state={state and state[:20]}..."
    )

    message, title, close, status_code = "", "", "", 200
    if error:
        logger.error(f"OAuth error: {error}")
        message = f"Error: {error}"
        title = "❌ Authorization Failed"
        status_code = 400
    elif not code or not state:
        message = "Missing authorization code or state parameter."
        title = "❌ Invalid Request"
        status_code = 400
    else:
        # Complete the OAuth flow (exchange code for token)
        with make_session() as session:
            status_code, message = await complete_oauth_flow(session, code, state)
        if 200 <= status_code < 300:
            title = "✅ Authorization Successful!"
            close = "You can close this window and return to the MCP server."
        else:
            title = "❌ Authorization Failed"

    return HTMLResponse(
        content=f"""
        <html>
            <body>
                <h1>{title}</h1>
                <p>{message}</p>
                <p>{close}</p>
            </body>
        </html>
        """,
        status_code=status_code,
    )


def run_discord_api_server(host: str = "127.0.0.1", port: int = 8001):
    """Run the Discord API server"""
    uvicorn.run(app, host=host, port=port, log_level="debug")


if __name__ == "__main__":
    # For testing the API server standalone
    host = settings.DISCORD_COLLECTOR_SERVER_URL
    port = settings.DISCORD_COLLECTOR_PORT
    run_discord_api_server(host, port)
