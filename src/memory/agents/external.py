"""Setting up a user's external MCP server: probe, authorize, cache its tools.

Network I/O runs on a detached copy of the row, with no DB session held.
"""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp.types import LATEST_PROTOCOL_VERSION, Tool
from pydantic import BaseModel, ConfigDict

from memory.agents.spec import TOOL_NAME, external_tool_name
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import ExternalMCPServer, ServerStatus
from memory.common.mcp_oauth import (
    Challenge,
    check_issuer,
    MCPServerError,
    discover,
    exchange_code,
    manual_auth_method,
    register_client,
    StateError,
    WrongUser,
    start_authorization,
    take_state,
    unreachable,
)
from memory.common.safe_http import mcp_http_client, public_http_client, resolve

logger = logging.getLogger(__name__)

# Probe, discovery, registration and the first tool listing, in the API process.
SETUP_DEADLINE_SEC = 60
REJECTED = "the server rejected these credentials"
NEEDS_AUTH = "this server requires authorization"
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
FORBIDDEN_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "cookie"}
)


class AuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["auto", "oauth", "headers", "none"] = "auto"
    headers: dict[str, str] = {}
    client_id: str | None = None
    client_secret: str | None = None
    scopes: str | None = None


def header_errors(headers: dict[str, str]) -> list[str]:
    return [
        f"header {name!r} is not allowed"
        for name in headers
        if not HEADER_NAME.match(name) or name.lower() in FORBIDDEN_HEADERS
    ] + [
        f"header {name!r} must be printable ASCII on one line"
        for name, value in headers.items()
        if not value.isascii() or "\r" in value or "\n" in value
    ]


async def check_url(url: str) -> None:
    """Refuse URLs no server may have. Reachability is checked at dial time."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise MCPServerError("the URL must be http(s)://host/...")
    # Registering memory itself would hand runs the owner's full token,
    # bypassing a run's narrowed scopes.
    if parsed.hostname.rstrip(".").lower() == (urlparse(settings.SERVER_URL).hostname or "").lower():
        raise MCPServerError("memory itself can't be registered as an external server")
    if parsed.scheme == "https":
        return
    # Plain http only where the token never crosses the internet.
    addresses = await resolve(parsed.hostname)
    if not addresses:
        raise unreachable("resolving", url, "no addresses")
    if not all(a.is_private for a in addresses):
        raise MCPServerError("use https for servers outside your private network")


def load_detached(server_id: int, user_id: int | None = None) -> ExternalMCPServer:
    """The server, detached; with `user_id`, only if that user owns it."""
    with make_session() as db:
        server = db.get(ExternalMCPServer, server_id)
        if server is None or user_id not in (None, server.user_id):
            raise MCPServerError(f"unknown MCP server {server_id}")
        db.refresh(server)
        db.expunge(server)
    return server


SAVED_COLUMNS = [
    c.key
    for c in ExternalMCPServer.__table__.columns
    if c.key not in ("id", "user_id", "created_at", "updated_at")
]


# Each write-back names the columns its path owns: a detached copy is up to a
# deadline old, and writing its stale tokens over a refresh that rotated them
# meanwhile would replay a spent refresh token.
TOOL_COLUMNS = ("tools", "tools_refreshed_at", "tools_changed_at")
STATUS_COLUMNS = ("status", "status_detail")
TOKEN_COLUMNS = ("access_token_encrypted", "refresh_token_encrypted", "token_expires_at")


def save(server: ExternalMCPServer, columns: tuple[str, ...] | list[str]) -> None:
    """Write `columns` of a detached copy back; a server deleted meanwhile stays deleted."""
    with make_session() as db:
        db.query(ExternalMCPServer).filter(ExternalMCPServer.id == server.id).update(
            {c: getattr(server, c) for c in columns}, synchronize_session=False
        )


def mcp_transport(server: ExternalMCPServer, auth: httpx.Auth | None):
    kind = SSETransport if server.transport == "sse" else StreamableHttpTransport
    return kind(
        server.url,
        headers=server.headers,
        auth=auth,
        httpx_client_factory=mcp_http_client,
    )


def stored_auth(server: ExternalMCPServer) -> httpx.Auth | None:
    token = server.access_token
    return BearerAuth(token) if token else None


@asynccontextmanager
async def mcp_session(
    server: ExternalMCPServer, auth: httpx.Auth | None = None
) -> AsyncIterator[Client]:
    async with Client(
        mcp_transport(server, auth or stored_auth(server)),
        timeout=settings.MCP_CALL_TIMEOUT_SEC,
    ) as client:
        yield client


async def list_remote_tools(server: ExternalMCPServer) -> list[Tool]:
    try:
        async with asyncio.timeout(settings.MCP_CONNECT_TIMEOUT_SEC):
            async with mcp_session(server) as client:
                return await client.list_tools()
    except Exception as e:
        # fastmcp surfaces transport failures as many exception types.
        raise unreachable("listing tools", server.url, repr(e)) from e


def tool_entry(tool: Tool) -> dict[str, Any]:
    description = (tool.description or "")[: settings.MCP_MAX_TOOL_DESCRIPTION_CHARS]
    digest = hashlib.sha256(
        json.dumps([description, tool.inputSchema], sort_keys=True).encode()
    ).hexdigest()
    return {
        "name": tool.name,
        "description": description,
        "input_schema": tool.inputSchema,
        "read_only": bool(tool.annotations and tool.annotations.readOnlyHint),
        "hash": digest,
    }


def unusable(server_id: int, tool: Tool) -> str | None:
    if not TOOL_NAME.match(external_tool_name(server_id, tool.name)):
        return (
            "its name is too long or has characters model providers reject "
            f"(exposed names must match {TOOL_NAME.pattern})"
        )
    size = len(json.dumps(tool.inputSchema))
    if size > settings.MCP_MAX_TOOL_SCHEMA_BYTES:
        return f"its input schema is {size} bytes, over the {settings.MCP_MAX_TOOL_SCHEMA_BYTES} limit"
    return None


def apply_tools(server: Any, tools: list[Tool], now: datetime) -> dict[str, Any]:
    """Replace the server's tool cache; what was added, removed, changed or dropped.

    Each entry keeps `changed_at`, when its hash last changed, so runs can tell
    which tools changed after their agent was saved.
    """
    entries: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for tool in tools:
        reason = unusable(server.id, tool)
        if reason is None and len(entries) >= settings.MCP_MAX_TOOLS_PER_SERVER:
            reason = f"over the limit of {settings.MCP_MAX_TOOLS_PER_SERVER} tools per server"
        if reason:
            dropped.append({"name": tool.name, "reason": reason})
            continue
        entries.append(tool_entry(tool))
    previous = {t["name"]: t for t in server.tools}
    for entry in entries:
        before = previous.get(entry["name"])
        same = before is not None and before["hash"] == entry["hash"]
        entry["changed_at"] = before.get("changed_at") if same and before else now.isoformat()
    old = {t["name"]: t["hash"] for t in server.tools}
    new = {t["name"]: t["hash"] for t in entries}
    diff = {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(n for n in new.keys() & old.keys() if new[n] != old[n]),
        "dropped": dropped,
    }
    if diff["added"] or diff["removed"] or diff["changed"]:
        server.tools_changed_at = now
    server.tools = entries
    server.tools_refreshed_at = now
    return diff


async def refresh_tool_cache(server: ExternalMCPServer) -> dict[str, Any]:
    return apply_tools(server, await list_remote_tools(server), datetime.now(timezone.utc))


Probe = Literal["open", "rejected"] | Challenge


async def probe(
    client: httpx.AsyncClient, server: ExternalMCPServer, headers: dict[str, str]
) -> Probe:
    """How the server answers an unauthenticated (or header-authenticated) hello."""
    if server.transport == "sse":
        request = client.build_request(
            "GET", server.url, headers=headers | {"Accept": "text/event-stream"}
        )
    else:
        request = client.build_request(
            "POST",
            server.url,
            headers=headers | {"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "memory", "version": "1"},
                },
            },
        )
    try:
        response = await client.send(request, stream=True)
        await response.aclose()
    except httpx.HTTPError as e:
        raise unreachable("probe", server.url, e) from e
    if response.is_success:
        return "open"
    if response.status_code == 401:
        return Challenge.from_response(response)
    if response.status_code == 403:
        return "rejected"
    raise unreachable("probe", server.url, f"status {response.status_code}")


async def connect_static(server: ExternalMCPServer, auth_type: str, headers: dict[str, str]) -> None:
    server.auth_type = auth_type
    server.headers = headers
    server.clear_tokens()
    server.clear_client()
    await refresh_tool_cache(server)
    server.status = ServerStatus.CONNECTED.value
    server.status_detail = None


async def setup(server: ExternalMCPServer, auth: AuthInput) -> str | None:
    """Configure `server` from `auth`; the URL to authorize at when OAuth is needed."""
    headers = auth.headers if auth.type == "headers" else {}
    if errors := header_errors(headers):
        raise MCPServerError("; ".join(errors))
    await check_url(server.url)
    async with public_http_client() as client:
        answer = await probe(client, server, headers)
        if auth.type in ("headers", "none") or (auth.type == "auto" and answer == "open"):
            if answer == "rejected" or (isinstance(answer, Challenge) and headers):
                raise MCPServerError(REJECTED)
            if isinstance(answer, Challenge):
                raise MCPServerError(NEEDS_AUTH)
            await connect_static(server, "none" if auth.type == "auto" else auth.type, headers)
            return None
        challenge = answer if isinstance(answer, Challenge) else Challenge()
        found = await discover(client, server.url, challenge)
        scopes = auth.scopes or found.scopes
        if auth.client_id:
            client_id, secret, method = (
                auth.client_id,
                auth.client_secret,
                manual_auth_method(auth.client_secret, found.auth_methods),
            )
            source = "manual"
        else:
            client_id, secret, method = await register_client(client, found, scopes)
            source = "dcr"
    server.headers = None
    server.clear_tokens()
    server.auth_type = "oauth"
    server.issuer = found.issuer
    server.iss_parameter_supported = found.iss_supported
    server.authorization_endpoint = found.authorization_endpoint
    server.token_endpoint = found.token_endpoint
    server.revocation_endpoint = found.revocation_endpoint
    server.resource = found.resource
    server.scopes = scopes
    server.client_id = client_id
    server.client_secret = secret
    server.token_endpoint_auth_method = method
    server.client_source = source
    server.status = ServerStatus.PENDING_AUTH.value
    server.status_detail = "authorize this server to finish connecting it"
    return start_authorization(server)


def fail(server: ExternalMCPServer, error: MCPServerError) -> None:
    server.status = ServerStatus.ERROR.value
    server.status_detail = str(error)
    save(server, STATUS_COLUMNS)


@dataclass(frozen=True)
class SetupResult:
    authorize_url: str | None = None
    # Where a client secret the user typed in will be sent, for them to confirm.
    secret_sent_to: str | None = None


async def configure(user_id: int, server_id: int, auth: AuthInput) -> SetupResult:
    """Set up one of the user's saved servers."""
    server = load_detached(server_id, user_id)
    try:
        async with asyncio.timeout(SETUP_DEADLINE_SEC):
            authorize_url = await setup(server, auth)
    except TimeoutError as e:
        error = unreachable("setup", server.url, "deadline exceeded")
        fail(server, error)
        raise error from e
    except MCPServerError as e:
        fail(server, e)
        raise
    save(server, SAVED_COLUMNS)
    sent_to = (
        urlparse(server.token_endpoint or "").hostname
        if auth.client_secret and server.auth_type == "oauth"
        else None
    )
    return SetupResult(authorize_url, sent_to)


async def authorize(user_id: int, server_id: int) -> str:
    """A fresh authorize URL for one of the user's servers set up for OAuth."""
    return start_authorization(load_detached(server_id, user_id))


async def finish_authorization(
    state: str, code: str, user_id: int, iss: str | None = None
) -> tuple[int, bool]:
    """The OAuth callback: exchange the code and cache tools; (server id, connected).

    A failure is recorded on the row for the server's page to show.
    """
    flow = take_state(state)
    if flow["user_id"] != user_id:
        raise WrongUser(
            "log in to memory as the user who started this authorization, then retry it"
        )
    try:
        server = load_detached(flow["server_id"])
    except MCPServerError as e:
        raise StateError("this server was deleted") from e
    if server.user_id != user_id:
        raise WrongUser("this server belongs to another user")
    check_issuer(flow, iss, server.iss_parameter_supported)
    try:
        async with asyncio.timeout(SETUP_DEADLINE_SEC):
            await exchange_code(server, code, flow["code_verifier"])
            await refresh_tool_cache(server)
    except TimeoutError:
        fail(server, unreachable("authorization", server.url, "deadline exceeded"))
        return server.id, False
    except MCPServerError as e:
        fail(server, e)
        return server.id, False
    save(server, [*TOKEN_COLUMNS, *STATUS_COLUMNS, *TOOL_COLUMNS])
    return server.id, True
