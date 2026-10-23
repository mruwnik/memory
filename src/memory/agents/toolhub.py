"""Every tool a run can call, behind one interface.

Memory tools run in-process as the run's user (visibility middleware applies),
external MCP servers are called over HTTP with the owner's token, and builtins
are either executed natively by the provider or locally here.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Protocol

import aiohttp
import httpx
from fastmcp import Client

from memory.agents.builtins import BUILTIN_DEFS, search_backend, web_fetch
from memory.agents.mcp_client import MemoryTools, memory_tools, render_content
from memory.agents.spec import AgentSpec, ExternalMCP, external_tool_name, is_denied
from memory.common.db.connection import make_session
from memory.common.db.models import MCPServer, MCPServerAssignment
from memory.common.oauth import refresh_mcp_token
from memory.common.llms.neutral import BUILTIN_TOOLS, Capabilities, ToolCall, ToolResult
from memory.common.llms.tools import ToolDefinition
from memory.common.ssrf import UnsafeURLError, validate_public_url

logger = logging.getLogger(__name__)

Caller = Callable[[dict[str, Any]], Awaitable[tuple[str, bool]]]


class ToolHubError(RuntimeError):
    pass


class ToolHub(Protocol):
    def tools(self) -> list[ToolDefinition]: ...

    async def call(self, call: ToolCall) -> ToolResult: ...


class MCPToolHub:
    def __init__(self, definitions: list[ToolDefinition], callers: dict[str, Caller]):
        # Sorted once: the tool list is part of the cached, append-only prefix.
        self.definitions = sorted(definitions, key=lambda t: t.name)
        self.callers = callers

    def tools(self) -> list[ToolDefinition]:
        return self.definitions

    async def call(self, call: ToolCall) -> ToolResult:
        """Never raises: a failing tool is an error result the model can react to."""
        caller = self.callers.get(call.name)
        if caller is None:
            text = (
                f"{call.name} is executed by the provider, not the runner"
                if call.name in BUILTIN_TOOLS
                else f"unknown tool {call.name}"
            )
            return ToolResult(
                tool_call_id=call.id, name=call.name, text=text, is_error=True
            )
        try:
            text, is_error = await caller(call.input)
        except Exception as e:
            logger.exception("tool %s failed", call.name)
            text, is_error = f"{call.name} failed: {e}", True
        return ToolResult(
            tool_call_id=call.id, name=call.name, text=text, is_error=is_error
        )


def memory_caller(tools: MemoryTools, name: str) -> Caller:
    async def call(arguments: dict[str, Any]) -> tuple[str, bool]:
        return await tools.call(name, arguments)

    return call


def http_mcp_caller(client: Client, name: str) -> Caller:
    async def call(arguments: dict[str, Any]) -> tuple[str, bool]:
        result = await client.call_tool(name, arguments, raise_on_error=False)
        return render_content(result.content), result.is_error

    return call


async def local_web_fetch(arguments: dict[str, Any]) -> tuple[str, bool]:
    try:
        return await web_fetch(str(arguments.get("url", ""))), False
    except UnsafeURLError as e:
        return f"refused: {e}", True
    except httpx.HTTPError as e:
        return f"fetch failed: {e}", True


def denied_tools(spec: AgentSpec) -> list[str]:
    """The hard denylist, re-applied at run start for specs stored before a rule existed."""
    raw = spec.tools + [t for s in spec.mcp_servers for t in s.tools]
    return [t for t in raw if is_denied(t)]


async def assigned_server(user_id: int, external: ExternalMCP) -> MCPServer:
    """The owner's server, with a usable token and a public URL, ready to connect."""
    with make_session() as db:
        server = db.get(MCPServer, external.server_id)
        assignment = (
            db.query(MCPServerAssignment)
            .filter(
                MCPServerAssignment.mcp_server_id == external.server_id,
                MCPServerAssignment.entity_type == "User",
                MCPServerAssignment.entity_id == user_id,
            )
            .first()
        )
        if server is None or assignment is None:
            raise ToolHubError(
                f"MCP server {external.server_id} is not assigned to you"
            )
        if not server.access_token:
            raise ToolHubError(
                f"MCP server {server.name} is not authorized; authorize it before running this agent"
            )
        try:
            # The worker dials this URL itself (and refreshes through it), so it
            # must not reach internal services.
            validate_public_url(server.mcp_server_url)
        except UnsafeURLError as e:
            raise ToolHubError(
                f"MCP server {server.name}: {server.mcp_server_url} is not a public URL ({e})"
            ) from e
        if server.token_expires_at and server.token_expires_at < datetime.now(
            timezone.utc
        ):
            try:
                await refresh_mcp_token(server)
            except (ValueError, aiohttp.ClientError) as e:
                raise ToolHubError(
                    f"MCP server {server.name}: token expired and refresh failed ({e}); "
                    "re-authorize it before running this agent"
                ) from e
            db.commit()
            db.refresh(server)
        db.expunge(server)
    return server


async def connect_external(
    stack: AsyncExitStack, server: MCPServer
) -> tuple[Client, dict[str, Any]]:
    try:
        client = await stack.enter_async_context(
            Client(server.mcp_server_url, auth=server.access_token)
        )
        offered = {t.name: t for t in await client.list_tools()}
    except Exception as e:
        # A revoked token surfaces as whatever the transport raises on a 401.
        raise ToolHubError(
            f"MCP server {server.name}: could not connect ({e}); "
            "re-authorize it if its token was revoked"
        ) from e
    return client, offered


@asynccontextmanager
async def open_toolhub(
    *,
    spec: AgentSpec,
    user_id: int,
    scopes: list[str],
    session_token: str,
    capabilities: Capabilities,
) -> AsyncIterator[ToolHub]:
    if bad := denied_tools(spec):
        raise ToolHubError(f"tools never callable inside a run: {', '.join(bad)}")
    definitions: list[ToolDefinition] = []
    callers: dict[str, Caller] = {}
    missing: list[str] = []
    async with AsyncExitStack() as stack:
        memory_names = [n for n in spec.tools if n not in BUILTIN_TOOLS]
        if memory_names:
            memory = await stack.enter_async_context(
                memory_tools(session_token, scopes)
            )
            visible = {t.name: t for t in await memory.list()}
            for name in memory_names:
                if name not in visible:
                    missing.append(
                        f"{name} (not visible to this user - missing scope?)"
                    )
                    continue
                definitions.append(
                    ToolDefinition(
                        name=name,
                        description=visible[name].description,
                        input_schema=visible[name].input_schema,
                    )
                )
                callers[name] = memory_caller(memory, name)

        for external in spec.mcp_servers:
            server = await assigned_server(user_id, external)
            client, offered = await connect_external(stack, server)
            for name in external.tools:
                if name not in offered:
                    missing.append(f"{name} (not offered by MCP server {server.name})")
                    continue
                exposed = external_tool_name(server.id, name)
                definitions.append(
                    ToolDefinition(
                        name=exposed,
                        description=offered[name].description or "",
                        input_schema=offered[name].inputSchema,
                    )
                )
                callers[exposed] = http_mcp_caller(client, name)

        native = capabilities.native_builtins()
        for name in sorted(set(spec.tools) & BUILTIN_TOOLS):
            definitions.append(BUILTIN_DEFS[name])
            if name in native:
                continue
            if name == "web_fetch":
                callers[name] = local_web_fetch
            elif search_backend() is None:
                missing.append(
                    "web_search (no native web search on this model and no local search backend)"
                )

        if missing:
            raise ToolHubError(f"tools unavailable: {'; '.join(missing)}")
        yield MCPToolHub(definitions, callers)
