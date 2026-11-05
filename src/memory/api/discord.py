"""API endpoints for Discord bot, server, and channel management."""

import base64
import binascii
import hashlib
import json
import logging
import time
from typing import Annotated, Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from memory.api.auth import (
    assert_project_membership,
    get_interactive_user,
    resolve_user_filter,
)
from memory.api.https_url import HttpsUrl, require_chat_url
from memory.api.request_body import read_request_body_with_cap
from memory.api.webhook_events import record_event_arrival, register_body_seen
from memory.api.llm_keys import LLMKeyInput, LLMKeyView, apply_llm_key, llm_key_view
from memory.common import discord as discord_client
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.celery_app import HANDLE_DISCORD_EVENT
from memory.common.celery_app import app as celery_app
from memory.common.data_source_access import (
    enqueue_access_control_propagation,
    mark_access_control_changed_if_needed,
)
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.discord import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
)
from memory.common.discord_events import hand_off_server
from memory.common.discord_data import (
    fetch_servers,
    get_bot_for_user,
    get_user_bots,
    server_bot_ids,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discord", tags=["discord"])


# --- Bot Models ---


PublicKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$", to_lower=True)]
Snowflake = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]


Realtime = Literal["gateway", "webhook"]


class DiscordBotCreate(BaseModel):
    """Every bot needs its token; a webhook bot also its public key."""

    name: str
    token: str
    application_id: Snowflake | None = None
    # Kept on re-registration unless given; new bots default to the gateway
    realtime: Realtime | None = None
    public_key: PublicKey | None = None
    api_base_url: HttpsUrl | None = None


class DiscordBotUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    token: str | None = None
    realtime: Realtime | None = None
    public_key: PublicKey | None = None
    api_base_url: HttpsUrl | None = None


class DiscordBotResponse(BaseModel):
    id: str  # String to avoid JavaScript precision loss on large snowflake IDs
    application_id: str
    name: str
    is_active: bool
    has_token: bool
    realtime: str
    public_key: str | None
    api_base_url: str
    # Where webhook delivery posts; shown before switching a bot to it
    events_url: str
    last_event_at: str | None
    last_sync_at: str | None
    created_at: str | None
    updated_at: str | None
    connected: bool | None = None


# --- Server Models ---


class DiscordServerResponse(BaseModel):
    id: str  # String to avoid JavaScript precision loss on large snowflake IDs
    name: str
    description: str | None
    member_count: int | None
    collect_messages: bool
    last_sync_at: str | None
    channel_count: int
    # The requester's bots in this server
    bot_ids: list[str]
    # Access control
    project_id: int | None
    sensitivity: str
    llm_key: LLMKeyView


class DiscordServerUpdate(BaseModel):
    collect_messages: bool | None = None
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None
    llm_key: LLMKeyInput | None = None


# --- Channel Models ---


class DiscordChannelResponse(BaseModel):
    id: str  # String to avoid JavaScript precision loss on large snowflake IDs
    server_id: str | None
    server_name: str | None
    name: str
    channel_type: str
    collect_messages: bool | None
    effective_collect: bool
    # Access control
    project_id: int | None
    sensitivity: str


class DiscordChannelUpdate(BaseModel):
    collect_messages: bool | None = None
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None


# --- Helper Functions ---


def get_user_bot(db: Session, bot_id: int, user: User) -> DiscordBot:
    """Get a bot by ID, ensuring user is authorized."""
    bot = get_bot_for_user(db, bot_id, user)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


def require_discord_access(
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> tuple[User, Session]:
    """Require user to have at least one authorized Discord bot.

    Use as a dependency to gate access to server/channel endpoints.
    """
    if not get_user_bots(db, user.id):
        raise HTTPException(status_code=403, detail="No authorized Discord bots")
    return user, db


def events_url_for(bot: DiscordBot) -> str:
    return f"{settings.SERVER_URL}/discord/events/{bot.id}"


def application_id_from_token(token: str) -> int:
    """Discord tokens start with the base64 bot user id, which is the application id."""
    try:
        encoded_id = token.split(".")[0]
        padded = encoded_id + "=" * (-len(encoded_id) % 4)
        return int(base64.b64decode(padded).decode())
    except (ValueError, IndexError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid bot token format")


def require_usable(realtime: str, public_key: str | None, is_active: bool, has_token: bool) -> None:
    if realtime == "webhook" and not public_key:
        raise HTTPException(status_code=400, detail="Webhook delivery needs the public key that signs the events")
    if is_active and not has_token:
        raise HTTPException(status_code=400, detail="Add the bot's token to resume it")


def bot_to_response(
    bot: DiscordBot, connected: bool | None = None
) -> DiscordBotResponse:
    """Convert a DiscordBot model to a response."""
    return DiscordBotResponse(
        id=str(bot.id),
        application_id=str(bot.application_id),
        name=cast(str, bot.name),
        is_active=cast(bool, bot.is_active),
        has_token=bot.token_encrypted is not None,
        realtime=bot.realtime,
        public_key=bot.public_key,
        api_base_url=bot.api_base_url,
        events_url=events_url_for(bot),
        last_event_at=bot.last_event_at.isoformat() if bot.last_event_at else None,
        last_sync_at=bot.last_sync_at.isoformat() if bot.last_sync_at else None,
        created_at=bot.created_at.isoformat() if bot.created_at else None,
        updated_at=bot.updated_at.isoformat() if bot.updated_at else None,
        connected=connected,
    )


def server_to_response(
    server: DiscordServer, db: Session, user: User, bot_ids: list[int]
) -> DiscordServerResponse:
    """Convert a DiscordServer model to a response."""
    return DiscordServerResponse(
        id=str(server.id),
        name=cast(str, server.name),
        description=cast(str | None, server.description),
        member_count=cast(int | None, server.member_count),
        collect_messages=cast(bool, server.collect_messages),
        last_sync_at=server.last_sync_at.isoformat() if server.last_sync_at else None,
        channel_count=len(server.channels),
        bot_ids=[str(bot_id) for bot_id in bot_ids],
        project_id=server.project_id,
        sensitivity=cast(str, server.sensitivity) or "basic",
        llm_key=llm_key_view(db, user, server),
    )


def channel_to_response(channel: DiscordChannel) -> DiscordChannelResponse:
    """Convert a DiscordChannel model to a response."""
    return DiscordChannelResponse(
        id=str(channel.id),
        server_id=str(channel.server_id) if channel.server_id else None,
        server_name=channel.server.name if channel.server else None,
        name=cast(str, channel.name),
        channel_type=cast(str, channel.channel_type),
        collect_messages=channel.collect_messages,
        effective_collect=channel.should_collect,
        project_id=channel.project_id,
        sensitivity=cast(str, channel.sensitivity) or "basic",
    )


# --- Bot Endpoints ---


@router.get("/bots")
def list_bots(
    user_id: int | None = None,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[DiscordBotResponse]:
    """List Discord bots. Admins can view any user's authorized bots."""
    resolved_user_id = resolve_user_filter(user_id, user, db)
    # If resolved_user_id is None (admin viewing all), show all bots
    if resolved_user_id is None:
        bots = db.query(DiscordBot).all()
    else:
        bots = get_user_bots(db, resolved_user_id)

    connected = discord_client.connected_bots()
    return [bot_to_response(bot, connected=bot.id in connected) for bot in bots]


@router.post("/bots")
def create_bot(
    data: DiscordBotCreate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> DiscordBotResponse:
    """Register a Discord bot for the caller.

    The application id comes from the token (a given one must match). Each
    user has their own row per application; registering one again updates it.
    """
    application_id = application_id_from_token(data.token)
    if data.application_id is not None and int(data.application_id) != application_id:
        raise HTTPException(status_code=400, detail="The bot token belongs to another application")
    if data.api_base_url:
        require_chat_url(data.api_base_url, user)

    existing = (
        db.query(DiscordBot)
        .filter(
            DiscordBot.application_id == application_id,
            DiscordBot.created_by_user_id == user.id,
        )
        .first()
    )
    realtime = data.realtime or (existing.realtime if existing else "gateway")
    require_usable(realtime, data.public_key or (existing and existing.public_key), True, True)
    bot = existing or DiscordBot(application_id=application_id, name=data.name, created_by_user_id=user.id)
    if existing and existing.token_encrypted is None:
        bot.is_active = True
    bot.token = data.token
    bot.realtime = realtime
    if data.public_key:
        bot.public_key = data.public_key
    if data.api_base_url:
        bot.api_base_url = data.api_base_url

    db.add(bot)
    db.commit()
    db.refresh(bot)

    return bot_to_response(bot)


@router.patch("/bots/{bot_id}")
def update_bot(
    bot_id: int,
    updates: DiscordBotUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> DiscordBotResponse:
    """Update a Discord bot, including switching its real-time delivery."""
    bot = get_user_bot(db, bot_id, user)
    if updates.token and application_id_from_token(updates.token) != bot.application_id:
        raise HTTPException(status_code=400, detail="The bot token belongs to another application")
    if updates.api_base_url:
        require_chat_url(updates.api_base_url, user)
    require_usable(
        updates.realtime or bot.realtime,
        updates.public_key or bot.public_key,
        bot.is_active if updates.is_active is None else updates.is_active,
        bool(updates.token) or bot.token_encrypted is not None,
    )

    if updates.name is not None:
        bot.name = updates.name
    if updates.is_active is not None:
        bot.is_active = updates.is_active
    if updates.token:
        # Bots registered before tokens were required were paused for lack of one.
        if bot.token_encrypted is None and updates.is_active is None:
            bot.is_active = True
        bot.token = updates.token
    if updates.realtime is not None:
        bot.realtime = updates.realtime
    if updates.public_key:
        bot.public_key = updates.public_key
    if updates.api_base_url:
        bot.api_base_url = updates.api_base_url

    db.commit()
    db.refresh(bot)

    return bot_to_response(bot)


@router.delete("/bots/{bot_id}")
def delete_bot(
    bot_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Delete a bot owned by the caller."""
    bot = get_user_bot(db, bot_id, user)
    for server in db.query(DiscordServer).filter(DiscordServer.bot_id == bot.id).all():
        hand_off_server(db, server, bot)
    db.delete(bot)

    db.commit()
    return {"status": "deleted"}


@router.get("/bots/{bot_id}/health")
def get_bot_health(
    bot_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Get real-time connection status for a bot."""
    get_user_bot(db, bot_id, user)  # Verify authorization
    connected = discord_client.is_collector_healthy(bot_id)
    return {"bot_id": bot_id, "connected": connected}


@router.post("/bots/{bot_id}/refresh")
def refresh_bot_metadata(
    bot_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Record this bot's servers, channels and members again, if it is on the gateway."""
    get_user_bot(db, bot_id, user)  # Verify authorization
    try:
        result = discord_client.refresh_discord_metadata(bot_id)
    except discord_client.BotNotConnected:
        raise HTTPException(status_code=409, detail="Bot is not connected to the gateway")
    if result is None:
        raise HTTPException(status_code=503, detail="Failed to refresh metadata")
    return {"success": True, **result}


@router.get("/bots/{bot_id}/invite")
def get_bot_invite_url(
    bot_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Generate a Discord invite URL for the bot.

    Returns a URL that can be used to add the bot to a Discord server.
    Permissions: Send Messages (2048) + Read Message History (65536) + View Channels (1024)
    """
    bot = get_user_bot(db, bot_id, user)
    permissions = 2048 + 65536 + 1024  # send messages, read history, view channels
    invite_url = f"https://discord.com/oauth2/authorize?client_id={bot.application_id}&scope=bot&permissions={permissions}"
    return {"invite_url": invite_url}


# --- Server Endpoints ---


def user_can_manage_server(user: User, db: Session, server: DiscordServer) -> bool:
    """Check that the user has a bot linked to this server.

    Returns True when:
    - The user has admin scope (superadmin)
    - The server's bot_id belongs to a bot the user is authorised for

    Servers with bot_id IS NULL (legacy rows from before the bot_id migration,
    or rows whose bot has been deleted) are admin-only — treating them as
    "anyone can manage" would be a privilege-escalation back door. So are
    servers only pushed events have claimed (not ``verified``).
    """
    if has_admin_scope(user):
        return True
    if server.bot_id is None or not server.verified:
        return False
    user_bot_ids = {bot.id for bot in get_user_bots(db, user.id)}
    return server.bot_id in user_bot_ids


def user_can_manage_channel(user: User, db: Session, channel: DiscordChannel) -> bool:
    """A server channel follows its server; a DM belongs to the owner of the
    bot that first delivered it, and without one to admins only."""
    if channel.server is not None:
        return user_can_manage_server(user, db, channel.server)
    if has_admin_scope(user):
        return True
    return channel.bot is not None and channel.bot.created_by_user_id == user.id


def user_bots_by_server(db: Session, user: User) -> dict[int, list[int]]:
    return server_bot_ids(db, [bot.id for bot in get_user_bots(db, user.id)])


@router.get("/servers")
def list_servers(
    auth: tuple[User, Session] = Depends(require_discord_access),
) -> list[DiscordServerResponse]:
    """List Discord servers owned by the requesting user's bots."""
    user, db = auth
    servers = fetch_servers(db, user_id=user.id)
    bots_by_server = user_bots_by_server(db, user)
    return [server_to_response(server, db, user, bots_by_server.get(server.id, [])) for server in servers]


@router.patch("/servers/{server_id}")
def update_server(
    server_id: int,
    updates: DiscordServerUpdate,
    auth: tuple[User, Session] = Depends(require_discord_access),
) -> DiscordServerResponse:
    """Update server collection settings."""
    user, db = auth
    server = db.get(DiscordServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if not user_can_manage_server(user, db, server):
        raise HTTPException(status_code=404, detail="Server not found")

    snap_pid, snap_sens = server.project_id, server.sensitivity
    if updates.collect_messages is not None:
        server.collect_messages = updates.collect_messages
    if "project_id" in updates.model_fields_set:
        if updates.project_id is not None:
            assert_project_membership(db, user, updates.project_id)
        server.project_id = updates.project_id
    if updates.sensitivity is not None:
        server.sensitivity = updates.sensitivity
    apply_llm_key(db, user, server, updates.llm_key)

    access_changed = mark_access_control_changed_if_needed(
        server,
        snapshot_project_id=snap_pid,
        snapshot_sensitivity=snap_sens,
    )

    db.commit()
    db.refresh(server)

    if access_changed:
        enqueue_access_control_propagation("discord_server", server)

    return server_to_response(server, db, user, user_bots_by_server(db, user).get(server.id, []))


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
    user, db = auth
    channel = db.get(DiscordChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if not user_can_manage_channel(user, db, channel):
        raise HTTPException(status_code=404, detail="Channel not found")

    snap_pid, snap_sens = channel.project_id, channel.sensitivity
    # None is a value here (inherit, no project), so only fields sent in the body change.
    if "collect_messages" in updates.model_fields_set:
        channel.collect_messages = updates.collect_messages
    if "project_id" in updates.model_fields_set:
        if updates.project_id is not None:
            assert_project_membership(db, user, updates.project_id)
        channel.project_id = updates.project_id
    if updates.sensitivity is not None:
        channel.sensitivity = updates.sensitivity

    access_changed = mark_access_control_changed_if_needed(
        channel,
        snapshot_project_id=snap_pid,
        snapshot_sensitivity=snap_sens,
    )

    db.commit()
    db.refresh(channel)

    if access_changed:
        enqueue_access_control_propagation("discord_channel", channel)

    return channel_to_response(channel)


# --- Webhook Events ---
#
# Discord's Webhook Events envelope: {version, application_id, type: 0 PING |
# 1 event, event: {type, timestamp, data}}, signed with Ed25519 over
# timestamp + body. Events carry gateway types (MESSAGE_CREATE ...) with the
# gateway's data. Discord expects a 204 within 3 s and probes with bad
# signatures, which must get a 401.

DISCORD_EVENT_MAX_BODY_BYTES = 1_048_576
DISCORD_EVENT_MAX_TS_SKEW_SECONDS = 5 * 60
DISCORD_PING = 0
DISCORD_EVENT = 1
CARRIED_EVENT_TYPES = frozenset(
    {
        "MESSAGE_CREATE",
        "MESSAGE_UPDATE",
        "MESSAGE_DELETE",
        "MESSAGE_DELETE_BULK",
        "MESSAGE_REACTION_ADD",
        "MESSAGE_REACTION_REMOVE",
        "MESSAGE_REACTION_REMOVE_ALL",
        "MESSAGE_REACTION_REMOVE_EMOJI",
        "THREAD_CREATE",
        "THREAD_UPDATE",
    }
)


def verify_discord_signature(public_key_hex: str, timestamp: str, body: bytes, signature_hex: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature_hex), timestamp.encode() + body)
    except (ValueError, InvalidSignature):
        return False
    return True


def is_fresh(timestamp: str) -> bool:
    try:
        return abs(time.time() - int(timestamp)) <= DISCORD_EVENT_MAX_TS_SKEW_SECONDS
    except ValueError:
        return False


@router.post("/events/{bot_id}", status_code=204)
async def discord_events(bot_id: int, request: Request, db: Session = Depends(get_session)) -> Response:
    """Receive Discord Webhook Events for one bot; the work is queued."""
    unauthorized = Response(status_code=401)
    try:
        body = await read_request_body_with_cap(request, DISCORD_EVENT_MAX_BODY_BYTES)
    except HTTPException:
        return unauthorized
    timestamp = request.headers.get("x-signature-timestamp", "")
    signature = request.headers.get("x-signature-ed25519", "")

    bot = db.get(DiscordBot, bot_id)
    if bot is None or not bot.is_active or bot.realtime != "webhook" or not bot.public_key:
        return unauthorized
    if not is_fresh(timestamp) or not verify_discord_signature(bot.public_key, timestamp, body, signature):
        logger.info("Discord event rejected for bot %s", bot_id)
        return unauthorized

    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Response(status_code=400)

    if str(envelope.get("application_id")) != str(bot.application_id):
        logger.info("Discord event for another application rejected for bot %s", bot_id)
        return unauthorized

    record_event_arrival(db, bot)
    # Discord retries a delivery it saw fail; the events are idempotent, so a
    # replay within the signature window is just acknowledged.
    if not register_body_seen(hashlib.sha256(body).hexdigest(), DISCORD_EVENT_MAX_TS_SKEW_SECONDS + 60):
        return Response(status_code=204)
    if envelope.get("type") != DISCORD_EVENT:
        return Response(status_code=204)
    event = envelope.get("event") or {}
    event_type = event.get("type")
    if event_type in CARRIED_EVENT_TYPES and isinstance(event.get("data"), dict):
        celery_app.send_task(
            HANDLE_DISCORD_EVENT,
            kwargs={"bot_id": bot.id, "event_type": event_type, "data": event["data"]},
        )
    return Response(status_code=204)
