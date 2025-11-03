"""Tests for base web tool definitions."""

from memory.common.llms.tools.base import WebFetchTool, WebSearchTool


def test_web_search_tool_provider_formats():
    tool = WebSearchTool()

    assert tool.provider_format("openai") == {"type": "web_search"}
    assert tool.provider_format("anthropic") == {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 10,
    }
    assert tool.provider_format("unknown") is None


def test_web_fetch_tool_provider_formats():
    tool = WebFetchTool()

    assert tool.provider_format("anthropic") == {
        "type": "web_fetch_20250910",
        "name": "web_fetch",
        "max_uses": 10,
    }
    assert tool.provider_format("openai") is None
