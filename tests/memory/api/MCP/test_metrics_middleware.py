"""Tests for the MCP metrics middleware."""

import asyncio
from unittest.mock import Mock, patch

import pytest

from memory.api.MCP.metrics_middleware import MetricsMiddleware


@pytest.fixture
def middleware():
    """Create a MetricsMiddleware instance."""
    return MetricsMiddleware()


@pytest.fixture
def middleware_with_user_info():
    """Create a MetricsMiddleware with user info getter."""

    def get_user_info():
        return {"user": {"user_id": 42, "email": "test@example.com"}}

    return MetricsMiddleware(get_user_info=get_user_info)


@pytest.fixture
def middleware_with_prefixes():
    """Create a MetricsMiddleware with tool name prefixes."""
    return MetricsMiddleware(prefixes=["core", "github", "organizer"])


@pytest.fixture
def mock_context():
    """Create a mock middleware context."""
    context = Mock()
    context.message = Mock()
    context.message.name = "search_knowledge_base"
    return context


@pytest.fixture
def mock_call_next():
    """Create a mock call_next function."""

    async def call_next(context):
        return Mock(isError=False)

    return call_next


# ============== _get_base_tool_name tests ==============


def test_get_base_tool_name_no_prefix(middleware):
    """Test extracting base name when no prefix matches."""
    result = middleware._get_base_tool_name("search_knowledge_base")
    assert result == "search_knowledge_base"


def test_get_base_tool_name_with_prefix(middleware_with_prefixes):
    """Test extracting base name with matching prefix."""
    result = middleware_with_prefixes._get_base_tool_name("core_search_knowledge_base")
    assert result == "search_knowledge_base"


def test_get_base_tool_name_multiple_prefixes(middleware_with_prefixes):
    """Test with multiple possible prefixes."""
    assert (
        middleware_with_prefixes._get_base_tool_name("github_list_entities")
        == "list_entities"
    )
    assert (
        middleware_with_prefixes._get_base_tool_name("organizer_create_task")
        == "create_task"
    )


def test_get_base_tool_name_partial_match(middleware_with_prefixes):
    """Test that partial prefix matches don't strip."""
    # "cor" is not a prefix, so should not strip
    result = middleware_with_prefixes._get_base_tool_name("cor_tool")
    assert result == "cor_tool"


# ============== on_call_tool tests ==============


@pytest.mark.asyncio
async def test_on_call_tool_success(middleware, mock_context):
    """Test recording metrics for successful tool call."""
    mock_result = Mock(isError=False)

    async def call_next(ctx):
        await asyncio.sleep(0.01)  # Small delay to test timing
        return mock_result

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        result = await middleware.on_call_tool(mock_context, call_next)

        assert result is mock_result
        mock_record.assert_called_once()

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["metric_type"] == "mcp_call"
        assert call_kwargs["name"] == "search_knowledge_base"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["duration_ms"] >= 10  # At least 10ms
        assert "full_name" in call_kwargs["labels"]


@pytest.mark.asyncio
async def test_on_call_tool_error_result(middleware, mock_context):
    """Test recording metrics when tool returns an error."""
    mock_result = Mock(isError=True)

    async def call_next(ctx):
        return mock_result

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        result = await middleware.on_call_tool(mock_context, call_next)

        assert result is mock_result
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["status"] == "error"


@pytest.mark.asyncio
async def test_on_call_tool_exception(middleware, mock_context):
    """Test recording metrics when tool raises an exception."""

    async def call_next(ctx):
        raise ValueError("Tool failed")

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        with pytest.raises(ValueError, match="Tool failed"):
            await middleware.on_call_tool(mock_context, call_next)

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["status"] == "failure"


@pytest.mark.asyncio
async def test_on_call_tool_with_user_info(middleware_with_user_info, mock_context):
    """Test that user_id is added to labels when available."""
    mock_result = Mock(isError=False)

    async def call_next(ctx):
        return mock_result

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        await middleware_with_user_info.on_call_tool(mock_context, call_next)

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["labels"]["user_id"] == 42


@pytest.mark.asyncio
async def test_on_call_tool_user_info_error(mock_context):
    """Test that user info errors don't break metric recording."""

    def bad_user_info():
        raise RuntimeError("User info failed")

    middleware = MetricsMiddleware(get_user_info=bad_user_info)
    mock_result = Mock(isError=False)

    async def call_next(context):
        return mock_result

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        # Should not raise despite user info error
        result = await middleware.on_call_tool(mock_context, call_next)  # type: ignore[arg-type]

        assert result is mock_result
        mock_record.assert_called_once()
        # user_id should not be in labels
        assert "user_id" not in mock_record.call_args[1]["labels"]


@pytest.mark.asyncio
async def test_on_call_tool_with_prefix_stripping(middleware_with_prefixes):
    """Test that prefixes are stripped from tool names."""
    context = Mock()
    context.message = Mock()
    context.message.name = "core_search_knowledge_base"

    mock_result = Mock(isError=False)

    async def call_next(ctx):
        return mock_result

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        await middleware_with_prefixes.on_call_tool(context, call_next)

        call_kwargs = mock_record.call_args[1]
        # Base name should be stripped
        assert call_kwargs["name"] == "search_knowledge_base"
        # Full name should be preserved in labels
        assert call_kwargs["labels"]["full_name"] == "core_search_knowledge_base"


@pytest.mark.asyncio
async def test_on_call_tool_timing_accuracy(middleware, mock_context):
    """Test that timing is reasonably accurate."""

    async def call_next(ctx):
        await asyncio.sleep(0.05)  # 50ms
        return Mock(isError=False)

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        await middleware.on_call_tool(mock_context, call_next)

        call_kwargs = mock_record.call_args[1]
        duration = call_kwargs["duration_ms"]
        # Should be at least 50ms, allow some tolerance
        assert 45 <= duration <= 150


@pytest.mark.asyncio
async def test_on_call_tool_records_even_on_exception(middleware, mock_context):
    """Test that metrics are recorded even when tool raises."""

    async def call_next(ctx):
        raise RuntimeError("Unexpected error")

    with patch("memory.api.MCP.metrics_middleware.record_metric") as mock_record:
        with pytest.raises(RuntimeError):
            await middleware.on_call_tool(mock_context, call_next)

        # Metric should still be recorded
        mock_record.assert_called_once()
        assert mock_record.call_args[1]["status"] == "failure"
