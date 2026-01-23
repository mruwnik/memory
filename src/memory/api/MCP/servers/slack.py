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
    if not token or session is None:
        return False

    def _check(session: DBSession) -> bool:
        user_session = session.get(UserSession, token)
        if not user_session or not user_session.user:
            return False
        # Check if user has any Slack credentials
        return len(user_session.user.slack_credentials) > 0

    return await asyncio.to_thread(_check, session)


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


def _get_channels_data(
    token: str, workspace_id: str | None, include_private: bool, include_dms: bool
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
            SlackChannel.is_archived == False,  # noqa: E712
        )

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
            "channels": [
                {
                    "id": ch.id,
                    "name": ch.name,
                    "type": ch.channel_type,
                    "is_private": ch.is_private,
                }
                for ch in channels
            ],
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
async def send_slack_message(
    message: str,
    channel: str,
    workspace_id: str | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """
    Send a message to a Slack channel.

    Args:
        message: The message content to send
        channel: Channel ID (e.g., C12345678) or channel name (e.g., "general")
        workspace_id: Optional workspace ID (uses default if not specified)
        thread_ts: Optional thread timestamp to reply to

    Returns:
        Dict with success status and message details
    """
    if not message:
        raise ValueError("Message cannot be empty")

    if not channel:
        raise ValueError("Channel is required")

    # Get access token from FastMCP context
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    # Run DB operations in thread to avoid blocking event loop
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
async def add_slack_reaction(
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
async def list_slack_channels(
    workspace_id: str | None = None,
    include_private: bool = True,
    include_dms: bool = False,
) -> dict[str, Any]:
    """
    List Slack channels the user has access to.

    Args:
        workspace_id: Optional workspace ID (uses default if not specified)
        include_private: Include private channels (default True)
        include_dms: Include DMs and group DMs (default False)

    Returns:
        Dict with channels list
    """
    # Get access token from FastMCP context
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    # Run DB operations in thread - this returns all the data we need
    return await asyncio.to_thread(
        _get_channels_data, access_token.token, workspace_id, include_private, include_dms
    )


@slack_mcp.tool()
@visible_when(require_scopes("slack"), has_slack_workspaces)
async def get_slack_channel_history(
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
        formatted_messages.append({
            "ts": msg.get("ts"),
            "user": msg.get("user"),  # Slack user ID
            "text": msg.get("text", ""),
            "thread_ts": msg.get("thread_ts"),
            "reply_count": msg.get("reply_count"),
        })

    return {
        "channel_id": channel_id,
        "workspace_id": ws_id,
        "messages": formatted_messages,
        "count": len(formatted_messages),
        "has_more": data.get("has_more", False),
    }
