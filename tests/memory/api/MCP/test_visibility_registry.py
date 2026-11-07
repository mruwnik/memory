"""Visibility checks against the real mounted MCP server and its registry."""

from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from memory.api.MCP.base import mcp
from memory.api.MCP.visibility import get_call_checker, get_visibility_checker
from memory.api.MCP.visibility_middleware import VisibilityMiddleware


@pytest.mark.asyncio
async def test_every_mounted_tool_has_its_own_checker():
    mounted_tools = await mcp.get_tools()
    checkers = {name: get_visibility_checker(name) for name in mounted_tools}

    assert [name for name, c in checkers.items() if c is None] == []

    by_checker = defaultdict(list)
    for name, checker in checkers.items():
        by_checker[id(checker)].append(name)
    assert [names for names in by_checker.values() if len(names) > 1] == []


@pytest.mark.asyncio
async def test_every_mounted_tool_forbids_task_execution():
    # Task-augmented calls run outside VisibilityMiddleware.on_call_tool.
    # A tool only accepts them when it advertises `execution.taskSupport`.
    mounted_tools = await mcp.get_tools()

    assert [n for n, t in mounted_tools.items() if t.to_mcp_tool().execution] == []


# Forecast lookups only read, but send caller-chosen text to outside services.
READ_ONLY_TOOLS = {
    *("agent_catalog", "agent_fetch", "agent_key_list", "agent_list_all"),
    *("agent_list_runs", "agent_schedules", "agent_server_list", "agent_system_list"),
    *("agent_triggers", "agent_wait"),
    "beeper_list_chats",
    *("books_fetch", "books_list_books"),
    *("claude_session_fetch", "claude_session_search"),
    *("core_count_items", "core_fetch", "core_fetch_file", "core_list_items"),
    *("core_search", "core_search_observations"),
    *("deadlines_fetch", "deadlines_list_upcoming"),
    *("discord_channel_history", "discord_list_channels", "discord_thread"),
    "github_pr_diff",
    "journal_list_all",
    *("meta_get_current_time", "meta_get_metadata_schemas", "meta_get_search_defaults"),
    *("meta_get_tags", "meta_get_user"),
    "notes_note_files",
    *("organizer_fetch", "organizer_list_tasks", "organizer_upcoming"),
    *("people_fetch", "people_list_all", "people_tidbit_list"),
    *("polling_fetch", "polling_list_polls"),
    *("projects_fetch", "projects_list_all"),
    *("scheduler_executions", "scheduler_list_all"),
    *("slack_get_channel_history", "slack_list_channels", "slack_thread"),
    *("teams_fetch", "teams_list_all", "teams_project_list_access"),
}


@pytest.mark.asyncio
async def test_exactly_the_tools_that_only_read_are_marked_read_only():
    mounted_tools = await mcp.get_tools()

    marked = {
        name
        for name, t in mounted_tools.items()
        if t.annotations and t.annotations.readOnlyHint
    }
    assert marked == READ_ONLY_TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, scope", [("slack_thread", "slack"), ("discord_thread", "discord")])
@pytest.mark.parametrize("get_checker", [get_visibility_checker, get_call_checker])
async def test_thread_tools_need_only_their_scope(tool, scope, get_checker):
    # Stored messages are access-filtered, so readers without a connected
    # workspace or bot of their own (team access) can use them.
    assert await get_checker(tool)({"scopes": [scope]}, None) is True


@pytest.fixture
def no_people_scope_middleware(monkeypatch):
    """The production VisibilityMiddleware, as seen by a user without `people`."""
    middleware = next(m for m in mcp.middleware if isinstance(m, VisibilityMiddleware))
    user_info = {"authenticated": True, "scopes": ["teams", "read", "write"]}
    monkeypatch.setattr(middleware, "get_user_info", lambda: user_info)
    return middleware


@pytest.mark.asyncio
async def test_people_fetch_hidden_without_people_scope(no_people_scope_middleware):
    mounted_tools = await mcp.get_tools()
    middleware = no_people_scope_middleware
    call_next = AsyncMock(return_value=list(mounted_tools.values()))

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        visible = {t.key for t in await middleware.on_list_tools(MagicMock(), call_next)}

    assert "people_fetch" not in visible
    assert "teams_fetch" in visible


@pytest.mark.asyncio
async def test_people_fetch_not_callable_without_people_scope(no_people_scope_middleware):
    middleware = no_people_scope_middleware
    context = MagicMock()
    context.message.name = "people_fetch"
    call_next = AsyncMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        with pytest.raises(ToolError, match="Access denied: people_fetch"):
            await middleware.on_call_tool(context, call_next)

    call_next.assert_not_awaited()
