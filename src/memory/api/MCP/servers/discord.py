"""MCP subserver for Discord messaging."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import desc, or_
from sqlalchemy.orm import joinedload
from fastmcp.server.dependencies import get_access_token

from memory.agents.subscription_input import new_tag, preamble, untrusted
from memory.api.MCP.access import (
    build_mcp_user_access_filter,
    fetch_user_by_token,
    get_mcp_current_user,
)
from memory.api.MCP.visibility import READ_ONLY, require_scopes, visible_when
from memory.common import discord as discord_client
from memory.common import settings
from memory.common.access_control import UserProxy, apply_access_filter_to_query, has_admin_scope
from memory.common.dates import parse_iso_datetime
from memory.common.scopes import (
    SCOPE_DISCORD,
    SCOPE_DISCORD_WRITE,
    SCOPE_PEOPLE,
    has_scope,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import DiscordBot, DiscordChannel, DiscordMessage, DiscordServer, DiscordUser
from memory.common.db.models.discord import DM_CHANNEL_TYPES
from memory.common.people import discord_ids, find_person
from memory.common.reactions import reaction_counts
from memory.common.discord_data import (
    caller_can_see_discord_user,
    fetch_servers,
    find_visible_channel,
    visible_channels,
)

logger = logging.getLogger(__name__)

discord_mcp = FastMCP("memory-discord")

CHANNEL_FIELDS: dict[str, Callable[[DiscordChannel], Any]] = {
    # Snowflakes as strings to avoid JS precision loss
    "id": lambda ch: str(ch.id),
    "name": lambda ch: ch.name,
    "type": lambda ch: ch.channel_type,
    "server_id": lambda ch: str(ch.server_id) if ch.server_id else None,
    "server_name": lambda ch: ch.server.name if ch.server else None,
    "category_id": lambda ch: str(ch.category_id) if ch.category_id else None,
    "collect_messages": lambda ch: ch.collect_messages,
    "effective_collect": lambda ch: ch.should_collect,
    "project_id": lambda ch: ch.project_id,
    "sensitivity": lambda ch: ch.sensitivity,
}
DEFAULT_CHANNEL_FIELDS = ["id", "name", "type", "server_id"]
# Server admins set these, and any member can name a thread.
NAME_FIELDS = frozenset({"name", "server_name"})


def channel_fields(tag: str, channel: DiscordChannel, fields: list[str]) -> dict[str, Any]:
    values = {f: CHANNEL_FIELDS[f](channel) for f in fields}
    return {
        f: (v and untrusted(tag, channel.id, f, v)) if f in NAME_FIELDS else v
        for f, v in values.items()
    }


def fetch_channels(
    session: DBSession,
    caller: UserProxy,
    server: str | None,
    include_dms: bool,
    name: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """The Discord channels visible to ``caller`` (see ``visible_channels``).

    ``server`` is a numeric id or name, resolved only among visible servers so
    another user's server reads as not found. ``name`` is a case-insensitive
    substring match.
    """
    fields = fields or DEFAULT_CHANNEL_FIELDS
    invalid = set(fields) - CHANNEL_FIELDS.keys()
    if invalid:
        raise ValueError(f"Invalid fields: {sorted(invalid)}. Available: {sorted(CHANNEL_FIELDS)}")

    tag = new_tag()
    query = visible_channels(session, caller).options(joinedload(DiscordChannel.server))
    if server is not None:
        servers = fetch_servers(session, user_id=None if has_admin_scope(caller) else caller.id)
        match = next((s for s in servers if server in (str(s.id), s.name)), None)
        if match is None:
            return {"untrusted": preamble(tag), "channels": [], "count": 0, "error": f"Server '{server}' not found"}
        query = query.filter(DiscordChannel.server_id == match.id)
    if not include_dms:
        query = query.filter(DiscordChannel.channel_type.notin_(DM_CHANNEL_TYPES))
    if name:
        query = query.filter(DiscordChannel.name.ilike(f"%{name}%"))

    channels = [channel_fields(tag, ch, fields) for ch in query.order_by(DiscordChannel.name)]
    return {"untrusted": preamble(tag), "channels": channels, "count": len(channels)}


async def has_discord_bots(user_info: dict, session: DBSession | None) -> bool:
    """Visibility checker: only show Discord tools if user has authorized bots."""
    token = user_info.get("token")
    if not token:
        return False

    def _check() -> bool:
        # Create our own session to avoid threading issues with passed session
        with make_session() as local_session:
            user = fetch_user_by_token(local_session, token)
            if not user:
                return False
            # Check if user has any authorized Discord bots
            return len(user.discord_bots) > 0

    return await asyncio.to_thread(_check)


def _get_user_and_bots(session: DBSession) -> tuple[int, list[DiscordBot]]:
    """Get the current user ID and their authorized bots."""
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    user = fetch_user_by_token(session, access_token.token)
    if not user:
        raise ValueError("User not found")

    return user.id, list(user.discord_bots)


def _get_default_bot(session: DBSession) -> DiscordBot:
    """Get the user's default (first) Discord bot."""
    _, bots = _get_user_and_bots(session)
    if not bots:
        raise ValueError("No Discord bots configured for this user")
    return bots[0]


def require_caller(session: DBSession) -> UserProxy:
    caller = get_mcp_current_user(session)
    if caller is None:
        raise ValueError("Not authenticated")
    return caller


def _to_snowflake(value: int | str) -> int:
    """Convert a snowflake ID (int or string) to int."""
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid snowflake ID: '{value}' is not a valid integer")


def resolve_bot(session: DBSession, bot_id: str | None) -> int:
    """Resolve and authorize a bot snowflake (string at the boundary)."""
    if bot_id is not None:
        bot_pk = _to_snowflake(bot_id)
        bot = session.get(DiscordBot, bot_pk)
        if not bot:
            raise ValueError(f"Bot {bot_id} not found")
        _, user_bots = _get_user_and_bots(session)
        if bot not in user_bots:
            raise ValueError(f"You don't have access to bot {bot_id}")
        return bot.id

    return _get_default_bot(session).id


async def send_to_channel(
    session: DBSession,
    bot_id: str | None,
    message: str,
    channel_id: str | None,
    channel_name: str | None,
) -> dict[str, Any]:
    """Send to a channel the caller can see, with the bot of the channel's server."""
    channel = find_visible_channel(session, require_caller(session), channel_id, channel_name)
    target = str(channel.id)
    server = session.get(DiscordServer, channel.server_id) if channel.server_id else None
    server_bot = server.bot_id if server else None

    if bot_id is not None:
        sender = resolve_bot(session, bot_id)
        if server_bot is not None and sender != server_bot:
            raise ValueError(f"Bot {bot_id} is not in this channel's server")
    else:
        sender = server_bot or channel.bot_id or resolve_bot(session, None)

    success = await asyncio.to_thread(
        discord_client.send_to_channel, sender, target, message
    )
    preview = message[:100] + "..." if len(message) > 100 else message
    return {"success": success, "type": "channel", "target": target, "message_preview": preview}


async def send_dm(
    session: DBSession,
    bot_id: int,
    message: str,
    user_id: str | None,
    username: str | None,
) -> dict[str, Any]:
    """Resolve user and send DM, with Person fallback on failure.

    Note: returns {"success": False, ...} with suggestions instead of raising
    when the Discord user is not found. This is intentional -- it lets MCP tool
    callers display suggestions to the end user rather than surfacing a raw error.
    """
    caller = require_caller(session)
    target = resolve_discord_user(session, caller, user_id, username)
    if target is None:
        tag = new_tag()
        suggestions = suggest_people(session, caller, username or "", tag)
        error = f"Discord user '{user_id or username}' not found"
        if suggestions:
            error += ", but found matching people"
        return {"untrusted": preamble(tag), "success": False, "error": error, "suggestions": suggestions}

    success = await asyncio.to_thread(discord_client.send_dm, bot_id, target, message)
    preview = message[:100] + "..." if len(message) > 100 else message
    return {"success": success, "type": "dm", "target": target, "message_preview": preview}


def resolve_discord_user(
    session: DBSession,
    caller: UserProxy,
    user_id: str | None,
    username: str | None,
) -> str | None:
    """The id of a Discord user the caller can see, by id or else username; None otherwise."""
    if user_id is not None:
        discord_id = _to_snowflake(user_id)
        return str(discord_id) if caller_can_see_discord_user(session, caller, discord_id) else None

    matches = session.query(DiscordUser.id).filter(
        or_(DiscordUser.username == username, DiscordUser.display_name == username)
    )
    return next(
        (str(m.id) for m in matches if caller_can_see_discord_user(session, caller, m.id)),
        None,
    )


def suggest_people(session: DBSession, caller: UserProxy, query: str, tag: str) -> list[dict[str, str]]:
    """Find Person records that might match a failed username lookup.

    Returns at most one suggestion (find_person returns a single match), and
    only of a person the caller may read whose Discord account they could DM.
    """
    if not has_scope(caller.scopes, SCOPE_PEOPLE):
        return []
    person = find_person(session, query)
    if not person:
        return []

    accounts = person.discord_accounts
    discord_id = str(accounts[0].id) if accounts else next(iter(discord_ids(person.contact_info)), None)
    discord_username = accounts[0].username if accounts else None
    if discord_id is None or not caller_can_see_discord_user(session, caller, int(discord_id)):
        return []

    return [{
        "person": person.identifier,
        # Ingest takes both from the Discord account, which its owner names.
        "display_name": untrusted(tag, person.id, "display_name", person.display_name),
        "discord_id": str(discord_id),
        "discord_username": untrusted(tag, person.id, "discord_username", discord_username or ""),
    }]


@discord_mcp.tool()
@visible_when(require_scopes(SCOPE_DISCORD_WRITE), has_discord_bots)
async def send_message(
    message: str,
    channel_id: str | None = None,
    channel_name: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """
    Send a Discord message to a channel or user via DM.

    You must specify either:
    - channel_id or channel_name to send to a channel
    - user_id or username to send a DM

    Args:
        message: The message content to send
        channel_id: Discord channel ID (snowflake) to send to
        channel_name: Discord channel name to send to (will search for it)
        user_id: Discord user ID (snowflake) to DM
        username: Discord username to DM (will search for it)
        bot_id: Optional bot ID to send with; a channel defaults to its server's bot, a DM to your first bot

    Returns:
        Dict with success status and details
    """
    if not message:
        raise ValueError("Message cannot be empty")

    has_channel = channel_id is not None or channel_name is not None
    has_user = user_id is not None or username is not None

    if not has_channel and not has_user:
        raise ValueError("Must specify either channel_id/channel_name or user_id/username")
    if has_channel and has_user:
        raise ValueError("Cannot specify both channel and user destinations")

    with make_session() as session:
        if has_channel:
            return await send_to_channel(session, bot_id, message, channel_id, channel_name)

        return await send_dm(session, resolve_bot(session, bot_id), message, user_id, username)


@discord_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_DISCORD), has_discord_bots)
async def channel_history(
    channel_id: str | None = None,
    channel_name: str | None = None,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    """
    Get message history from a Discord channel. Only messages you can read are
    returned, as in search: in a server or channel with no project they are
    admin-only, so the list can be empty.

    Args:
        channel_id: Discord channel ID (snowflake)
        channel_name: Discord channel name (will search for it)
        limit: Maximum number of messages to return (default 50, max 200)
        before: ISO datetime - only get messages before this time
        after: ISO datetime - only get messages after this time

    Returns:
        Dict with untrusted (names the tag its name, author, text, filename and
        content_type blocks carry), channel (id, name, server_id, server_name),
        messages oldest first, count and limit; each message has id, message_id,
        author, author_id, sent_at, edited_at, text (ending in a line per
        attachment), attachments, is_pinned, reactions and reply_to (a
        message_id). Each attachment has filename, content_type, size and file:
        pass file to core_fetch_file for the stored copy (null when there is
        none; only images are stored)
    """
    if channel_id is None and channel_name is None:
        raise ValueError("Must specify either channel_id or channel_name")

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    # Parse datetime filters
    before_dt = parse_iso_datetime(before)
    if before and before_dt is None:
        raise ValueError(f"Invalid 'before' datetime format: {before}")
    after_dt = parse_iso_datetime(after)
    if after and after_dt is None:
        raise ValueError(f"Invalid 'after' datetime format: {after}")

    with make_session() as session:
        return await asyncio.to_thread(
            fetch_channel_history,
            session,
            require_caller(session),
            channel_id,
            channel_name,
            before_dt,
            after_dt,
            limit,
        )


def attachment_fields(tag: str, message: DiscordMessage) -> list[dict[str, Any]]:
    """Each attachment; pass ``file`` to core_fetch_file for the stored copy."""
    return [
        {
            "filename": untrusted(tag, message.id, "filename", a["filename"] or ""),
            "content_type": a["content_type"] and untrusted(tag, message.id, "content_type", a["content_type"]),
            "size": a["size"],
            "file": a["file"],
        }
        for a in message.attachment_files
    ]


def thread_message(tag: str, message: DiscordMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "message_id": str(message.message_id),
        "author": (
            untrusted(tag, message.id, "author", message.author.name)
            if message.author
            else str(message.author_id)
        ),
        "sent_at": message.sent_at.isoformat(),
        "text": untrusted(tag, message.id, "text", message.readable_content),
        "attachments": attachment_fields(tag, message),
    }


def history_message(tag: str, message: DiscordMessage) -> dict[str, Any]:
    return thread_message(tag, message) | {
        "author_id": str(message.author_id),
        "edited_at": message.edited_at and message.edited_at.isoformat(),
        "is_pinned": message.is_pinned,
        "reactions": reaction_counts(message.reactions),
        "reply_to": message.reply_to_message_id and str(message.reply_to_message_id),
    }


def fetch_channel_history(
    session: DBSession,
    caller: UserProxy,
    channel_id: str | None,
    channel_name: str | None,
    before_dt: datetime | None,
    after_dt: datetime | None,
    limit: int,
) -> dict[str, Any]:
    channel = find_visible_channel(session, caller, channel_id, channel_name)
    # A visible channel can hold messages whose project or sensitivity the caller can't read.
    query = apply_access_filter_to_query(
        session.query(DiscordMessage)
        .options(joinedload(DiscordMessage.author))
        .filter(DiscordMessage.channel_id == channel.id),
        build_mcp_user_access_filter(session, caller),
    )
    if before_dt:
        query = query.filter(DiscordMessage.sent_at < before_dt)
    if after_dt:
        query = query.filter(DiscordMessage.sent_at > after_dt)
    messages = query.order_by(desc(DiscordMessage.sent_at)).limit(limit).all()
    tag = new_tag()
    return {
        "untrusted": preamble(tag),
        "channel": channel_fields(tag, channel, ["id", "name", "server_id", "server_name"]),
        "messages": [history_message(tag, m) for m in reversed(messages)],
        "count": len(messages),
        "limit": limit,
    }


def fetch_thread(session: DBSession, item_id: int, limit: int) -> dict[str, Any]:
    access = build_mcp_user_access_filter(session, require_caller(session))

    def readable(*criteria):
        return apply_access_filter_to_query(session.query(DiscordMessage).filter(*criteria), access)

    item = readable(DiscordMessage.id == item_id).one_or_none()
    if item is None:
        raise ValueError(f"Discord message {item_id} not found")
    # A thread's id is the id of the message that started it.
    channel = item.channel
    in_thread = channel is not None and (channel.channel_type == "thread" or channel.parent_id is not None)
    thread_id = item.channel_id if in_thread else item.thread_id
    tag = new_tag()
    if thread_id is None:
        return {"untrusted": preamble(tag), "thread_id": None, "parent": thread_message(tag, item), "replies": []}
    parent = readable(DiscordMessage.message_id == thread_id).first()
    replies = (
        readable(DiscordMessage.channel_id == thread_id, DiscordMessage.message_id != thread_id)
        .order_by(desc(DiscordMessage.sent_at))
        .limit(limit)
        .all()
    )
    return {
        "untrusted": preamble(tag),
        "thread_id": str(thread_id),
        "parent": parent and thread_message(tag, parent),
        "replies": [thread_message(tag, m) for m in reversed(replies)],
    }


@discord_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_DISCORD))
async def thread(item_id: int, limit: int = settings.CHAT_THREAD_REPLIES) -> dict[str, Any]:
    """
    Get the thread a stored Discord message is in (or started): the message
    that started it and the latest replies. Use on a message from search or a
    subscription delivery to see the conversation around it. Only messages you can
    read are returned.

    Args:
        item_id: The message's item id (the `id` in search results or a delivery's input)
        limit: How many of the latest replies to return, oldest first (max 100)

    Returns:
        Dict with untrusted (names the tag its author, text, filename and
        content_type blocks carry), thread_id (null when the message isn't in a
        thread), parent (null if you can't read it) and replies; each message
        has id, message_id, author, sent_at, text and attachments, as in
        discord_channel_history
    """
    with make_session() as session:
        return await asyncio.to_thread(fetch_thread, session, item_id, max(1, min(100, limit)))


@discord_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_DISCORD), has_discord_bots)
async def list_channels(
    server: str | None = None,
    name: str | None = None,
    include_dms: bool = False,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """
    List Discord channels in servers your bots are in (admins see all).

    Args:
        server: Filter by server - numeric ID or server name
        name: Case-insensitive substring of the channel name
        include_dms: Include DMs and group DMs your bots delivered (default False)
        fields: Fields to include for each channel.
                Default: ["id", "name", "type", "server_id"]
                Available fields:
                - id: Channel ID (snowflake, as string)
                - name: Channel name
                - type: text, voice, dm, group_dm or thread
                - server_id: Server (guild) ID
                - server_name: Server (guild) name
                - category_id: Category ID
                - collect_messages: The channel's own setting (null = inherit from server)
                - effective_collect: Whether messages are collected (after server inheritance)
                - project_id: Associated project ID for access control
                - sensitivity: Sensitivity level (public, basic, internal, confidential)

    Returns:
        Dict with untrusted (names the tag its name and server_name blocks
        carry), channels and count
    """
    with make_session() as session:
        return await asyncio.to_thread(
            fetch_channels, session, require_caller(session), server, include_dms, name, fields
        )
