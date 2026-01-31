"""Tests for MCP tool name length constraints.

Claude Code has a 64-character limit on tool names. This test ensures
all MCP tool names stay within that limit when prefixed with the
server name pattern used by Claude Code.
"""

import pytest

from memory.api.MCP.base import SUBSERVER_PREFIXES
from memory.api.MCP.servers.books import books_mcp
from memory.api.MCP.servers.core import core_mcp
from memory.api.MCP.servers.discord import discord_mcp
from memory.api.MCP.servers.email import email_mcp
from memory.api.MCP.servers.forecast import forecast_mcp
from memory.api.MCP.servers.github import github_mcp
from memory.api.MCP.servers.meta import meta_mcp
from memory.api.MCP.servers.organizer import organizer_mcp
from memory.api.MCP.servers.people import people_mcp
from memory.api.MCP.servers.polling import polling_mcp
from memory.api.MCP.servers.schedule import schedule_mcp
from memory.api.MCP.servers.slack import slack_mcp


# Map prefixes to their FastMCP instances
SUBSERVERS = {
    "books": books_mcp,
    "core": core_mcp,
    "discord": discord_mcp,
    "email": email_mcp,
    "forecast": forecast_mcp,
    "github": github_mcp,
    "meta": meta_mcp,
    "organizer": organizer_mcp,
    "people": people_mcp,
    "polling": polling_mcp,
    "schedule": schedule_mcp,
    "slack": slack_mcp,
}

# Claude Code tool name limit
MAX_TOOL_NAME_LENGTH = 64
TOOL_PREFIX = "plugin_equistamp-all_equistamp"


def get_all_tool_names() -> list[tuple[str, str, str]]:
    """Get all tool names with their prefixes.

    Returns:
        List of (prefix, tool_name, full_prefixed_name) tuples.
    """
    tools = []
    for prefix, server in SUBSERVERS.items():
        tool_names = list(server._tool_manager._tools.keys())
        for tool_name in tool_names:
            prefixed_name = f"{prefix}_{tool_name}"
            tools.append((prefix, tool_name, prefixed_name))
    return tools


def compute_claude_code_name(server_name: str, prefixed_tool_name: str) -> str:
    """Compute the full tool name as used by Claude Code.

    Claude Code uses the pattern: mcp__{server_name}__{prefixed_tool_name}

    Args:
        server_name: The MCP server name (e.g., "memory-system")
        prefixed_tool_name: The tool name with prefix (e.g., "core_search")

    Returns:
        Full Claude Code tool name (e.g., "mcp__memory-system__core_search")
    """
    return f"mcp__{server_name}__{prefixed_tool_name}"


# Generate test cases for all tools
ALL_TOOLS = get_all_tool_names()


@pytest.mark.parametrize(
    "prefixed_name",
    [pn for _, _, pn in ALL_TOOLS],
    ids=[f"{p}_{t}" for p, t, _ in ALL_TOOLS],
)
def test_tool_name_length_memory_system(prefixed_name: str):
    """Tool names are within limit for memory-system server."""
    full_name = compute_claude_code_name("memory-system", prefixed_name)
    assert len(full_name) < MAX_TOOL_NAME_LENGTH, (
        f"Tool name too long ({len(full_name)} >= {MAX_TOOL_NAME_LENGTH}): {full_name}"
    )


@pytest.mark.parametrize(
    "prefixed_name",
    [pn for _, _, pn in ALL_TOOLS],
    ids=[f"{p}_{t}" for p, t, _ in ALL_TOOLS],
)
def test_tool_name_length_equistamp_plugin(prefixed_name: str):
    """Tool names are within limit for equistamp plugin server.

    The equistamp plugin has a longer server name pattern:
    plugin_equistamp-all_equistamp
    """
    full_name = compute_claude_code_name(TOOL_PREFIX, prefixed_name)
    assert len(full_name) < MAX_TOOL_NAME_LENGTH, (
        f"Tool name too long ({len(full_name)} >= {MAX_TOOL_NAME_LENGTH}): {full_name}"
    )


def test_all_subserver_prefixes_have_servers():
    """All prefixes in SUBSERVER_PREFIXES have corresponding server mappings."""
    for prefix in SUBSERVER_PREFIXES:
        assert prefix in SUBSERVERS, f"Missing server for prefix: {prefix}"
