"""Call memory's own MCP tools in-process, as a specific user.

Calls go through the mounted server so the visibility and metrics middleware
apply exactly as they do for remote MCP clients.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastmcp import Client
from fastmcp.server.auth.auth import AccessToken  # type: ignore[reportPrivateImportUsage]
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from memory.api.auth import lookup_principal, system_scopes
from memory.common.db.connection import make_session
from memory.common.scopes import SCOPE_ADMIN


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool


def narrow(granted: list[str], requested: list[str]) -> list[str]:
    if SCOPE_ADMIN in granted:
        return list(requested)
    if SCOPE_ADMIN in requested:
        return list(granted)
    return [scope for scope in requested if scope in granted]


def granted_scopes(token: str) -> list[str]:
    with make_session() as db:
        principal = lookup_principal(token, db, record_use=False)
        if principal is None:
            raise ValueError("unknown or expired token")
        return system_scopes(principal)


def render_content(content: list[Any]) -> str:
    return "\n".join(
        block.text
        if getattr(block, "type", None) == "text"
        else f"[{block.type} omitted]"
        for block in content
    )


class MemoryTools:
    def __init__(self, client: Client):
        self.client = client

    async def list(self) -> list[ToolInfo]:
        return [
            ToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema,
                read_only=bool(t.annotations and t.annotations.readOnlyHint),
            )
            for t in await self.client.list_tools()
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        result = await self.client.call_tool(name, arguments, raise_on_error=False)
        return render_content(result.content), result.is_error


@asynccontextmanager
async def memory_tools(token: str, scopes: list[str]) -> AsyncIterator[MemoryTools]:
    """Memory's tools as the token's principal, limited to `scopes`.

    `scopes` can only narrow what the principal is granted, never widen it.
    """
    # Imported here, not at module top: base mounts every subserver, including
    # the agents subserver that imports this module, so a top-level import is a cycle.
    from memory.api.MCP.base import mcp

    access = AccessToken(
        token=token, client_id="agent-run", scopes=narrow(granted_scopes(token), scopes)
    )
    # Set before the client spawns the in-memory server task, which copies the
    # current context, so each client's identity is fixed at creation.
    reset = auth_context_var.set(AuthenticatedUser(access))
    try:
        async with Client(mcp) as client:
            yield MemoryTools(client)
    finally:
        auth_context_var.reset(reset)
