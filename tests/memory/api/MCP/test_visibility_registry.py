"""Visibility checks against the real mounted MCP server and its registry."""

from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from memory.api.MCP.base import mcp
from memory.api.MCP.visibility import get_visibility_checker
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
