"""MCP subservers for composable tool organization."""

from memory.api.MCP.servers.core import core_mcp
from memory.api.MCP.servers.github import github_mcp
from memory.api.MCP.servers.people import people_mcp
from memory.api.MCP.servers.schedule import schedule_mcp
from memory.api.MCP.servers.books import books_mcp
from memory.api.MCP.servers.meta import meta_mcp

__all__ = [
    "core_mcp",
    "github_mcp",
    "people_mcp",
    "schedule_mcp",
    "books_mcp",
    "meta_mcp",
]
