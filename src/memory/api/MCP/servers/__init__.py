"""MCP subservers for composable tool organization."""

import logging
from enum import Enum
from typing import TYPE_CHECKING

from memory.common import settings

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


class MCPServer(str, Enum):
    """All available MCP subservers."""

    BOOKS = "books"
    CORE = "core"
    DISCORD = "discord"
    EMAIL = "email"
    FORECAST = "forecast"
    GITHUB = "github"
    JOURNAL = "journal"
    META = "meta"
    NOTES = "notes"
    ORGANIZER = "organizer"
    PEOPLE = "people"
    POLLING = "polling"
    PROJECTS = "projects"
    REPORTS = "reports"
    SCHEDULER = "scheduler"
    SLACK = "slack"
    TEAMS = "teams"


ALL_MCP_SERVERS: frozenset[str] = frozenset(s.value for s in MCPServer)


def get_enabled_servers() -> frozenset[str]:
    """Return set of enabled MCP server names."""
    disabled = settings.DISABLED_MCP_SERVERS
    invalid = disabled - ALL_MCP_SERVERS
    if invalid:
        logger.warning(f"Unknown servers in DISABLED_MCP_SERVERS: {invalid}")
    return ALL_MCP_SERVERS - disabled


def is_server_enabled(server: str | MCPServer) -> bool:
    """Check if a server is enabled for this deployment."""
    name = server.value if isinstance(server, MCPServer) else server
    return name not in settings.DISABLED_MCP_SERVERS


def get_server_instance(server: MCPServer) -> "FastMCP":
    """Lazy-load server instance to avoid importing disabled servers."""
    match server:
        case MCPServer.BOOKS:
            from memory.api.MCP.servers.books import books_mcp

            return books_mcp
        case MCPServer.CORE:
            from memory.api.MCP.servers.core import core_mcp

            return core_mcp
        case MCPServer.DISCORD:
            from memory.api.MCP.servers.discord import discord_mcp

            return discord_mcp
        case MCPServer.EMAIL:
            from memory.api.MCP.servers.email import email_mcp

            return email_mcp
        case MCPServer.FORECAST:
            from memory.api.MCP.servers.forecast import forecast_mcp

            return forecast_mcp
        case MCPServer.GITHUB:
            from memory.api.MCP.servers.github import github_mcp

            return github_mcp
        case MCPServer.JOURNAL:
            from memory.api.MCP.servers.journal import journal_mcp

            return journal_mcp
        case MCPServer.META:
            from memory.api.MCP.servers.meta import meta_mcp

            return meta_mcp
        case MCPServer.NOTES:
            from memory.api.MCP.servers.notes import notes_mcp

            return notes_mcp
        case MCPServer.ORGANIZER:
            from memory.api.MCP.servers.organizer import organizer_mcp

            return organizer_mcp
        case MCPServer.PEOPLE:
            from memory.api.MCP.servers.people import people_mcp

            return people_mcp
        case MCPServer.POLLING:
            from memory.api.MCP.servers.polling import polling_mcp

            return polling_mcp
        case MCPServer.PROJECTS:
            from memory.api.MCP.servers.projects import projects_mcp

            return projects_mcp
        case MCPServer.REPORTS:
            from memory.api.MCP.servers.reports import reports_mcp

            return reports_mcp
        case MCPServer.SCHEDULER:
            from memory.api.MCP.servers.scheduler import scheduler_mcp

            return scheduler_mcp
        case MCPServer.SLACK:
            from memory.api.MCP.servers.slack import slack_mcp

            return slack_mcp
        case MCPServer.TEAMS:
            from memory.api.MCP.servers.teams import teams_mcp

            return teams_mcp
        case _:
            raise ValueError(f"Unknown server: {server}")


__all__ = [
    "MCPServer",
    "ALL_MCP_SERVERS",
    "get_enabled_servers",
    "is_server_enabled",
    "get_server_instance",
]
