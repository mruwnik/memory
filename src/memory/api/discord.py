"""API endpoints for Discord bot, server, and channel management."""

from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common import discord as discord_client
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.discord import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
)
from memory.common.discord_data import (
    fetch_servers,
    get_bot_for_user,
    get_user_bots,
)

router = APIRouter(prefix="/discord", tags=["discord"])


# --- Bot Models ---


class DiscordBotCreate(BaseModel):
    name: str
    token: str


class DiscordBotUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class DiscordBotResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    created_at: str | None
    updated_at: str | None
    connected: bool | None = None


# --- Server Models ---


class DiscordServerResponse(BaseModel):
    id: int
    name: str
    description: str | None
    member_count: int | None
    collect_messages: bool
    last_sync_at: str | None
    channel_count: int


class DiscordServerUpdate(BaseModel):
    collect_messages: bool | None = None


# --- Channel Models ---


class DiscordChannelResponse(BaseModel):
    id: int
    server_id: int | None
    server_name: str | None
    name: str
    channel_type: str
    collect_messages: bool | None
    effective_collect: bool


class DiscordChannelUpdate(BaseModel):
    collect_messages: bool | None = None


# --- Bot User Models ---


class BotUserResponse(BaseModel):
    """Response model for bot authorized users (excludes email for privacy)."""

    id: int
    name: str


class UserStatusResponse(BaseModel):
    """Response model for user add/remove operations."""

    status: str
    user_id: int


class AddUserRequest(BaseModel):
    """Request model for adding a user to a bot's authorized users."""

    user_id: int


# --- Helper Functions ---


def get_user_bot(db: Session, bot_id: int, user: User) -> DiscordBot:
    """Get a bot by ID, ensuring user is authorized."""
    bot = get_bot_for_user(db, bot_id, user)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


def require_discord_access(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> tuple[User, Session]:
    """Require user to have at least one authorized Discord bot.

    Use as a dependency to gate access to server/channel endpoints.
    """
    if not get_user_bots(db, user.id):
        raise HTTPException(status_code=403, detail="No authorized Discord bots")
    return user, db


def bot_to_response(bot: DiscordBot, connected: bool | None = None) -> DiscordBotResponse:
    """Convert a DiscordBot model to a response."""
    return DiscordBotResponse(
        id=cast(int, bot.id),
        name=cast(str, bot.name),
        is_active=cast(bool, bot.is_active),
        created_at=bot.created_at.isoformat() if bot.created_at else None,
        updated_at=bot.updated_at.isoformat() if bot.updated_at else None,
        connected=connected,
    )


def server_to_response(server: DiscordServer) -> DiscordServerResponse:
    """Convert a DiscordServer model to a response."""
    return DiscordServerResponse(
        id=cast(int, server.id),
        name=cast(str, server.name),
        description=cast(str | None, server.description),
        member_count=cast(int | None, server.member_count),
        collect_messages=cast(bool, server.collect_messages),
        last_sync_at=server.last_sync_at.isoformat() if server.last_sync_at else None,
        channel_count=len(server.channels),
    )


def channel_to_response(channel: DiscordChannel) -> DiscordChannelResponse:
    """Convert a DiscordChannel model to a response."""
    return DiscordChannelResponse(
        id=cast(int, channel.id),
        server_id=cast(int | None, channel.server_id),
        server_name=channel.server.name if channel.server else None,
        name=cast(str, channel.name),
        channel_type=cast(str, channel.channel_type),
        collect_messages=channel.collect_messages,
        effective_collect=channel.should_collect,
    )


# --- Bot Endpoints ---


@router.get("/bots")
def list_bots(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[DiscordBotResponse]:
    """List Discord bots the user is authorized to use."""
    bots = get_user_bots(db, user.id)

    # Check connection status for each bot
    responses = []
    for bot in bots:
        connected = discord_client.is_collector_healthy(cast(int, bot.id))
        responses.append(bot_to_response(bot, connected=connected))

    return responses


@router.post("/bots")
def create_bot(
    data: DiscordBotCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> DiscordBotResponse:
    """Create a new Discord bot.

    The bot ID is extracted from the token. The user is automatically
    authorized to use the bot.
    """
    # Extract bot ID from token (format: bot_id.timestamp.hmac)
    try:
        bot_id = int(data.token.split(".")[0])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid bot token format")

    # Check if bot already exists
    existing = db.get(DiscordBot, bot_id)
    if existing:
        # If bot exists, just authorize this user if not already
        if not existing.is_authorized(user):
            existing.authorized_users.append(user)
            db.commit()
        return bot_to_response(existing)

    # Create new bot
    bot = DiscordBot(id=bot_id, name=data.name)
    bot.token = data.token
    bot.authorized_users.append(user)

    db.add(bot)
    db.commit()
    db.refresh(bot)

    return bot_to_response(bot)


@router.patch("/bots/{bot_id}")
def update_bot(
    bot_id: int,
    updates: DiscordBotUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> DiscordBotResponse:
    """Update a Discord bot."""
    bot = get_user_bot(db, bot_id, user)

    if updates.name is not None:
        bot.name = updates.name
    if updates.is_active is not None:
        bot.is_active = updates.is_active

    db.commit()
    db.refresh(bot)

    return bot_to_response(bot)


@router.delete("/bots/{bot_id}")
def delete_bot(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Remove user authorization from a bot.

    If the user is the only authorized user, the bot is deleted entirely.
    """
    bot = get_user_bot(db, bot_id, user)

    if len(bot.authorized_users) == 1:
        # User is the only one authorized - delete the bot
        db.delete(bot)
    else:
        # Just remove this user's authorization
        bot.authorized_users.remove(user)

    db.commit()
    return {"status": "deleted"}


@router.get("/bots/{bot_id}/health")
def get_bot_health(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Get real-time connection status for a bot."""
    get_user_bot(db, bot_id, user)  # Verify authorization
    connected = discord_client.is_collector_healthy(bot_id)
    return {"bot_id": bot_id, "connected": connected}


@router.post("/bots/{bot_id}/refresh")
def refresh_bot_metadata(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Trigger metadata refresh from Discord API."""
    get_user_bot(db, bot_id, user)  # Verify authorization
    result = discord_client.refresh_discord_metadata()
    if result is None:
        raise HTTPException(status_code=503, detail="Failed to refresh metadata")
    return {"success": True, **result}


@router.post("/bots/{bot_id}/users", response_model=UserStatusResponse)
def add_user_to_bot(
    bot_id: int,
    data: AddUserRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserStatusResponse:
    """Add another user to a bot's authorized users.

    Only existing authorized users can add new users.
    """
    bot = get_user_bot(db, bot_id, user)  # Verify current user is authorized

    # Get the target user
    target_user = db.get(User, data.user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already authorized
    if bot.is_authorized(target_user):
        return UserStatusResponse(status="already_authorized", user_id=data.user_id)

    bot.authorized_users.append(target_user)
    db.commit()

    return UserStatusResponse(status="added", user_id=data.user_id)


@router.delete("/bots/{bot_id}/users/{user_id}", response_model=UserStatusResponse)
def remove_user_from_bot(
    bot_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserStatusResponse:
    """Remove a user from a bot's authorized users.

    Users can remove themselves. Otherwise, only authorized users can remove others.
    Cannot remove the last authorized user (use delete bot instead).
    """
    bot = get_user_bot(db, bot_id, user)  # Verify current user is authorized

    # Get the target user
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if not bot.is_authorized(target_user):
        raise HTTPException(status_code=404, detail="User not authorized for this bot")

    if len(bot.authorized_users) == 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove the last authorized user. Delete the bot instead.",
        )

    bot.authorized_users.remove(target_user)
    db.commit()

    return UserStatusResponse(status="removed", user_id=user_id)


@router.get("/bots/{bot_id}/users", response_model=list[BotUserResponse])
def list_bot_users(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[BotUserResponse]:
    """List users authorized to use a bot."""
    bot = get_user_bot(db, bot_id, user)  # Verify current user is authorized

    return [
        BotUserResponse(id=cast(int, u.id), name=cast(str, u.name))
        for u in bot.authorized_users
    ]


# --- Server Endpoints ---


@router.get("/servers")
def list_servers(
    bot_id: int | None = None,
    auth: tuple[User, Session] = Depends(require_discord_access),
) -> list[DiscordServerResponse]:
    """List Discord servers.

    Note: Currently returns all servers. In a multi-bot setup, could filter
    by which servers the bot is in.
    """
    _, db = auth
    servers = fetch_servers(db)
    return [server_to_response(server) for server in servers]


@router.patch("/servers/{server_id}")
def update_server(
    server_id: int,
    updates: DiscordServerUpdate,
    auth: tuple[User, Session] = Depends(require_discord_access),
) -> DiscordServerResponse:
    """Update server collection settings."""
    _, db = auth
    server = db.get(DiscordServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if updates.collect_messages is not None:
        server.collect_messages = updates.collect_messages

    db.commit()
    db.refresh(server)

    return server_to_response(server)


# --- Channel Endpoints ---


@router.patch("/channels/{channel_id}")
def update_channel(
    channel_id: int,
    updates: DiscordChannelUpdate,
    auth: tuple[User, Session] = Depends(require_discord_access),
) -> DiscordChannelResponse:
    """Update channel collection settings.

    Set collect_messages to:
    - true: Always collect messages from this channel
    - false: Never collect messages from this channel
    - null: Inherit from server setting
    """
    _, db = auth
    channel = db.get(DiscordChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Allow setting to None (inherit) - check if key is present, not if value is None
    channel.collect_messages = updates.collect_messages

    db.commit()
    db.refresh(channel)

    return channel_to_response(channel)
