"""Common Slack API client and utilities.

This module provides a shared interface for Slack API calls used across
workers, API endpoints, and MCP tools.
"""

import asyncio
import logging
import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import object_session

from memory.common import settings
from memory.common.db.connection import DBSession
from memory.common.db.models.slack import (
    DEFAULT_SLACK_API_BASE_URL,
    SlackApp,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.chat_urls import chat_url_allowed
from memory.common.db.models.users import User
from memory.common.safe_http import public_http_client, public_sync_http_client
from memory.common.access_control import UserLike

logger = logging.getLogger(__name__)

# Rate limit retry settings
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER = 5  # seconds
# The API URL is user-configurable, so a server asking for longer waits gets
# the call failed rather than a worker held.
MAX_RETRY_AFTER = 30  # seconds

# A message timestamp: epoch seconds, a dot, a sequence number. It names the
# message's image directory, so nothing else may pass for one.
SLACK_TS_PATTERN = re.compile(r"\d{1,12}\.\d{1,9}")


def is_slack_ts(value: str | None) -> bool:
    return value is not None and SLACK_TS_PATTERN.fullmatch(value) is not None


def rate_limit_wait(error: str, response: httpx.Response, attempt: int) -> int | None:
    """Seconds to wait before retrying a rate-limited call, or None to give up."""
    if error != "ratelimited" or attempt >= MAX_RATE_LIMIT_RETRIES:
        return None
    try:
        retry_after = int(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
    except (ValueError, TypeError):
        retry_after = DEFAULT_RETRY_AFTER
    return retry_after if retry_after <= MAX_RETRY_AFTER else None

# Slack object ids are prefix-typed: users are U/W, channels are C (public),
# G (private) or D (DM). The delivery method is derived from this prefix.
SLACK_USER_PREFIXES = ("U", "W")
SLACK_CHANNEL_PREFIXES = ("C", "G", "D")


def slack_target_is_dm(target: str) -> bool:
    """True if the target id is a Slack user (DM), else a channel."""
    return target[:1].upper() in SLACK_USER_PREFIXES


def user_has_workspace_membership(db: DBSession, workspace_id: str, user: UserLike) -> bool:
    """Cheap any-app membership probe used to gate workspace-level views.

    Returns True if the user has at least one credential for this
    workspace through any SlackApp they're authorized on. Does NOT
    return the credential itself — for that, use
    :func:`memory.api.slack.get_user_credentials_for_app`.
    """
    return (
        db.query(SlackUserCredentials.id)
        .filter(
            SlackUserCredentials.workspace_id == workspace_id,
            SlackUserCredentials.user_id == user.id,
        )
        .first()
        is not None
    )


def workspace_poll_plan(db: DBSession, workspace_id: str) -> tuple[list[int], list[str]]:
    """The SlackApps whose credentials can poll this workspace, and why each other one can't.

    Polling only needs an unexpired OAuth user token on an active app. An
    app's ``setup_state`` tracks push-event delivery, which polling doesn't
    use, so a workspace is polled even while its app's event setup is
    unfinished.
    """
    credentials = (
        db.query(SlackUserCredentials)
        .filter(SlackUserCredentials.workspace_id == workspace_id)
        .all()
    )
    if not credentials:
        return [], ["no Slack credentials are connected"]

    pollable: list[int] = []
    blocked: list[str] = []
    for app_id in sorted({c.slack_app_id for c in credentials}):
        app = db.get(SlackApp, app_id)
        if app is None or not app.is_active:
            blocked.append(f"Slack app {app_id} is inactive")
            continue
        if not any(c.is_usable() for c in credentials if c.slack_app_id == app_id):
            blocked.append(f"Slack app {app_id} has no unexpired token; reconnect the workspace")
            continue
        pollable.append(app_id)
    return pollable, blocked


def workspace_sync_interval(db: DBSession, workspace: SlackWorkspace, app_ids: list[int]) -> int:
    """Use slow reconciliation only while every polling app is receiving events.

    An app with no event within PUSH_STALE_HOURS (never set up, or its
    delivery broke) keeps the polling cadence as its fallback. First-time
    imports are scheduled regardless of this interval by the caller.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.PUSH_STALE_HOURS)
    apps = [db.get(SlackApp, app_id) for app_id in app_ids]
    if apps and all(app and app.last_event_at and app.last_event_at > cutoff for app in apps):
        return workspace.event_reconcile_interval_seconds
    return workspace.sync_interval_seconds


UNREACHABLE_CHANNEL_ERRORS = frozenset({"channel_not_found", "not_in_channel"})


class SlackAPIError(Exception):
    """Error from Slack API."""

    def __init__(self, error: str, response: dict | None = None):
        self.error = error
        self.response = response
        super().__init__(f"Slack API error: {error}")


def api_url(base_url: str, method: str) -> str:
    return f"{base_url.rstrip('/')}/{method}"


class SlackClient:
    """Synchronous client for the Slack Web API, or any server speaking it at
    ``base_url``. The URL is user-configurable, so every dial is address-checked."""

    def __init__(
        self,
        access_token: str,
        timeout: float = 30.0,
        base_url: str = DEFAULT_SLACK_API_BASE_URL,
    ):
        self.access_token = access_token
        self.timeout = timeout
        self.base_url = base_url
        self._client: httpx.Client | None = None

    def __enter__(self) -> "SlackClient":
        self._client = public_sync_http_client(timeout=self.timeout)
        self._client.headers["Authorization"] = f"Bearer {self.access_token}"
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
            response = self._client.post(
                api_url(self.base_url, method), data=kwargs if kwargs else None
            )
            data = response.json()

            if data.get("ok"):
                return data

            error = data.get("error", "unknown_error")

            # Handle rate limiting with retry
            retry_after = rate_limit_wait(error, response, attempt)
            if retry_after is not None:
                logger.warning(
                    f"Slack rate limited on {method}, waiting {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})"
                )
                time.sleep(retry_after)
                continue

            # Channels the app has left or can't see fail like this on every
            # sync; the caller already treats it as a per-channel result.
            log = logger.info if error in UNREACHABLE_CHANNEL_ERRORS else logger.error
            log(f"Slack API error in {method}: {error}")
            raise SlackAPIError(error, data)

        # Unreachable, but satisfies type checker
        raise SlackAPIError("ratelimited", {"error": "ratelimited"})


def slack_api_base_url(app: SlackApp) -> str:
    """The app's Web API URL, if it is still on the chat API allowlist."""
    session = object_session(app)
    owner = session.get(User, app.created_by_user_id) if session and app.created_by_user_id else None
    if not chat_url_allowed(app.api_base_url, owner):
        logger.warning(f"Slack app {app.id}: API URL is not on the allowlist")
        raise SlackAPIError("api_base_url_not_allowed")
    return app.api_base_url


def slack_client_for(credentials: SlackUserCredentials) -> SlackClient:
    """A client for the API of the app the credential was issued through."""
    return SlackClient(
        credentials.access_token or "", base_url=slack_api_base_url(credentials.slack_app)
    )


async def async_slack_call(
    access_token: str,
    method: str,
    base_url: str = DEFAULT_SLACK_API_BASE_URL,
    **params,
) -> dict:
    """Make an async Slack API call with rate limit retry handling."""
    async with public_http_client() as client:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            response = await client.post(
                api_url(base_url, method),
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
            retry_after = rate_limit_wait(error, response, attempt)
            if retry_after is not None:
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
    latest: str | None = None,
    inclusive: bool = False,
    limit: int = 100,
) -> Iterator[dict]:
    """Iterate over messages in a channel with automatic pagination.

    Args:
        client: SlackClient instance
        channel_id: Channel to fetch messages from
        oldest: Only fetch messages after this timestamp (for incremental sync)
        latest: Only fetch messages before this timestamp
        inclusive: Also fetch messages at exactly ``oldest`` or ``latest``
        limit: Messages per page (max 100)

    Yields:
        Message dicts from newest to oldest
    """
    params: dict[str, Any] = {"channel": channel_id, "limit": limit}
    if oldest:
        params["oldest"] = oldest
    if latest:
        params["latest"] = latest
    if inclusive:
        params["inclusive"] = "true"
    yield from _paginate(
        client, "conversations.history", "messages", params, check_has_more=True
    )


def iter_thread_replies(
    client: SlackClient,
    channel_id: str,
    thread_ts: str,
    oldest: str | None = None,
    limit: int = 100,
) -> Iterator[dict]:
    """Iterate over thread replies with automatic pagination.

    Args:
        client: SlackClient instance
        channel_id: Channel containing the thread
        thread_ts: Parent message timestamp
        oldest: Only fetch replies after this timestamp
        limit: Replies per page (max 100)

    Yields:
        Reply message dicts (excludes parent message)
    """
    params: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": limit}
    if oldest:
        params["oldest"] = oldest
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
