"""Tests for Discord MCP server management."""

import json
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import discord
import pytest

from memory.common.db.models import MCPServer, MCPServerAssignment
from memory.discord.mcp import (
    call_mcp_server,
    find_mcp_server,
    handle_mcp_add,
    handle_mcp_connect,
    handle_mcp_delete,
    handle_mcp_list,
    handle_mcp_tools,
    run_mcp_server_command,
)


# Helper class for async iteration
class AsyncIterator:
    """Helper to create an async iterator for mocking aiohttp response content."""
    def __init__(self, items):
        self.items = items
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.items):
            raise StopAsyncIteration
        item = self.items[self.index]
        self.index += 1
        return item


@pytest.fixture
def mcp_server(db_session) -> MCPServer:
    """Create a test MCP server."""
    server = MCPServer(
        name="Test MCP Server",
        mcp_server_url="https://mcp.example.com",
        client_id="test_client_id",
        access_token="test_access_token",
        available_tools=["tool1", "tool2"],
    )
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def mcp_assignment(db_session, mcp_server: MCPServer) -> MCPServerAssignment:
    """Create a test MCP server assignment."""
    assignment = MCPServerAssignment(
        mcp_server_id=mcp_server.id,
        entity_type="DiscordUser",
        entity_id=123456,
    )
    db_session.add(assignment)
    db_session.commit()
    return assignment


@pytest.fixture
def mock_bot_user() -> discord.User:
    """Create a mock Discord bot user."""
    user = Mock(spec=discord.User)
    user.name = "TestBot"
    user.id = 999
    return user


def test_find_mcp_server_exists(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test finding an existing MCP server."""
    result = find_mcp_server(
        db_session,
        entity_type="DiscordUser",
        entity_id=123456,
        url="https://mcp.example.com",
    )

    assert result is not None
    assert result.id == mcp_server.id
    assert result.mcp_server_url == "https://mcp.example.com"


def test_find_mcp_server_not_found(db_session):
    """Test finding a non-existent MCP server."""
    result = find_mcp_server(
        db_session,
        entity_type="DiscordUser",
        entity_id=999999,
        url="https://nonexistent.com",
    )

    assert result is None


def test_find_mcp_server_wrong_entity(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test finding MCP server with wrong entity type."""
    result = find_mcp_server(
        db_session,
        entity_type="DiscordChannel",  # Wrong entity type
        entity_id=123456,
        url="https://mcp.example.com",
    )

    assert result is None


@pytest.mark.asyncio
async def test_call_mcp_server_success():
    """Test calling MCP server successfully."""
    mock_response_data = [
        b'data: {"result": {"tools": [{"name": "test"}]}}\n',
        b'data: {"status": "ok"}\n',
    ]

    mock_response = Mock()
    mock_response.status = 200
    mock_response.content = AsyncIterator(mock_response_data)

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        results = []
        async for data in call_mcp_server(
            "https://mcp.example.com", "test_token", "tools/list", {}
        ):
            results.append(data)

        assert len(results) == 2
        assert "result" in results[0]
        assert results[0]["result"]["tools"][0]["name"] == "test"


@pytest.mark.asyncio
async def test_call_mcp_server_error():
    """Test calling MCP server with error response."""
    mock_response = Mock()
    mock_response.status = 500
    mock_response.text = AsyncMock(return_value="Internal Server Error")

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        with pytest.raises(ValueError, match="Failed to call MCP server"):
            async for _ in call_mcp_server(
                "https://mcp.example.com", "test_token", "tools/list"
            ):
                pass


@pytest.mark.asyncio
async def test_call_mcp_server_invalid_json():
    """Test calling MCP server with invalid JSON."""
    mock_response_data = [
        b"data: invalid json\n",
        b'data: {"valid": "json"}\n',
    ]

    mock_response = Mock()
    mock_response.status = 200
    mock_response.content = AsyncIterator(mock_response_data)

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        results = []
        async for data in call_mcp_server(
            "https://mcp.example.com", "test_token", "tools/list"
        ):
            results.append(data)

        # Should skip invalid JSON and only return valid one
        assert len(results) == 1
        assert results[0] == {"valid": "json"}


@pytest.mark.asyncio
async def test_handle_mcp_list_empty(db_session):
    """Test listing MCP servers when none exist."""
    result = await handle_mcp_list("DiscordUser", 123456)

    assert "You don't have any MCP servers configured yet" in result
    assert "/memory_mcp_servers add" in result


@pytest.mark.asyncio
async def test_handle_mcp_list_with_servers(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test listing MCP servers with existing servers."""
    result = await handle_mcp_list("DiscordUser", 123456)

    assert "Your MCP Servers" in result
    assert "https://mcp.example.com" in result
    assert "test_client_id" in result
    assert "🟢" in result  # Server has access token


@pytest.mark.asyncio
async def test_handle_mcp_list_disconnected_server(db_session):
    """Test listing MCP servers with disconnected server."""
    server = MCPServer(
        name="Disconnected Server",
        mcp_server_url="https://disconnected.example.com",
        client_id="client_123",
        access_token=None,  # No access token
    )
    db_session.add(server)
    db_session.flush()

    assignment = MCPServerAssignment(
        mcp_server_id=server.id,
        entity_type="DiscordUser",
        entity_id=123456,
    )
    db_session.add(assignment)
    db_session.commit()

    result = await handle_mcp_list("DiscordUser", 123456)

    assert "🔴" in result  # Server has no access token


@pytest.mark.asyncio
async def test_handle_mcp_add_new_server(db_session, mock_bot_user):
    """Test adding a new MCP server."""
    with (
        patch("memory.discord.mcp.get_endpoints") as mock_get_endpoints,
        patch("memory.discord.mcp.register_oauth_client") as mock_register,
        patch("memory.discord.mcp.issue_challenge") as mock_challenge,
    ):
        mock_endpoints = Mock()
        mock_get_endpoints.return_value = mock_endpoints
        mock_register.return_value = "new_client_id"
        mock_challenge.return_value = "https://auth.example.com/authorize"

        result = await handle_mcp_add(
            "DiscordUser", 123456, mock_bot_user, "https://new.example.com"
        )

        assert "Add MCP Server" in result
        assert "https://new.example.com" in result
        assert "https://auth.example.com/authorize" in result

        # Verify server was created
        server = (
            db_session.query(MCPServer)
            .filter(MCPServer.mcp_server_url == "https://new.example.com")
            .first()
        )
        assert server is not None
        assert server.client_id == "new_client_id"

        # Verify assignment was created
        assignment = (
            db_session.query(MCPServerAssignment)
            .filter(
                MCPServerAssignment.mcp_server_id == server.id,
                MCPServerAssignment.entity_type == "DiscordUser",
                MCPServerAssignment.entity_id == 123456,
            )
            .first()
        )
        assert assignment is not None


@pytest.mark.asyncio
async def test_handle_mcp_add_existing_server(
    db_session,
    mcp_server: MCPServer,
    mcp_assignment: MCPServerAssignment,
    mock_bot_user,
):
    """Test adding an MCP server that already exists."""
    result = await handle_mcp_add(
        "DiscordUser", 123456, mock_bot_user, "https://mcp.example.com"
    )

    assert "MCP Server Already Exists" in result
    assert "https://mcp.example.com" in result
    assert "/memory_mcp_servers connect" in result


@pytest.mark.asyncio
async def test_handle_mcp_add_no_bot_user(db_session):
    """Test adding MCP server without bot user."""
    with pytest.raises(ValueError, match="Bot user is required"):
        await handle_mcp_add("DiscordUser", 123456, None, "https://example.com")


@pytest.mark.asyncio
async def test_handle_mcp_delete_existing(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test deleting an existing MCP server assignment."""
    # Store IDs before deletion
    assignment_id = mcp_assignment.id
    server_id = mcp_server.id

    result = await handle_mcp_delete("DiscordUser", 123456, "https://mcp.example.com")

    assert "Delete MCP Server" in result
    assert "https://mcp.example.com" in result
    assert "has been removed" in result

    # Verify assignment was deleted
    assignment = (
        db_session.query(MCPServerAssignment)
        .filter(MCPServerAssignment.id == assignment_id)
        .first()
    )
    assert assignment is None

    # Verify server was also deleted (no other assignments)
    server = db_session.query(MCPServer).filter(MCPServer.id == server_id).first()
    assert server is None


@pytest.mark.asyncio
async def test_handle_mcp_delete_not_found(db_session):
    """Test deleting a non-existent MCP server."""
    result = await handle_mcp_delete("DiscordUser", 123456, "https://nonexistent.com")

    assert "MCP Server Not Found" in result
    assert "https://nonexistent.com" in result


@pytest.mark.asyncio
async def test_handle_mcp_delete_with_other_assignments(db_session):
    """Test deleting MCP server with multiple assignments."""
    server = MCPServer(
        name="Shared Server",
        mcp_server_url="https://shared.example.com",
        client_id="shared_client",
    )
    db_session.add(server)
    db_session.flush()

    assignment1 = MCPServerAssignment(
        mcp_server_id=server.id,
        entity_type="DiscordUser",
        entity_id=111,
    )
    assignment2 = MCPServerAssignment(
        mcp_server_id=server.id,
        entity_type="DiscordUser",
        entity_id=222,
    )
    db_session.add_all([assignment1, assignment2])
    db_session.commit()

    # Delete one assignment
    result = await handle_mcp_delete("DiscordUser", 111, "https://shared.example.com")

    assert "has been removed" in result

    # Verify only one assignment was deleted
    remaining = (
        db_session.query(MCPServerAssignment)
        .filter(MCPServerAssignment.mcp_server_id == server.id)
        .count()
    )
    assert remaining == 1

    # Verify server still exists
    server_check = db_session.query(MCPServer).filter(MCPServer.id == server.id).first()
    assert server_check is not None


@pytest.mark.asyncio
async def test_handle_mcp_connect_existing(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test reconnecting to an existing MCP server."""
    with (
        patch("memory.discord.mcp.get_endpoints") as mock_get_endpoints,
        patch("memory.discord.mcp.issue_challenge") as mock_challenge,
    ):
        mock_endpoints = Mock()
        mock_get_endpoints.return_value = mock_endpoints
        mock_challenge.return_value = "https://auth.example.com/authorize?state=new"

        result = await handle_mcp_connect(
            "DiscordUser", 123456, "https://mcp.example.com"
        )

        assert "Reconnect to MCP Server" in result
        assert "https://mcp.example.com" in result
        assert "https://auth.example.com/authorize?state=new" in result


@pytest.mark.asyncio
async def test_handle_mcp_connect_not_found(db_session):
    """Test reconnecting to a non-existent MCP server."""
    with pytest.raises(ValueError, match="MCP Server Not Found"):
        await handle_mcp_connect("DiscordUser", 123456, "https://nonexistent.com")


@pytest.mark.asyncio
async def test_handle_mcp_tools_success(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test listing tools from an MCP server."""
    mock_response_data = [
        b'data: {"result": {"tools": [{"name": "search", "description": "Search tool"}]}}\n',
    ]

    mock_response = Mock()
    mock_response.status = 200
    mock_response.content = AsyncIterator(mock_response_data)

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        result = await handle_mcp_tools(
            "DiscordUser", 123456, "https://mcp.example.com"
        )

        assert "MCP Server Tools" in result
        assert "https://mcp.example.com" in result
        assert "search" in result
        assert "Search tool" in result
        assert "Found 1 tool(s)" in result


@pytest.mark.asyncio
async def test_handle_mcp_tools_no_tools(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test listing tools when server has no tools."""
    mock_response_data = [
        b'data: {"result": {"tools": []}}\n',
    ]

    mock_response = Mock()
    mock_response.status = 200
    mock_response.content = AsyncIterator(mock_response_data)

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        result = await handle_mcp_tools(
            "DiscordUser", 123456, "https://mcp.example.com"
        )

        assert "No tools available" in result


@pytest.mark.asyncio
async def test_handle_mcp_tools_server_not_found(db_session):
    """Test listing tools for a non-existent server."""
    with pytest.raises(ValueError, match="MCP Server Not Found"):
        await handle_mcp_tools("DiscordUser", 123456, "https://nonexistent.com")


@pytest.mark.asyncio
async def test_handle_mcp_tools_not_authorized(db_session):
    """Test listing tools when not authorized."""
    server = MCPServer(
        name="Unauthorized Server",
        mcp_server_url="https://unauthorized.example.com",
        client_id="client_123",
        access_token=None,  # No access token
    )
    db_session.add(server)
    db_session.flush()

    assignment = MCPServerAssignment(
        mcp_server_id=server.id,
        entity_type="DiscordUser",
        entity_id=123456,
    )
    db_session.add(assignment)
    db_session.commit()

    with pytest.raises(ValueError, match="Not Authorized"):
        await handle_mcp_tools(
            "DiscordUser", 123456, "https://unauthorized.example.com"
        )


@pytest.mark.asyncio
async def test_handle_mcp_tools_connection_error(
    db_session, mcp_server: MCPServer, mcp_assignment: MCPServerAssignment
):
    """Test listing tools with connection error."""
    mock_post = AsyncMock()
    mock_post.__aenter__.side_effect = aiohttp.ClientError("Connection failed")
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        with pytest.raises(ValueError, match="Connection failed"):
            await handle_mcp_tools("DiscordUser", 123456, "https://mcp.example.com")


@pytest.mark.asyncio
async def test_run_mcp_server_command_list(db_session, mock_bot_user):
    """Test run_mcp_server_command with list action."""
    result = await run_mcp_server_command(
        mock_bot_user, "list", None, "DiscordUser", 123456
    )

    assert "Your MCP Servers" in result


@pytest.mark.asyncio
async def test_run_mcp_server_command_invalid_action(mock_bot_user):
    """Test run_mcp_server_command with invalid action."""
    with pytest.raises(ValueError, match="Invalid action"):
        await run_mcp_server_command(
            mock_bot_user, "invalid", None, "DiscordUser", 123456
        )


@pytest.mark.asyncio
async def test_run_mcp_server_command_missing_url(mock_bot_user):
    """Test run_mcp_server_command with missing URL for non-list action."""
    with pytest.raises(ValueError, match="URL is required"):
        await run_mcp_server_command(mock_bot_user, "add", None, "DiscordUser", 123456)


@pytest.mark.asyncio
async def test_run_mcp_server_command_no_bot_user():
    """Test run_mcp_server_command without bot user."""
    with pytest.raises(ValueError, match="Bot user is required"):
        await run_mcp_server_command(None, "list", None, "DiscordUser", 123456)
