"""MCP subservers for composable tool organization."""

from enum import Enum
from typing import TYPE_CHECKING

from memory.common import instance_settings
from memory.common.module_settings import enabled_modules
from memory.common.modules import MODULE_MCP_SERVERS, servers_disabled

if TYPE_CHECKING:
    from fastmcp import FastMCP


class MCPServer(str, Enum):
    """All available MCP subservers."""

    AGENT = "agent"
    BOOKS = "books"
    CLAUDE = "claude"
    CONTAINERS = "containers"
    CORE = "core"
    DEADLINES = "deadlines"
    DISCORD = "discord"
    EMAIL = "email"
    FORECAST = "forecast"
    INGEST = "ingest"
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


def check_disabled_servers(disabled: frozenset[str]) -> None:
    """`disabled` counts the servers of the modules that are off too."""
    if ALL_MCP_SERVERS <= disabled:
        raise ValueError("can't disable every MCP server")


HIDDEN_MCP_SERVERS = instance_settings.register(
    instance_settings.Setting(
        key="disabled_mcp_servers",
        kind="list",
        applies="live",
        default=frozenset(),
        description="MCP servers whose tools are hidden",
        from_env=False,
        # A module's servers go with the module.
        choices=ALL_MCP_SERVERS - frozenset().union(*MODULE_MCP_SERVERS.values()),
    )
)


def unmounted_servers() -> frozenset[str]:
    """The servers of every module that is off."""
    return frozenset(servers_disabled(enabled_modules()))


def effective_disabled_servers() -> frozenset[str]:
    """The servers whose tools are off now: HIDDEN_MCP_SERVERS (set on the Server settings
    page) plus the servers of every module that is off."""
    return instance_settings.value(HIDDEN_MCP_SERVERS) | servers_disabled(enabled_modules())


def tool_server(tool: str) -> str | None:
    """The server a mounted tool, named <server>_<tool>, belongs to; longest prefix wins."""
    return max(
        (name for name in ALL_MCP_SERVERS if tool.startswith(f"{name}_")),
        key=len,
        default=None,
    )


def get_server_instance(server: MCPServer) -> "FastMCP":
    """Lazy-load server instance to avoid importing disabled servers."""
    match server:
        case MCPServer.AGENT:
            from memory.api.MCP.servers.agent import agent_mcp

            return agent_mcp
        case MCPServer.BOOKS:
            from memory.api.MCP.servers.books import books_mcp

            return books_mcp
        case MCPServer.CLAUDE:
            from memory.api.MCP.servers.claude import claude_mcp

            return claude_mcp
        case MCPServer.CONTAINERS:
            from memory.claude_containers.containers import containers_mcp

            return containers_mcp
        case MCPServer.CORE:
            from memory.api.MCP.servers.core import core_mcp

            return core_mcp
        case MCPServer.DEADLINES:
            from memory.api.MCP.servers.deadlines import deadlines_mcp

            return deadlines_mcp
        case MCPServer.DISCORD:
            from memory.api.MCP.servers.discord import discord_mcp

            return discord_mcp
        case MCPServer.EMAIL:
            from memory.api.MCP.servers.email import email_mcp

            return email_mcp
        case MCPServer.FORECAST:
            from memory.api.MCP.servers.forecast import forecast_mcp

            return forecast_mcp
        case MCPServer.INGEST:
            from memory.api.MCP.servers.ingest import ingest_mcp

            return ingest_mcp
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
    "effective_disabled_servers",
    "tool_server",
    "unmounted_servers",
    "get_server_instance",
]
