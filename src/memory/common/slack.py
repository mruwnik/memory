"""Common Slack API client and utilities.

This module provides a shared interface for Slack API calls used across
workers, API endpoints, and MCP tools.
"""

import logging
from collections.abc import Iterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SlackAPIError(Exception):
    """Error from Slack API."""

    def __init__(self, error: str, response: dict | None = None):
        self.error = error
        self.response = response
        super().__init__(f"Slack API error: {error}")


class SlackClient:
    """Synchronous Slack API client."""

    def __init__(self, access_token: str, timeout: float = 30.0):
        self.access_token = access_token
        self.timeout = timeout
        self._client: httpx.Client | None = None

    def __enter__(self) -> "SlackClient":
        self._client = httpx.Client(
            base_url="https://slack.com/api/",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=self.timeout,
        )
        return self

    def __exit__(self, *args) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def call(self, method: str, **kwargs) -> dict:
        """Make a Slack API call and handle errors."""
        if not self._client:
            raise RuntimeError("SlackClient must be used as context manager")

        response = self._client.post(method, json=kwargs if kwargs else None)
        data = response.json()

        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            logger.error(f"Slack API error in {method}: {error}")
            raise SlackAPIError(error, data)

        return data


async def async_slack_call(access_token: str, method: str, **params) -> dict:
    """Make an async Slack API call."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://slack.com/api/{method}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=params if params else None,
            timeout=30.0,
        )
        data = response.json()

        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            raise SlackAPIError(f"Slack API error: {error}")

        return data


# --- Paginated Iterators ---


def iter_users(client: SlackClient, limit: int = 200) -> Iterator[dict]:
    """Iterate over all users in a workspace with automatic pagination."""
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        response = client.call("users.list", **params)

        for member in response.get("members", []):
            yield member

        metadata = response.get("response_metadata", {})
        cursor = metadata.get("next_cursor")
        if not cursor:
            break


def iter_channels(
    client: SlackClient,
    types: str = "public_channel,private_channel,mpim,im",
    limit: int = 200,
) -> Iterator[dict]:
    """Iterate over all channels in a workspace with automatic pagination."""
    cursor = None
    while True:
        params: dict[str, Any] = {"types": types, "limit": limit}
        if cursor:
            params["cursor"] = cursor

        response = client.call("conversations.list", **params)

        for channel in response.get("channels", []):
            yield channel

        metadata = response.get("response_metadata", {})
        cursor = metadata.get("next_cursor")
        if not cursor:
            break


def iter_messages(
    client: SlackClient,
    channel_id: str,
    oldest: str | None = None,
    limit: int = 100,
) -> Iterator[dict]:
    """Iterate over messages in a channel with automatic pagination.

    Args:
        client: SlackClient instance
        channel_id: Channel to fetch messages from
        oldest: Only fetch messages after this timestamp (for incremental sync)
        limit: Messages per page (max 100)

    Yields:
        Message dicts from newest to oldest
    """
    cursor = None
    while True:
        params: dict[str, Any] = {"channel": channel_id, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        if cursor:
            params["cursor"] = cursor

        response = client.call("conversations.history", **params)
        messages = response.get("messages", [])

        if not messages:
            break

        for msg in messages:
            yield msg

        if not response.get("has_more"):
            break

        metadata = response.get("response_metadata", {})
        cursor = metadata.get("next_cursor")
        if not cursor:
            break


def iter_thread_replies(
    client: SlackClient,
    channel_id: str,
    thread_ts: str,
    limit: int = 100,
) -> Iterator[dict]:
    """Iterate over thread replies with automatic pagination.

    Args:
        client: SlackClient instance
        channel_id: Channel containing the thread
        thread_ts: Parent message timestamp
        limit: Replies per page (max 100)

    Yields:
        Reply message dicts (excludes parent message)
    """
    cursor = None
    while True:
        params: dict[str, Any] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor

        response = client.call("conversations.replies", **params)

        for msg in response.get("messages", []):
            # Skip the parent message
            if msg.get("ts") != thread_ts:
                yield msg

        if not response.get("has_more"):
            break

        metadata = response.get("response_metadata", {})
        cursor = metadata.get("next_cursor")
        if not cursor:
            break


# --- Channel Type Detection ---


def get_channel_type(channel: dict) -> str:
    """Determine channel type from Slack API response.

    Returns one of: "dm", "mpim", "private_channel", "channel"
    """
    if channel.get("is_im"):
        return "dm"
    if channel.get("is_mpim"):
        return "mpim"
    if channel.get("is_group") or channel.get("is_private"):
        return "private_channel"
    return "channel"
