"""Lightweight slash-command helpers for the Discord collector."""

import json
import logging
import time
from typing import Any, AsyncGenerator, Literal, cast

import aiohttp
import discord
from sqlalchemy.orm import Session, scoped_session

from memory.common.db.connection import make_session
from memory.common.db.models.discord import MCPServer, MCPServerAssignment
from memory.common.oauth import get_endpoints, issue_challenge, register_oauth_client

logger = logging.getLogger(__name__)


def find_mcp_server(
    session: Session | scoped_session, entity_type: str, entity_id: int, url: str
) -> MCPServer | None:
    """Find an MCP server assigned to an entity."""
    assignment = (
        session.query(MCPServerAssignment)
        .join(MCPServer)
        .filter(
            MCPServerAssignment.entity_type == entity_type,
            MCPServerAssignment.entity_id == entity_id,
            MCPServer.mcp_server_url == url,
        )
        .first()
    )
    return assignment and assignment.mcp_server


async def call_mcp_server(
    url: str, access_token: str, method: str, params: dict = {}
) -> AsyncGenerator[Any, None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
    }

    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": method,
        "params": params,
    }

    async with aiohttp.ClientSession() as http_session:
        async with http_session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Tools list failed: {resp.status} - {error_text}")
                raise ValueError(
                    f"Failed to call MCP server: {resp.status} - {error_text}"
                )

            # Parse SSE stream
            async for line in resp.content:
                line_str = line.decode("utf-8").strip()

                # SSE format: "data: {json}"
                if line_str.startswith("data: "):
                    json_str = line_str[6:]  # Remove "data: " prefix
                    try:
                        yield json.loads(json_str)
                    except json.JSONDecodeError:
                        continue  # Skip invalid JSON lines


async def handle_mcp_list(entity_type: str, entity_id: int) -> str:
    """List all MCP servers for the user."""
    with make_session() as session:
        assignments = (
            session.query(MCPServerAssignment)
            .join(MCPServer)
            .filter(
                MCPServerAssignment.entity_type == entity_type,
                MCPServerAssignment.entity_id == entity_id,
            )
            .all()
        )

        if not assignments:
            return (
                "📋 **Your MCP Servers**\n\n"
                "You don't have any MCP servers configured yet.\n"
                "Use `/memory_mcp_servers add <url>` to add one."
            )

        def format_server(assignment: MCPServerAssignment) -> str:
            server = assignment.mcp_server
            con = "🟢" if cast(str | None, server.access_token) else "🔴"
            return f"{con} **{server.mcp_server_url}**\n`{server.client_id}`"

        server_list = "\n".join(format_server(a) for a in assignments)

    return f"📋 **Your MCP Servers**\n\n{server_list}"


async def handle_mcp_add(
    entity_type: str,
    entity_id: int,
    bot_user: discord.User | None,
    url: str,
) -> str:
    """Add a new MCP server via OAuth."""
    if not bot_user:
        raise ValueError("Bot user is required")
    with make_session() as session:
        if find_mcp_server(session, entity_type, entity_id, url):
            return (
                f"**MCP Server Already Exists**\n\n"
                f"You already have an MCP server configured at `{url}`.\n"
                f"Use `/memory_mcp_servers connect {url}` to reconnect."
            )

        endpoints = await get_endpoints(url)
        name = f"Discord Bot - {bot_user.name} ({entity_type} {entity_id})"
        client_id = await register_oauth_client(endpoints, url, name)

        # Create MCP server
        mcp_server = MCPServer(
            mcp_server_url=url,
            client_id=client_id,
            name=name,
        )
        session.add(mcp_server)
        session.flush()

        assignment = MCPServerAssignment(
            mcp_server_id=mcp_server.id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        session.add(assignment)
        session.flush()

        auth_url = await issue_challenge(mcp_server, endpoints)
        session.commit()

        logger.info(
            f"Created MCP server record: id={mcp_server.id}, "
            f"{entity_type}={entity_id}, url={url}"
        )

    return (
        f"🔐 **Add MCP Server**\n\n"
        f"Server: `{url}`\n"
        f"Click the link below to authorize:\n{auth_url}\n\n"
        f"⚠️ Keep this link private!\n"
        f"💡 You'll be redirected to login and grant access to the MCP server."
    )


async def handle_mcp_delete(entity_type: str, entity_id: int, url: str) -> str:
    """Delete an MCP server assignment."""
    with make_session() as session:
        # Find the assignment
        assignment = (
            session.query(MCPServerAssignment)
            .join(MCPServer)
            .filter(
                MCPServerAssignment.entity_type == entity_type,
                MCPServerAssignment.entity_id == entity_id,
                MCPServer.mcp_server_url == url,
            )
            .first()
        )

        if not assignment:
            return (
                f"**MCP Server Not Found**\n\n"
                f"You don't have an MCP server configured at `{url}`.\n"
            )

        # Delete the assignment (server will cascade delete if no other assignments exist)
        session.delete(assignment)

        # Check if server has other assignments
        mcp_server = assignment.mcp_server
        other_assignments = (
            session.query(MCPServerAssignment)
            .filter(
                MCPServerAssignment.mcp_server_id == mcp_server.id,
                MCPServerAssignment.id != assignment.id,
            )
            .count()
        )

        # If no other assignments, delete the server too
        if other_assignments == 0:
            session.delete(mcp_server)

        session.commit()

    return f"🗑️ **Delete MCP Server**\n\nServer `{url}` has been removed."


async def handle_mcp_connect(entity_type: str, entity_id: int, url: str) -> str:
    """Reconnect to an existing MCP server (redo OAuth)."""
    with make_session() as session:
        mcp_server = find_mcp_server(session, entity_type, entity_id, url)
        if not mcp_server:
            raise ValueError(
                f"**MCP Server Not Found**\n\n"
                f"You don't have an MCP server configured at `{url}`.\n"
                f"Use `/memory_mcp_servers add {url}` to add it first."
            )

        endpoints = await get_endpoints(url)
        auth_url = await issue_challenge(mcp_server, endpoints)

        session.commit()

        logger.info(
            f"Regenerated OAuth challenge for {entity_type}={entity_id}, url={url}"
        )

    return (
        f"🔄 **Reconnect to MCP Server**\n\n"
        f"Server: `{url}`\n"
        f"Click the link below to reauthorize:\n{auth_url}\n\n"
        f"⚠️ Keep this link private!\n"
        f"💡 You'll be redirected to login and grant access to the MCP server again."
    )


async def handle_mcp_tools(entity_type: str, entity_id: int, url: str) -> str:
    """List tools available on an MCP server."""
    with make_session() as session:
        mcp_server = find_mcp_server(session, entity_type, entity_id, url)

        if not mcp_server:
            raise ValueError(
                f"**MCP Server Not Found**\n\n"
                f"You don't have an MCP server configured at `{url}`.\n"
                f"Use `/memory_mcp_servers add {url}` to add it first."
            )

        if not cast(str | None, mcp_server.access_token):
            raise ValueError(
                f"**Not Authorized**\n\n"
                f"You haven't authorized access to `{url}` yet.\n"
                f"Use `/memory_mcp_servers connect {url}` to authorize."
            )

        access_token = cast(str, mcp_server.access_token)

    # Make JSON-RPC request to MCP server
    tools = None
    try:
        async for data in call_mcp_server(url, access_token, "tools/list"):
            if "result" in data and "tools" in data["result"]:
                tools = data["result"]["tools"]
                break
    except aiohttp.ClientError as exc:
        logger.exception(f"Failed to connect to MCP server: {exc}")
        raise ValueError(
            f"**Connection failed**\n\n"
            f"Server: `{url}`\n"
            f"Could not connect to the MCP server: {str(exc)}"
        )
    except Exception as exc:
        logger.exception(f"Failed to list tools: {exc}")
        raise ValueError(
            f"**Error**\n\nServer: `{url}`\nFailed to list tools: {str(exc)}"
        )

    if tools is None:
        raise ValueError(
            f"**Unexpected response format**\n\n"
            f"Server: `{url}`\n"
            f"The server returned an unexpected response format."
        )

    if not tools:
        return (
            f"🔧 **MCP Server Tools**\n\n"
            f"Server: `{url}`\n\n"
            f"No tools available on this server."
        )

    # Format tools list
    tools_list = "\n".join(
        f"• **{t.get('name', 'unknown')}**: {t.get('description', 'No description')}"
        for t in tools
    )

    return (
        f"🔧 **MCP Server Tools**\n\n"
        f"Server: `{url}`\n"
        f"Found {len(tools)} tool(s):\n\n"
        f"{tools_list}"
    )


async def run_mcp_server_command(
    bot_user: discord.User | None,
    action: Literal["list", "add", "delete", "connect", "tools"],
    url: str | None,
    entity_type: str,
    entity_id: int,
) -> None:
    """Handle MCP server management commands."""
    if action not in ["list", "add", "delete", "connect", "tools"]:
        raise ValueError(f"Invalid action: {action}")
    if action != "list" and not url:
        raise ValueError("URL is required for this action")
    if not bot_user:
        raise ValueError("Bot user is required")

    if action == "list" or not url:
        return await handle_mcp_list(entity_type, entity_id)
    elif action == "add":
        return await handle_mcp_add(entity_type, entity_id, bot_user, url)
    elif action == "delete":
        return await handle_mcp_delete(entity_type, entity_id, url)
    elif action == "connect":
        return await handle_mcp_connect(entity_type, entity_id, url)
    elif action == "tools":
        return await handle_mcp_tools(entity_type, entity_id, url)
    raise ValueError(f"Invalid action: {action}")
