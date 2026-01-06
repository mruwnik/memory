"""Tests for the MCP visibility system."""

import pytest
from sqlalchemy.orm import Session
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as mt
from fastmcp.tools.tool import Tool

from memory.api.MCP.visibility import (
    has_items,
    require_scopes,
    visible_when,
    register_visibility,
    get_visibility_checker,
    clear_checkers,
)
from memory.api.MCP.visibility_middleware import VisibilityMiddleware


@pytest.fixture(autouse=True)
def clean_checkers():
    """Clear checkers before and after each test."""
    clear_checkers()
    yield
    clear_checkers()


@pytest.fixture
def mock_session():
    return MagicMock(spec=Session)


# --- require_scopes tests ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "required_scopes,user_scopes,expected",
    [
        (("read",), ["read", "write"], True),  # has required scope
        (("admin",), ["read", "write"], False),  # missing scope
        (("admin",), ["*"], True),  # wildcard grants access
        (("admin", "superuser"), ["superuser"], True),  # any scope matches
        (("read",), [], False),  # empty scopes
    ],
)
async def test_require_scopes(mock_session, required_scopes, user_scopes, expected):
    checker = require_scopes(*required_scopes)
    user_info = {"scopes": user_scopes}
    assert await checker(user_info, mock_session) is expected


@pytest.mark.asyncio
async def test_require_scopes_missing_key(mock_session):
    """Missing scopes key should deny access."""
    checker = require_scopes("read")
    assert await checker({}, mock_session) is False


# --- has_items tests ---


class FakeModel:
    """Fake model for testing has_items."""

    pass


@pytest.mark.asyncio
async def test_has_items_with_results(mock_session):
    """has_items returns True when items exist."""
    mock_session.query.return_value.limit.return_value.count.return_value = 1
    checker = has_items(FakeModel)
    assert await checker({}, mock_session) is True


@pytest.mark.asyncio
async def test_has_items_empty(mock_session):
    """has_items returns False when no items exist."""
    mock_session.query.return_value.limit.return_value.count.return_value = 0
    checker = has_items(FakeModel)
    assert await checker({}, mock_session) is False


@pytest.mark.asyncio
async def test_has_items_no_session():
    """has_items returns True when session is None (graceful degradation)."""
    checker = has_items(FakeModel)
    assert await checker({}, None) is True


def test_has_items_checker_name():
    """has_items sets descriptive __name__."""
    checker = has_items(FakeModel)
    assert checker.__name__ == "has_items(FakeModel)"


# --- visible_when decorator tests ---


def test_visible_when_single_checker_registers():
    checker = require_scopes("test")

    @visible_when(checker)
    def my_tool():
        pass

    # Single checker uses combined wrapper now
    registered = get_visibility_checker("my_tool")
    assert registered is not None


def test_visible_when_preserves_function():
    @visible_when(require_scopes("test"))
    def my_func():
        return "hello"

    assert my_func() == "hello"
    assert my_func.__name__ == "my_func"


def test_visible_when_no_checkers_does_not_register():
    @visible_when()
    def unrestricted_tool():
        pass

    assert get_visibility_checker("unrestricted_tool") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_scopes,expected",
    [
        (["read", "write"], True),  # has both scopes
        (["read"], False),  # missing one scope
        (["write"], False),  # missing other scope
        ([], False),  # no scopes
    ],
)
async def test_visible_when_multiple_checkers_and_logic(mock_session, user_scopes, expected):
    """Multiple checkers are combined with AND logic."""
    checker1 = require_scopes("read")
    checker2 = require_scopes("write")

    @visible_when(checker1, checker2)
    def multi_tool():
        pass

    combined = get_visibility_checker("multi_tool")
    assert combined is not None

    user_info = {"scopes": user_scopes}
    assert await combined(user_info, mock_session) is expected


@pytest.mark.asyncio
async def test_visible_when_multiple_checkers_fails_fast(mock_session):
    """Combined checkers should fail as soon as one fails."""
    call_count = 0

    async def counting_checker(user_info, session):
        nonlocal call_count
        call_count += 1
        return False  # Always fails

    async def never_called(user_info, session):
        nonlocal call_count
        call_count += 1
        return True

    @visible_when(counting_checker, never_called)
    def fail_fast_tool():
        pass

    combined = get_visibility_checker("fail_fast_tool")
    result = await combined({}, mock_session)

    assert result is False
    assert call_count == 1  # Second checker never called


# --- Registry tests ---


def test_registry_register_and_get():
    checker = require_scopes("test")
    register_visibility("test_tool", checker)
    assert get_visibility_checker("test_tool") is checker


def test_registry_get_missing_returns_none():
    assert get_visibility_checker("nonexistent") is None


def test_registry_clear_removes_all():
    register_visibility("tool1", require_scopes("a"))
    register_visibility("tool2", require_scopes("b"))
    clear_checkers()
    assert get_visibility_checker("tool1") is None
    assert get_visibility_checker("tool2") is None


# --- VisibilityMiddleware tests ---


@pytest.fixture
def make_tool():
    """Factory for creating mock Tool objects."""

    def _make_tool(name: str) -> MagicMock:
        tool = MagicMock(spec=Tool)
        tool.name = name
        return tool

    return _make_tool


@pytest.fixture
def user_info():
    return {"scopes": ["read"], "user": {"user_id": "test-user"}}


@pytest.fixture
def middleware(user_info):
    """Create middleware with test user info."""
    return VisibilityMiddleware(
        get_user_info=lambda: user_info,
        prefixes=["core", "github"],
    )


# --- _get_base_tool_name tests ---


@pytest.mark.parametrize(
    "tool_name,prefixes,expected",
    [
        ("core_search", ["core"], "search"),
        ("github_list_issues", ["core", "github"], "list_issues"),
        ("unprefixed_tool", ["core"], "unprefixed_tool"),
        ("core_search", [], "core_search"),  # No prefixes configured
        ("core_nested_name_here", ["core"], "nested_name_here"),
    ],
)
def test_get_base_tool_name(tool_name, prefixes, expected):
    middleware = VisibilityMiddleware(get_user_info=lambda: {}, prefixes=prefixes)
    assert middleware._get_base_tool_name(tool_name) == expected


# --- on_list_tools tests ---


@pytest.mark.asyncio
async def test_on_list_tools_filters_by_visibility(middleware, make_tool):
    """on_list_tools filters out tools user can't access."""
    # Register checker that only allows "allowed_tool"
    async def allow_only_allowed(user_info, session):
        return True

    async def deny_all(user_info, session):
        return False

    register_visibility("allowed_tool", allow_only_allowed)
    register_visibility("denied_tool", deny_all)

    tools = [
        make_tool("allowed_tool"),
        make_tool("denied_tool"),
        make_tool("public_tool"),  # No checker = public
    ]

    call_next = AsyncMock(return_value=tools)
    context = MagicMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_list_tools(context, call_next)

    assert len(result) == 2
    tool_names = [t.name for t in result]
    assert "allowed_tool" in tool_names
    assert "public_tool" in tool_names
    assert "denied_tool" not in tool_names


@pytest.mark.asyncio
async def test_on_list_tools_strips_prefix_for_checker_lookup(make_tool):
    """Middleware strips prefix when looking up checker."""
    # Register checker for base name (without prefix)
    async def checker(user_info, session):
        return False  # Deny

    register_visibility("my_tool", checker)

    middleware = VisibilityMiddleware(
        get_user_info=lambda: {"scopes": []},
        prefixes=["core"],
    )

    tools = [make_tool("core_my_tool")]  # Prefixed name
    call_next = AsyncMock(return_value=tools)
    context = MagicMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_list_tools(context, call_next)

    # Tool should be filtered out (checker denies)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_on_list_tools_checker_error_denies_access(middleware, make_tool):
    """If checker raises exception, tool is filtered out (fail closed)."""

    async def failing_checker(user_info, session):
        raise RuntimeError("Database connection failed")

    register_visibility("error_tool", failing_checker)

    tools = [make_tool("error_tool"), make_tool("public_tool")]
    call_next = AsyncMock(return_value=tools)
    context = MagicMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_list_tools(context, call_next)

    # Only public_tool should remain (error_tool denied due to exception)
    assert len(result) == 1
    assert result[0].name == "public_tool"


# --- on_call_tool tests ---


@pytest.mark.asyncio
async def test_on_call_tool_allows_public_tools(middleware):
    """Tools without visibility checker are allowed."""
    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "public_tool"

    expected_result = mt.CallToolResult(
        content=[mt.TextContent(type="text", text="Success")]
    )
    call_next = AsyncMock(return_value=expected_result)

    result = await middleware.on_call_tool(context, call_next)

    assert result == expected_result
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_call_tool_allows_when_checker_passes(middleware):
    """Tool call allowed when checker returns True."""

    async def allow_checker(user_info, session):
        return True

    register_visibility("allowed_tool", allow_checker)

    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "allowed_tool"

    expected_result = mt.CallToolResult(
        content=[mt.TextContent(type="text", text="Success")]
    )
    call_next = AsyncMock(return_value=expected_result)

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_call_tool(context, call_next)

    assert result == expected_result
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_call_tool_denies_when_checker_fails(middleware):
    """Tool call denied when checker returns False."""

    async def deny_checker(user_info, session):
        return False

    register_visibility("denied_tool", deny_checker)

    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "denied_tool"

    call_next = AsyncMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_call_tool(context, call_next)

    # Should return error result
    assert result.isError is True
    assert "Access denied" in result.content[0].text
    assert "denied_tool" in result.content[0].text

    # call_next should NOT be called
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_call_tool_checker_error_returns_error_result(middleware):
    """If checker raises exception, return error result."""

    async def failing_checker(user_info, session):
        raise RuntimeError("Database connection failed")

    register_visibility("error_tool", failing_checker)

    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "error_tool"

    call_next = AsyncMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_call_tool(context, call_next)

    # Should return error result
    assert result.isError is True
    assert "Access check failed" in result.content[0].text
    assert "error_tool" in result.content[0].text

    # call_next should NOT be called
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_call_tool_strips_prefix_for_checker_lookup():
    """on_call_tool strips prefix when looking up checker."""

    async def deny_checker(user_info, session):
        return False

    register_visibility("my_tool", deny_checker)

    middleware = VisibilityMiddleware(
        get_user_info=lambda: {"scopes": []},
        prefixes=["core"],
    )

    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "core_my_tool"  # Prefixed name

    call_next = AsyncMock()

    with patch("memory.api.MCP.visibility_middleware.make_session"):
        result = await middleware.on_call_tool(context, call_next)

    # Should be denied (checker found via base name lookup)
    assert result.isError is True
    call_next.assert_not_awaited()
