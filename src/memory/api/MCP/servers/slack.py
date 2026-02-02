"""MCP subserver for Slack messaging."""

import asyncio
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import UserSession
from memory.common.db.models.slack import (
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.slack import async_slack_call

logger = logging.getLogger(__name__)

slack_mcp = FastMCP("memory-slack")


# --- Visibility Checker ---


async def has_slack_workspaces(user_info: dict, session: DBSession | None) -> bool:
    """Visibility checker: only show Slack tools if user has connected workspaces."""
    token = user_info.get("token")
    if not token:
        return False

    def _check() -> bool:
        # Create our own session to avoid threading issues with passed session
        with make_session() as local_session:
            user_session = local_session.get(UserSession, token)
            if not user_session or not user_session.user:
                return False
            # Check if user has any Slack credentials
            return len(user_session.user.slack_credentials) > 0

    return await asyncio.to_thread(_check)


# --- Sync DB Helpers (run in thread) ---


def _get_user_credentials(session: DBSession, token: str) -> list[SlackUserCredentials]:
    """Get the current user's Slack credentials."""
    user_session = session.get(UserSession, token)
    if not user_session or not user_session.user:
        raise ValueError("User not found")
    return list(user_session.user.slack_credentials)


def _get_default_credentials(session: DBSession, token: str) -> SlackUserCredentials:
    """Get the user's default (first) Slack credentials."""
    credentials = _get_user_credentials(session, token)
    if not credentials:
        raise ValueError("No Slack workspaces connected")
    return credentials[0]


def _get_credentials_for_workspace(
    session: DBSession, token: str, workspace_id: str
) -> SlackUserCredentials:
    """Get credentials for a specific workspace, verifying user access."""
    credentials = _get_user_credentials(session, token)
    for cred in credentials:
        if cred.workspace_id == workspace_id:
            return cred
    raise ValueError(f"Workspace {workspace_id} not found or not accessible")


def _resolve_channel(session: DBSession, workspace_id: str, channel: str) -> str:
    """Resolve a channel name to ID, or return the ID if already an ID."""
    # Detect if channel is an ID or name
    # Slack channel IDs start with C (public), D (DM), or G (private/group)
    if channel and channel[0] in "CDG" and channel[1:].isalnum():
        return channel

    # It's a name - look it up
    db_channel = (
        session.query(SlackChannel)
        .filter(
            SlackChannel.workspace_id == workspace_id,
            SlackChannel.name == channel,
        )
        .first()
    )
    if not db_channel:
        raise ValueError(f"Channel '{channel}' not found in workspace")
    return db_channel.id


def _get_slack_token_for_dm(token: str, workspace_id: str | None) -> str:
    """Get the Slack access token for sending a DM (runs in thread)."""
    with make_session() as session:
        if workspace_id:
            credentials = _get_credentials_for_workspace(session, token, workspace_id)
        else:
            credentials = _get_default_credentials(session, token)
        if not credentials.access_token:
            raise ValueError("No access token for workspace")
        return credentials.access_token


async def _open_dm_channel(slack_token: str, user_id: str) -> str:
    """Open a DM channel with a user, returning the channel ID."""
    data = await async_slack_call(slack_token, "conversations.open", users=user_id)
    channel = data.get("channel", {})
    channel_id = channel.get("id")
    if not channel_id:
        raise ValueError(f"Failed to open DM with user {user_id}")
    return channel_id


def _get_credentials_for_send(
    token: str, workspace_id: str | None, channel: str
) -> tuple[str, str, str]:
    """
    Get credentials needed for sending a message (runs in thread).

    Returns: (workspace_id, channel_id, access_token)
    """
    with make_session() as session:
        if workspace_id:
            credentials = _get_credentials_for_workspace(session, token, workspace_id)
        else:
            credentials = _get_default_credentials(session, token)

        channel_id = _resolve_channel(session, credentials.workspace_id, channel)
        access_token = credentials.access_token

        if not access_token:
            raise ValueError("No access token for workspace")

        return credentials.workspace_id, channel_id, access_token


# All available channel fields that can be requested
CHANNEL_FIELDS = {
    "id",
    "name",
    "type",
    "is_private",
    "is_archived",
    "workspace_id",
    "collect_messages",
    "effective_collect",
    "last_message_ts",
    "project_id",
    "sensitivity",
}

# Default fields for list_channels
DEFAULT_CHANNEL_FIELDS = ["id", "name", "type", "is_private"]


def _channel_to_dict(channel: SlackChannel, fields: list[str]) -> dict[str, Any]:
    """Convert a channel to a dict with only the requested fields."""
    field_map = {
        "id": lambda ch: ch.id,
        "name": lambda ch: ch.name,
        "type": lambda ch: ch.channel_type,
        "is_private": lambda ch: ch.is_private,
        "is_archived": lambda ch: ch.is_archived,
        "workspace_id": lambda ch: ch.workspace_id,
        "collect_messages": lambda ch: ch.collect_messages,
        "effective_collect": lambda ch: ch.should_collect,
        "last_message_ts": lambda ch: ch.last_message_ts,
        "project_id": lambda ch: ch.project_id,
        "sensitivity": lambda ch: ch.sensitivity or "basic",
    }
    return {f: field_map[f](channel) for f in fields if f in field_map}


def _get_channels_data(
    token: str,
    workspace_id: str | None,
    include_private: bool,
    include_dms: bool,
    include_archived: bool,
    fields: list[str],
) -> dict[str, Any]:
    """Get channels list data (runs in thread)."""
    with make_session() as session:
        if workspace_id:
            credentials = _get_credentials_for_workspace(session, token, workspace_id)
        else:
            credentials = _get_default_credentials(session, token)

        workspace = session.get(SlackWorkspace, credentials.workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")

        query = session.query(SlackChannel).filter(
            SlackChannel.workspace_id == credentials.workspace_id,
        )

        if not include_archived:
            query = query.filter(SlackChannel.is_archived == False)  # noqa: E712

        if not include_private:
            query = query.filter(SlackChannel.is_private == False)  # noqa: E712

        if not include_dms:
            query = query.filter(
                SlackChannel.channel_type.notin_(["dm", "mpim", "private_channel"])
            )

        channels = query.order_by(SlackChannel.name).all()

        return {
            "workspace_id": credentials.workspace_id,
            "workspace_name": workspace.name,
            "channels": [_channel_to_dict(ch, fields) for ch in channels],
            "count": len(channels),
        }


def _get_history_data(
    token: str, workspace_id: str | None, channel: str
) -> tuple[str, str, str]:
    """
    Get data needed for fetching history (runs in thread).

    Returns: (workspace_id, channel_id, access_token)
    """
    with make_session() as session:
        if workspace_id:
            credentials = _get_credentials_for_workspace(session, token, workspace_id)
        else:
            credentials = _get_default_credentials(session, token)

        channel_id = _resolve_channel(session, credentials.workspace_id, channel)
        access_token = credentials.access_token

        if not access_token:
            raise ValueError("No access token for workspace")

        return credentials.workspace_id, channel_id, access_token


# --- MCP Tools ---


@slack_mcp.tool()
@visible_when(require_scopes("slack"), has_slack_workspaces)
async def send(
    message: str,
    channel: str | None = None,
    user: str | None = None,
    workspace_id: str | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """
    Send a message to a Slack channel or user.

    Args:
        message: The message content to send
        channel: Channel ID (e.g., C12345678) or channel name (e.g., "general")
        user: User ID (e.g., U12345678) to send a DM to. If provided, opens a DM with the user.
        workspace_id: Optional workspace ID (uses default if not specified)
        thread_ts: Optional thread timestamp to reply to

    Returns:
        Dict with success status and message details
    """
    if not message:
        raise ValueError("Message cannot be empty")

    if not channel and not user:
        raise ValueError("Either channel or user is required")

    if channel and user:
        raise ValueError("Specify either channel or user, not both")

    # Get access token from FastMCP context
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    # If user specified, we need to open a DM channel
    if user:
        # Get credentials in thread to avoid blocking
        slack_token = await asyncio.to_thread(
            _get_slack_token_for_dm, access_token.token, workspace_id
        )
        # Open DM channel with the user
        channel_id = await _open_dm_channel(slack_token, user)
    else:
        # Run DB operations in thread to avoid blocking event loop
        assert channel is not None  # for type checker
        _, channel_id, slack_token = await asyncio.to_thread(
            _get_credentials_for_send, access_token.token, workspace_id, channel
        )

    # Build API params
    params: dict[str, Any] = {
        "channel": channel_id,
        "text": message,
    }
    if thread_ts:
        params["thread_ts"] = thread_ts

    # Make async API call (session is now closed)
    data = await async_slack_call(slack_token, "chat.postMessage", **params)

    return {
        "success": True,
        "channel": channel_id,
        "ts": data.get("ts"),
        "message_preview": message[:100] + "..." if len(message) > 100 else message,
    }


@slack_mcp.tool()
@visible_when(require_scopes("slack"), has_slack_workspaces)
async def add_reaction(
    channel: str,
    message_ts: str,
    emoji: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """
    Add a reaction emoji to a Slack message.

    Args:
        channel: Channel ID (e.g., C12345678) or channel name (e.g., "general")
        message_ts: The timestamp of the message to react to
        emoji: The emoji name (without colons, e.g., "thumbsup" not ":thumbsup:")
        workspace_id: Optional workspace ID (uses default if not specified)

    Returns:
        Dict with success status
    """
    if not emoji:
        raise ValueError("Emoji cannot be empty")

    # Remove colons if present
    emoji = emoji.strip(":")

    # Get access token from FastMCP context
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    # Run DB operations in thread
    _, channel_id, slack_token = await asyncio.to_thread(
        _get_credentials_for_send, access_token.token, workspace_id, channel
    )

    # Make async API call
    await async_slack_call(
        slack_token,
        "reactions.add",
        channel=channel_id,
        timestamp=message_ts,
        name=emoji,
    )

    return {
        "success": True,
        "channel": channel_id,
        "message_ts": message_ts,
        "emoji": emoji,
    }


@slack_mcp.tool()
@visible_when(require_scopes("slack"), has_slack_workspaces)
async def list_channels(
    workspace_id: str | None = None,
    include_private: bool = True,
    include_dms: bool = False,
    include_archived: bool = False,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """
    List Slack channels the user has access to.

    Args:
        workspace_id: Optional workspace ID (uses default if not specified)
        include_private: Include private channels (default True)
        include_dms: Include DMs and group DMs (default False)
        include_archived: Include archived channels (default False)
        fields: List of fields to include for each channel.
                Default: ["id", "name", "type", "is_private"]
                Available fields:
                - id: Channel ID (e.g., C12345678)
                - name: Channel name
                - type: Channel type (public_channel, private_channel, dm, mpim)
                - is_private: Whether the channel is private
                - is_archived: Whether the channel is archived
                - workspace_id: The workspace ID this channel belongs to
                - collect_messages: Whether message collection is enabled (null = inherit)
                - effective_collect: Actual collection status after inheritance
                - last_message_ts: Timestamp of last synced message
                - project_id: Associated project ID for access control
                - sensitivity: Sensitivity level (public, basic, internal, confidential)

    Returns:
        Dict with workspace info and channels list
    """
    # Get access token from FastMCP context
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    # Validate and default fields
    requested_fields = fields or DEFAULT_CHANNEL_FIELDS
    invalid_fields = set(requested_fields) - CHANNEL_FIELDS
    if invalid_fields:
        raise ValueError(f"Invalid fields: {invalid_fields}. Available: {CHANNEL_FIELDS}")

    # Run DB operations in thread - this returns all the data we need
    return await asyncio.to_thread(
        _get_channels_data,
        access_token.token,
        workspace_id,
        include_private,
        include_dms,
        include_archived,
        requested_fields,
    )


@slack_mcp.tool()
@visible_when(require_scopes("slack"), has_slack_workspaces)
async def get_channel_history(
    channel: str,
    workspace_id: str | None = None,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    """
    Get message history from a Slack channel.

    Args:
        channel: Channel ID (e.g., C12345678) or channel name (e.g., "general")
        workspace_id: Optional workspace ID (uses default if not specified)
        limit: Maximum number of messages (default 50, max 100)
        before: ISO datetime or message ts - only get messages before this
        after: ISO datetime or message ts - only get messages after this

    Returns:
        Dict with messages list
    """
    if not channel:
        raise ValueError("Channel is required")

    limit = max(1, min(100, limit))

    # Get access token from FastMCP context
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    # Run DB operations in thread
    ws_id, channel_id, slack_token = await asyncio.to_thread(
        _get_history_data, access_token.token, workspace_id, channel
    )

    # Build API params
    params: dict[str, Any] = {
        "channel": channel_id,
        "limit": limit,
    }
    if before:
        params["latest"] = before
    if after:
        params["oldest"] = after

    # Make async API call
    data = await async_slack_call(slack_token, "conversations.history", **params)
    messages = data.get("messages", [])

    # Format messages (user names not available without additional API calls)
    formatted_messages = []
    for msg in messages:
        formatted_messages.append(
            {
                "ts": msg.get("ts"),
                "user": msg.get("user"),  # Slack user ID
                "text": msg.get("text", ""),
                "thread_ts": msg.get("thread_ts"),
                "reply_count": msg.get("reply_count"),
            }
        )

    return {
        "channel_id": channel_id,
        "workspace_id": ws_id,
        "messages": formatted_messages,
        "count": len(formatted_messages),
        "has_more": data.get("has_more", False),
    }
