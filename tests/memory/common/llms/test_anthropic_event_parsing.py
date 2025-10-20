"""Comprehensive tests for Anthropic stream event parsing."""

import pytest
from unittest.mock import Mock

from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.base import StreamEvent


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="test-key", model="claude-3-opus-20240229")


# Content Block Start Tests


@pytest.mark.parametrize(
    "block_type,block_attrs,expected_tool_use",
    [
        (
            "tool_use",
            {"id": "tool-1", "name": "search", "input": {}},
            {
                "id": "tool-1",
                "name": "search",
                "input": {},
                "server_name": None,
                "is_server_call": False,
            },
        ),
        (
            "mcp_tool_use",
            {
                "id": "mcp-1",
                "name": "mcp_search",
                "input": {},
                "server_name": "mcp-server",
            },
            {
                "id": "mcp-1",
                "name": "mcp_search",
                "input": {},
                "server_name": "mcp-server",
                "is_server_call": True,
            },
        ),
        (
            "server_tool_use",
            {
                "id": "srv-1",
                "name": "server_action",
                "input": {},
                "server_name": "custom-server",
            },
            {
                "id": "srv-1",
                "name": "server_action",
                "input": {},
                "server_name": "custom-server",
                "is_server_call": True,
            },
        ),
    ],
)
def test_content_block_start_tool_types(
    provider, block_type, block_attrs, expected_tool_use
):
    """Different tool types should be tracked correctly."""
    block = Mock(spec=["type"] + list(block_attrs.keys()))
    block.type = block_type
    for key, value in block_attrs.items():
        setattr(block, key, value)

    event = Mock(spec=["type", "content_block"])
    event.type = "content_block_start"
    event.content_block = block

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None
    assert tool_use == expected_tool_use


def test_content_block_start_tool_without_input(provider):
    """Tool use without input field should initialize as empty string."""
    block = Mock(spec=["type", "id", "name"])
    block.type = "tool_use"
    block.id = "tool-2"
    block.name = "calculate"

    event = Mock(spec=["type", "content_block"])
    event.type = "content_block_start"
    event.content_block = block

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert tool_use["input"] == ""


def test_content_block_start_tool_result(provider):
    """Tool result blocks should emit tool_result event."""
    block = Mock(spec=["tool_use_id", "content"])
    block.tool_use_id = "tool-1"
    block.content = "Result content"

    event = Mock(spec=["type", "content_block"])
    event.type = "content_block_start"
    event.content_block = block

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is not None
    assert stream_event.type == "tool_result"
    assert stream_event.data == {"id": "tool-1", "result": "Result content"}


@pytest.mark.parametrize(
    "has_content_block,block_type",
    [
        (False, None),
        (True, "unknown_type"),
    ],
)
def test_content_block_start_ignored_cases(provider, has_content_block, block_type):
    """Events without content_block or with unknown types should be ignored."""
    event = Mock(spec=["type", "content_block"] if has_content_block else ["type"])
    event.type = "content_block_start"

    if has_content_block:
        block = Mock(spec=["type"])
        block.type = block_type
        event.content_block = block

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None
    assert tool_use is None


# Content Block Delta Tests


@pytest.mark.parametrize(
    "delta_type,delta_attr,attr_value,expected_type,expected_data",
    [
        ("text_delta", "text", "Hello world", "text", "Hello world"),
        ("text_delta", "text", "", "text", ""),
        (
            "thinking_delta",
            "thinking",
            "Let me think...",
            "thinking",
            "Let me think...",
        ),
        ("signature_delta", "signature", "sig-12345", "thinking", None),
    ],
)
def test_content_block_delta_types(
    provider, delta_type, delta_attr, attr_value, expected_type, expected_data
):
    """Different delta types should emit appropriate events."""
    delta = Mock(spec=["type", delta_attr])
    delta.type = delta_type
    setattr(delta, delta_attr, attr_value)

    event = Mock(spec=["type", "delta"])
    event.type = "content_block_delta"
    event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event.type == expected_type
    if expected_type == "thinking" and delta_type == "signature_delta":
        assert stream_event.signature == attr_value
    else:
        assert stream_event.data == expected_data


@pytest.mark.parametrize(
    "current_tool,partial_json,expected_input",
    [
        (
            {"id": "t1", "name": "search", "input": '{"query": "'},
            'test"}',
            '{"query": "test"}',
        ),
        (
            {"id": "t1", "name": "search", "input": '{"'},
            'key": "value"}',
            '{"key": "value"}',
        ),
        (
            {"id": "t1", "name": "search", "input": ""},
            '{"query": "test"}',
            '{"query": "test"}',
        ),
    ],
)
def test_content_block_delta_input_json_accumulation(
    provider, current_tool, partial_json, expected_input
):
    """JSON delta should accumulate to tool input."""
    delta = Mock(spec=["type", "partial_json"])
    delta.type = "input_json_delta"
    delta.partial_json = partial_json

    event = Mock(spec=["type", "delta"])
    event.type = "content_block_delta"
    event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert stream_event is None
    assert tool_use["input"] == expected_input


def test_content_block_delta_input_json_without_tool(provider):
    """JSON delta without tool context should return None."""
    delta = Mock(spec=["type", "partial_json"])
    delta.type = "input_json_delta"
    delta.partial_json = '{"key": "value"}'

    event = Mock(spec=["type", "delta"])
    event.type = "content_block_delta"
    event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None
    assert tool_use is None


def test_content_block_delta_input_json_with_dict_input(provider):
    """JSON delta shouldn't modify if input is already a dict."""
    current_tool = {"id": "t1", "name": "search", "input": {"query": "test"}}

    delta = Mock(spec=["type", "partial_json"])
    delta.type = "input_json_delta"
    delta.partial_json = ', "extra": "data"'

    event = Mock(spec=["type", "delta"])
    event.type = "content_block_delta"
    event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert tool_use["input"] == {"query": "test"}


@pytest.mark.parametrize(
    "has_delta,delta_type",
    [
        (False, None),
        (True, "unknown_delta"),
    ],
)
def test_content_block_delta_ignored_cases(provider, has_delta, delta_type):
    """Events without delta or with unknown types should be ignored."""
    event = Mock(spec=["type", "delta"] if has_delta else ["type"])
    event.type = "content_block_delta"

    if has_delta:
        delta = Mock(spec=["type"])
        delta.type = delta_type
        event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None


# Content Block Stop Tests


@pytest.mark.parametrize(
    "input_value,has_content_block,expected_input",
    [
        ("", False, {}),
        ("   \n\t  ", False, {}),
        ('{"invalid": json}', False, {}),
        ('{"query": "test", "limit": 10}', False, {"query": "test", "limit": 10}),
        (
            '{"filters": {"type": "user", "status": ["active", "pending"]}, "limit": 100}',
            False,
            {
                "filters": {"type": "user", "status": ["active", "pending"]},
                "limit": 100,
            },
        ),
        ("", True, {"query": "test"}),
    ],
)
def test_content_block_stop_tool_finalization(
    provider, input_value, has_content_block, expected_input
):
    """Tool stop should parse or use provided input correctly."""
    current_tool = {"id": "t1", "name": "search", "input": input_value}

    event = Mock(spec=["type", "content_block"] if has_content_block else ["type"])
    event.type = "content_block_stop"

    if has_content_block:
        block = Mock(spec=["input"])
        block.input = {"query": "test"}
        event.content_block = block

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert stream_event.type == "tool_use"
    assert stream_event.data["input"] == expected_input
    assert tool_use is None


def test_content_block_stop_with_server_info(provider):
    """Server tool info should be included in final event."""
    current_tool = {
        "id": "t1",
        "name": "mcp_search",
        "input": '{"q": "test"}',
        "server_name": "mcp-server",
        "is_server_call": True,
    }

    event = Mock(spec=["type"])
    event.type = "content_block_stop"

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert stream_event.data["server_name"] == "mcp-server"
    assert stream_event.data["is_server_call"] is True


def test_content_block_stop_without_tool(provider):
    """Stop without current tool should return None."""
    event = Mock(spec=["type"])
    event.type = "content_block_stop"

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None
    assert tool_use is None


# Message Delta Tests


def test_message_delta_max_tokens(provider):
    """Max tokens stop reason should emit error."""
    delta = Mock(spec=["stop_reason"])
    delta.stop_reason = "max_tokens"

    event = Mock(spec=["type", "delta"])
    event.type = "message_delta"
    event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event.type == "error"
    assert "Max tokens" in stream_event.data


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence", None])
def test_message_delta_other_stop_reasons(provider, stop_reason):
    """Other stop reasons should not emit error."""
    delta = Mock(spec=["stop_reason"])
    delta.stop_reason = stop_reason

    event = Mock(spec=["type", "delta"])
    event.type = "message_delta"
    event.delta = delta

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None


def test_message_delta_token_usage(provider):
    """Token usage should be logged but not emitted."""
    usage = Mock(
        spec=[
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ]
    )
    usage.input_tokens = 100
    usage.output_tokens = 50
    usage.cache_creation_input_tokens = 10
    usage.cache_read_input_tokens = 20

    event = Mock(spec=["type", "usage"])
    event.type = "message_delta"
    event.usage = usage

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None


def test_message_delta_empty(provider):
    """Message delta without delta or usage should return None."""
    event = Mock(spec=["type"])
    event.type = "message_delta"

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None


# Message Stop Tests


@pytest.mark.parametrize(
    "current_tool",
    [
        None,
        {"id": "t1", "name": "search", "input": '{"incomplete'},
    ],
)
def test_message_stop(provider, current_tool):
    """Message stop should emit done regardless of incomplete tools."""
    event = Mock(spec=["type"])
    event.type = "message_stop"

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert stream_event.type == "done"
    assert tool_use is None


# Error Handling Tests


@pytest.mark.parametrize(
    "has_error,error_value,expected_message",
    [
        (True, "API rate limit exceeded", "rate limit"),
        (False, None, "Unknown error"),
    ],
)
def test_error_events(provider, has_error, error_value, expected_message):
    """Error events should emit error StreamEvent."""
    event = Mock(spec=["type", "error"] if has_error else ["type"])
    event.type = "error"
    if has_error:
        event.error = error_value

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event.type == "error"
    assert expected_message in stream_event.data


# Unknown Event Tests


@pytest.mark.parametrize(
    "event_type",
    ["message_start", "future_event_type", None],
)
def test_unknown_or_ignored_events(provider, event_type):
    """Unknown event types should be logged but not fail."""
    if event_type is None:
        event = Mock(spec=[])
    else:
        event = Mock(spec=["type"])
        event.type = event_type

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None


# State Transition Tests


def test_complete_tool_call_sequence(provider):
    """Simulate a complete tool call from start to finish."""
    # Start
    block = Mock(spec=["type", "id", "name", "input"])
    block.type = "tool_use"
    block.id = "tool-1"
    block.name = "search"
    block.input = None

    event1 = Mock(spec=["type", "content_block"])
    event1.type = "content_block_start"
    event1.content_block = block

    _, tool_use = provider._handle_stream_event(event1, None)
    assert tool_use["input"] == ""

    # Delta 1
    delta1 = Mock(spec=["type", "partial_json"])
    delta1.type = "input_json_delta"
    delta1.partial_json = '{"query":'

    event2 = Mock(spec=["type", "delta"])
    event2.type = "content_block_delta"
    event2.delta = delta1

    _, tool_use = provider._handle_stream_event(event2, tool_use)
    assert tool_use["input"] == '{"query":'

    # Delta 2
    delta2 = Mock(spec=["type", "partial_json"])
    delta2.type = "input_json_delta"
    delta2.partial_json = ' "test"}'

    event3 = Mock(spec=["type", "delta"])
    event3.type = "content_block_delta"
    event3.delta = delta2

    _, tool_use = provider._handle_stream_event(event3, tool_use)
    assert tool_use["input"] == '{"query": "test"}'

    # Stop
    event4 = Mock(spec=["type"])
    event4.type = "content_block_stop"

    stream_event, tool_use = provider._handle_stream_event(event4, tool_use)

    assert stream_event.type == "tool_use"
    assert stream_event.data["input"] == {"query": "test"}
    assert tool_use is None


def test_text_and_thinking_mixed(provider):
    """Text and thinking deltas should be handled independently."""
    delta1 = Mock(spec=["type", "text"])
    delta1.type = "text_delta"
    delta1.text = "Answer: "

    event1 = Mock(spec=["type", "delta"])
    event1.type = "content_block_delta"
    event1.delta = delta1

    event1_result, _ = provider._handle_stream_event(event1, None)
    assert event1_result.type == "text"

    delta2 = Mock(spec=["type", "thinking"])
    delta2.type = "thinking_delta"
    delta2.thinking = "reasoning..."

    event2 = Mock(spec=["type", "delta"])
    event2.type = "content_block_delta"
    event2.delta = delta2

    event2_result, _ = provider._handle_stream_event(event2, None)
    assert event2_result.type == "thinking"
