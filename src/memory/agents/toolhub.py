"""Every tool a run can call, behind one interface.

Memory tools run in-process as the run's user (visibility middleware applies),
external MCP servers are called over HTTP with the owner's token, and builtins
are either executed natively by the provider or locally here.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx
from fastmcp import Client
from mcp.types import Tool

from memory.agents.builtins import BUILTIN_DEFS, search_backend, web_fetch
from memory.agents.mcp_client import MemoryTools, memory_tools, render_content
from memory.agents.spec import AgentSpec, ExternalMCP, external_tool_name, is_denied
from memory.agents.external import load_detached, tool_entry, unusable
from memory.agents.run_auth import (
    RunSession,
    StoredTokenAuth,
    fresh_token,
    run_auth,
    unless_reauth,
    write_back_tools,
)
from memory.common import settings
from memory.common.db.models import ExternalMCPServer, ServerStatus
from memory.common.mcp_oauth import (
    UNREACHABLE,
    MCPServerError,
    server_page,
    unusable_message,
)
from memory.common.llms.neutral import BUILTIN_TOOLS, Capabilities, ToolCall, ToolResult
from memory.common.llms.tools import ToolDefinition
from memory.common.ssrf import UnsafeURLError

logger = logging.getLogger(__name__)

Caller = Callable[[dict[str, Any]], Awaitable[tuple[str, bool]]]


class ToolHubError(RuntimeError):
    pass


class ToolHub(Protocol):
    def tools(self) -> list[ToolDefinition]: ...

    def notices(self) -> list[str]:
        """Things the run's transcript should mention, e.g. changed tools."""
        ...

    async def call(self, call: ToolCall) -> ToolResult: ...


class MCPToolHub:
    def __init__(
        self,
        definitions: list[ToolDefinition],
        callers: dict[str, Caller],
        notices: list[str] | None = None,
    ):
        # Sorted once: the tool list is part of the cached, append-only prefix.
        self.definitions = sorted(definitions, key=lambda t: t.name)
        self.callers = callers
        self.notice_texts = notices or []

    def tools(self) -> list[ToolDefinition]:
        return self.definitions

    def notices(self) -> list[str]:
        return self.notice_texts

    async def call(self, call: ToolCall) -> ToolResult:
        """A failing tool is an error result the model can react to; only an
        external server's lost authorization (MCPServerError) is raised."""
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
        except MCPServerError:
            # Ends the run: the server's session is gone, and a run that quietly
            # loses a tool is worse than a failed one.
            raise
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


def http_mcp_caller(client: Client, name: str, auth: StoredTokenAuth | None) -> Caller:
    async def call(arguments: dict[str, Any]) -> tuple[str, bool]:
        result = await unless_reauth(
            auth,
            client.call_tool(
                name,
                arguments,
                raise_on_error=False,
                timeout=settings.MCP_CALL_TIMEOUT_SEC,
            ),
        )
        return render_content(result.content), result.is_error

    return call


async def local_web_fetch(arguments: dict[str, Any]) -> tuple[str, bool]:
    try:
        return await web_fetch(str(arguments.get("url", ""))), False
    except UnsafeURLError as e:
        return f"refused: {e}", True
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        return f"fetch failed: {e}", True


def denied_tools(spec: AgentSpec) -> list[str]:
    """The hard denylist, re-applied at run start for specs stored before a rule existed."""
    raw = spec.tools + [t for s in spec.mcp_servers for t in s.tools]
    return [t for t in raw if is_denied(t)]


async def usable_server(user_id: int, external: ExternalMCP) -> ExternalMCPServer:
    """The owner's server with a current token, detached and ready to connect."""
    try:
        server = load_detached(external.server_id)
    except MCPServerError:
        server = None
    if server is None or server.user_id != user_id:
        raise ToolHubError(f"MCP server {external.server_id} is not yours")
    if server.status != ServerStatus.CONNECTED:
        raise ToolHubError(unusable_message(server))
    soon = datetime.now(timezone.utc) + timedelta(
        seconds=settings.MCP_TOKEN_REFRESH_SKEW_SEC
    )
    if server.token_expires_at is not None and server.token_expires_at < soon:
        try:
            server.access_token = await fresh_token(server.id, server.access_token)
        except MCPServerError as e:
            raise ToolHubError(str(e)) from e
    return server


async def connect_external(
    stack: AsyncExitStack, server: ExternalMCPServer, auth: StoredTokenAuth | None
) -> tuple[Client, list[Tool]]:
    try:
        async with asyncio.timeout(settings.MCP_CONNECT_TIMEOUT_SEC):
            client = await stack.enter_async_context(RunSession(server, auth))
            listed = await unless_reauth(auth, client.list_tools())
    except Exception as e:
        if auth and auth.failure:
            raise ToolHubError(str(auth.failure)) from e
        logger.warning("connecting to MCP server %s failed: %r", server.id, e)
        raise ToolHubError(
            f"MCP server '{server.name}': {UNREACHABLE} ({server_page(server.id)})"
        ) from e
    return client, listed


@asynccontextmanager
async def open_toolhub(
    *,
    spec: AgentSpec,
    user_id: int,
    scopes: list[str],
    session_token: str,
    capabilities: Capabilities,
    saved_at: datetime | None = None,
) -> AsyncIterator[ToolHub]:
    """`saved_at`: when the run's definition was saved, to note external tools
    that changed since."""
    if bad := denied_tools(spec):
        raise ToolHubError(f"tools never callable inside a run: {', '.join(bad)}")
    definitions: list[ToolDefinition] = []
    callers: dict[str, Caller] = {}
    missing: list[str] = []
    notices: list[str] = []
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
            server = await usable_server(user_id, external)
            auth = run_auth(server)
            client, listed = await connect_external(stack, server, auth)
            offered = {t.name: t for t in listed}
            cached = {t["name"]: t for t in write_back_tools(server, listed)}
            for name in external.tools:
                if name not in offered:
                    missing.append(f"{name} (not offered by MCP server {server.name})")
                    continue
                # The same limits as the cache: the live list could have grown since.
                if reason := unusable(server.id, offered[name]):
                    missing.append(f"{name} ({reason})")
                    continue
                entry = tool_entry(offered[name])
                exposed = external_tool_name(server.id, name)
                definitions.append(
                    ToolDefinition(
                        name=exposed,
                        description=f"[{server.name}] {entry['description']}",
                        input_schema=entry["input_schema"],
                    )
                )
                callers[exposed] = http_mcp_caller(client, name, auth)
                changed_at = cached.get(name, {}).get("changed_at")
                if saved_at and changed_at and datetime.fromisoformat(changed_at) > saved_at:
                    notices.append(
                        f"tool {name} on MCP server '{server.name}' changed since this agent was saved"
                    )

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
        yield MCPToolHub(definitions, callers, notices)
