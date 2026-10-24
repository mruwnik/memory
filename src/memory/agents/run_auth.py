"""Credentials that fail inside a run, and notifying their owner once.

External MCP servers: the stored token, refreshed once on a 401 under the row
lock, and a failure that ends the run. Provider keys: a key its provider
refuses is marked invalid and its owner told.
"""

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
from fastmcp import Client
from mcp.types import Tool

from memory.agents.external import apply_tools, mcp_session
from memory.agents.provider_keys import keys_page
from memory.common.db.connection import make_session
from memory.common.db.models import ExternalMCPServer, LLMProviderKey, User
from memory.common.notify import notify_user_now
from memory.common.mcp_oauth import (
    MCPServerError,
    ReauthRequired,
    refresh_locked,
    reject_token,
    server_page,
)

logger = logging.getLogger(__name__)


def notify_reauth(error: ReauthRequired) -> None:
    if not error.transitioned:
        return
    with make_session() as db:
        user = db.get(User, error.user_id)
        if user is None:
            return
        notify_user_now(
            db,
            user,
            f"MCP server '{error.server_name}' needs re-authorization",
            "Its authorization expired or was revoked. Agent runs that use it fail "
            "until you re-authorize it.",
            server_page(error.server_id),
        )


def notify_key_rejected(key: LLMProviderKey) -> None:
    with make_session() as db:
        user = db.get(User, key.user_id)
        if user is None:
            return
        notify_user_now(
            db,
            user,
            f"Your {key.provider} API key '{key.label}' was rejected",
            f"{key.provider} refused it during an agent run. Runs that use it fail "
            "until you replace it.",
            keys_page(),
        )


async def fresh_token(server_id: int, stale: str | None) -> str:
    try:
        return await refresh_locked(server_id, stale)
    except ReauthRequired as e:
        notify_reauth(e)
        raise


class StoredTokenAuth(httpx.Auth):
    """The server's stored token; on a 401, one locked refresh and a retry.

    A failure is also kept on `failure` and signalled on `failed`: the MCP
    transport sends requests from background tasks, where it kills the session
    and leaves the pending call waiting out its timeout.
    """

    requires_request_body = True

    def __init__(self, server: ExternalMCPServer):
        self.server_id = server.id
        self.token = server.access_token
        self.failure: MCPServerError | None = None
        self.failed = asyncio.Event()

    async def async_auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code != 401:
            return
        try:
            self.token = await fresh_token(self.server_id, self.token)
            request.headers["Authorization"] = f"Bearer {self.token}"
            response = yield request
            if response.status_code == 401:
                rejected = await reject_token(self.server_id, self.token)
                notify_reauth(rejected)
                raise rejected
        except MCPServerError as e:
            # Any refresh failure ends the call now, not at MCP_CALL_TIMEOUT_SEC.
            self.failure = e
            self.failed.set()
            raise


async def unless_reauth[T](auth: StoredTokenAuth | None, work: Awaitable[T]) -> T:
    """`work`'s result, or the auth failure as soon as one happens."""
    if auth is None:
        return await work
    attempt = asyncio.ensure_future(work)
    failed = asyncio.ensure_future(auth.failed.wait())
    try:
        await asyncio.wait({attempt, failed}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        failed.cancel()
        attempt.cancel()
    if auth.failure is not None:
        raise auth.failure
    return attempt.result()


class RunSession:
    """An MCP session for a run. Once the auth has failed, the transport's own
    teardown error is dropped so the run ends with the failure that caused it."""

    def __init__(self, server: ExternalMCPServer, auth: StoredTokenAuth | None):
        self.session = mcp_session(server, auth)
        self.auth = auth

    async def __aenter__(self) -> Client:
        return await self.session.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> bool | None:
        try:
            return await self.session.__aexit__(*exc_info)
        except Exception:
            if self.auth is None or self.auth.failure is None:
                raise
            logger.info("MCP session for server %s ended after its auth failed", self.auth.server_id)
            return False


def run_auth(server: ExternalMCPServer) -> StoredTokenAuth | None:
    return StoredTokenAuth(server) if server.auth_type == "oauth" else None


def write_back_tools(server: ExternalMCPServer, tools: list[Tool]) -> list[dict[str, Any]]:
    """The tool cache as of this run start, written back if it changed."""
    now = datetime.now(timezone.utc)
    seen = SimpleNamespace(id=server.id, tools=server.tools)
    diff = apply_tools(seen, tools, now)
    if diff["added"] or diff["removed"] or diff["changed"]:
        with make_session() as db:
            if row := db.get(ExternalMCPServer, server.id):
                apply_tools(row, tools, now)
    return seen.tools
