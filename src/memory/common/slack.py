"""Common Slack API client and utilities.

This module provides a shared interface for Slack API calls used across
workers, API endpoints, and MCP tools.
"""

import asyncio
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Rate limit retry settings
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER = 5  # seconds


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
            },
            timeout=self.timeout,
        )
        return self

    def __exit__(self, *args) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def call(self, method: str, **kwargs) -> dict:
        """Make a Slack API call with rate limit retry handling."""
        if not self._client:
            raise RuntimeError("SlackClient must be used as context manager")

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            response = self._client.post(method, data=kwargs if kwargs else None)
            data = response.json()

            if data.get("ok"):
                return data

            error = data.get("error", "unknown_error")

            # Handle rate limiting with retry
            if error == "ratelimited" and attempt < MAX_RATE_LIMIT_RETRIES:
                try:
                    retry_after = int(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
                except (ValueError, TypeError):
                    retry_after = DEFAULT_RETRY_AFTER
                logger.warning(
                    f"Slack rate limited on {method}, waiting {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})"
                )
                time.sleep(retry_after)
                continue

            logger.error(f"Slack API error in {method}: {error}")
            raise SlackAPIError(error, data)

        # Unreachable, but satisfies type checker
        raise SlackAPIError("ratelimited", {"error": "ratelimited"})


async def async_slack_call(access_token: str, method: str, **params) -> dict:
    """Make an async Slack API call with rate limit retry handling."""
    async with httpx.AsyncClient() as client:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            response = await client.post(
                f"https://slack.com/api/{method}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
                data=params if params else None,
                timeout=30.0,
            )
            data = response.json()

            if data.get("ok"):
                return data

            error = data.get("error", "unknown_error")

            # Handle rate limiting with retry
            if error == "ratelimited" and attempt < MAX_RATE_LIMIT_RETRIES:
                try:
                    retry_after = int(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
                except (ValueError, TypeError):
                    retry_after = DEFAULT_RETRY_AFTER
                logger.warning(
                    f"Slack rate limited on {method}, waiting {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})"
                )
                await asyncio.sleep(retry_after)
                continue

            raise SlackAPIError(error, data)

        # Unreachable, but satisfies type checker
        raise SlackAPIError("ratelimited", {"error": "ratelimited"})


# --- Paginated Iterators ---


def _paginate(
    client: SlackClient,
    method: str,
    response_key: str,
    params: dict[str, Any],
    check_has_more: bool = False,
) -> Iterator[dict]:
    """Generic cursor-based pagination for Slack API.

    Args:
        client: SlackClient instance
        method: Slack API method name
        response_key: Key in response containing items (e.g., "members", "channels")
        params: Initial API parameters (copied, not mutated)
        check_has_more: If True, also check "has_more" field (for conversations.*)
    """
    # Copy to avoid mutating caller's dict
    request_params = dict(params)
    cursor = None
    while True:
        if cursor:
            request_params["cursor"] = cursor

        response = client.call(method, **request_params)
        items = response.get(response_key, [])

        if not items:
            break

        yield from items

        if check_has_more and not response.get("has_more"):
            break

        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break


def iter_users(client: SlackClient, limit: int = 200) -> Iterator[dict]:
    """Iterate over all users in a workspace with automatic pagination."""
    yield from _paginate(client, "users.list", "members", {"limit": limit})


def iter_channels(
    client: SlackClient,
    types: str = "public_channel,private_channel,mpim,im",
    limit: int = 200,
) -> Iterator[dict]:
    """Iterate over all channels in a workspace with automatic pagination."""
    yield from _paginate(
        client, "conversations.list", "channels", {"types": types, "limit": limit}
    )


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
    params: dict[str, Any] = {"channel": channel_id, "limit": limit}
    if oldest:
        params["oldest"] = oldest
    yield from _paginate(
        client, "conversations.history", "messages", params, check_has_more=True
    )


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
    params: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": limit}
    for msg in _paginate(
        client, "conversations.replies", "messages", params, check_has_more=True
    ):
        # Skip the parent message (it's always included in replies)
        if msg.get("ts") != thread_ts:
            yield msg


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
