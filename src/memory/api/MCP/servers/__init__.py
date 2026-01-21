"""MCP subservers for composable tool organization."""

from memory.api.MCP.servers.books import books_mcp
from memory.api.MCP.servers.core import core_mcp
from memory.api.MCP.servers.discord import discord_mcp
from memory.api.MCP.servers.email import email_mcp
from memory.api.MCP.servers.github import github_mcp
from memory.api.MCP.servers.meta import meta_mcp
from memory.api.MCP.servers.organizer import organizer_mcp
from memory.api.MCP.servers.people import people_mcp
from memory.api.MCP.servers.polling import polling_mcp
from memory.api.MCP.servers.schedule import schedule_mcp

__all__ = [
    "books_mcp",
    "core_mcp",
    "discord_mcp",
    "email_mcp",
    "github_mcp",
    "meta_mcp",
    "organizer_mcp",
    "people_mcp",
    "polling_mcp",
    "schedule_mcp",
]
