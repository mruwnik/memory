"""Comprehensive tests for OpenAI stream chunk parsing."""

import pytest
from unittest.mock import Mock

from memory.common.llms.openai_provider import OpenAIProvider
from memory.common.llms.base import StreamEvent


@pytest.fixture
def provider():
    return OpenAIProvider(api_key="test-key", model="gpt-4o")


# Text Content Tests


@pytest.mark.parametrize(
    "content,expected_events",
    [
        ("Hello", 1),
        ("", 0),  # Empty string is falsy
        (None, 0),
        ("Line 1\nLine 2\nLine 3", 1),
        ("Hello 世界 🌍", 1),
    ],
)
def test_text_content(provider, content, expected_events):
    """Text content should emit text events appropriately."""
    delta = Mock(spec=["content", "tool_calls"])
    delta.content = content
    delta.tool_calls = None

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == expected_events
    if expected_events > 0:
        assert events[0].type == "text"
        assert events[0].data == content
    assert tool_call is None


# Tool Call Start Tests


def test_new_tool_call_basic(provider):
    """New tool call should initialize state."""
    function = Mock(spec=["name", "arguments"])
    function.name = "search"
    function.arguments = ""

    tool = Mock(spec=["id", "function"])
    tool.id = "call_123"
    tool.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = [tool]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 0
    assert tool_call == {"id": "call_123", "name": "search", "arguments": ""}


@pytest.mark.parametrize(
    "name,arguments,expected_name,expected_args",
    [
        ("calculate", '{"operation":', "calculate", '{"operation":'),
        (None, "", "", ""),
        ("test", None, "test", ""),
    ],
)
def test_new_tool_call_variations(
    provider, name, arguments, expected_name, expected_args
):
    """Tool calls with various name/argument combinations."""
    function = Mock(spec=["name", "arguments"])
    function.name = name
    function.arguments = arguments

    tool = Mock(spec=["id", "function"])
    tool.id = "call_123"
    tool.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = [tool]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert tool_call["name"] == expected_name
    assert tool_call["arguments"] == expected_args


def test_new_tool_call_replaces_previous(provider):
    """New tool call should finalize and replace previous."""
    current = {"id": "call_old", "name": "old_tool", "arguments": '{"arg": "value"}'}

    function = Mock(spec=["name", "arguments"])
    function.name = "new_tool"
    function.arguments = ""

    tool = Mock(spec=["id", "function"])
    tool.id = "call_new"
    tool.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = [tool]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, current)

    assert len(events) == 1
    assert events[0].type == "tool_use"
    assert events[0].data["id"] == "call_old"
    assert events[0].data["input"] == {"arg": "value"}
    assert tool_call["id"] == "call_new"


# Tool Call Continuation Tests


@pytest.mark.parametrize(
    "initial_args,new_args,expected_args",
    [
        ('{"query": "', 'test query"}', '{"query": "test query"}'),
        ('{"query"', ': "value"}', '{"query": "value"}'),
        ("", '{"full": "json"}', '{"full": "json"}'),
        ('{"partial"', "", '{"partial"'),  # Empty doesn't accumulate
    ],
)
def test_tool_call_argument_accumulation(
    provider, initial_args, new_args, expected_args
):
    """Arguments should accumulate correctly."""
    current = {"id": "call_123", "name": "search", "arguments": initial_args}

    function = Mock(spec=["name", "arguments"])
    function.name = None
    function.arguments = new_args

    tool = Mock(spec=["id", "function"])
    tool.id = None
    tool.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = [tool]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, current)

    assert len(events) == 0
    assert tool_call["arguments"] == expected_args


def test_tool_call_accumulation_without_current_tool(provider):
    """Arguments without current tool should be ignored."""
    function = Mock(spec=["name", "arguments"])
    function.name = None
    function.arguments = '{"arg": "value"}'

    tool = Mock(spec=["id", "function"])
    tool.id = None
    tool.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = [tool]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 0
    assert tool_call is None


def test_incremental_json_building(provider):
    """Test realistic incremental JSON building across multiple chunks."""
    current = {"id": "c1", "name": "search", "arguments": ""}

    increments = ['{"', 'query":', ' "test"}']
    expected_states = ['{"', '{"query":', '{"query": "test"}']

    for increment, expected in zip(increments, expected_states):
        function = Mock(spec=["name", "arguments"])
        function.name = None
        function.arguments = increment

        tool = Mock(spec=["id", "function"])
        tool.id = None
        tool.function = function

        delta = Mock(spec=["content", "tool_calls"])
        delta.content = None
        delta.tool_calls = [tool]

        choice = Mock(spec=["delta", "finish_reason"])
        choice.delta = delta
        choice.finish_reason = None

        chunk = Mock(spec=["choices"])
        chunk.choices = [choice]

        _, current = provider._handle_stream_chunk(chunk, current)
        assert current["arguments"] == expected


# Finish Reason Tests


def test_finish_reason_without_tool(provider):
    """Stop finish without tool should not emit events."""
    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = None

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = "stop"

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 0
    assert tool_call is None


@pytest.mark.parametrize(
    "arguments,expected_input",
    [
        ('{"query": "test"}', {"query": "test"}),
        ('{"invalid": json}', {}),
        ("", {}),
    ],
)
def test_finish_reason_with_tool(provider, arguments, expected_input):
    """Finish with tool call should finalize and emit."""
    current = {"id": "call_123", "name": "search", "arguments": arguments}

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = None

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = "tool_calls"

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, current)

    assert len(events) == 1
    assert events[0].type == "tool_use"
    assert events[0].data["id"] == "call_123"
    assert events[0].data["input"] == expected_input
    assert tool_call is None


@pytest.mark.parametrize("reason", ["stop", "length", "content_filter", "tool_calls"])
def test_various_finish_reasons(provider, reason):
    """Various finish reasons with active tool should finalize."""
    current = {"id": "call_123", "name": "test", "arguments": '{"a": 1}'}

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = None

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = reason

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, current)

    assert len(events) == 1
    assert tool_call is None


# Edge Cases Tests


def test_empty_choices(provider):
    """Empty choices list should return empty events."""
    chunk = Mock(spec=["choices"])
    chunk.choices = []

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 0
    assert tool_call is None


def test_none_choices(provider):
    """None choices should be handled gracefully."""
    chunk = Mock(spec=["choices"])
    chunk.choices = None

    try:
        events, tool_call = provider._handle_stream_chunk(chunk, None)
        assert len(events) == 0
    except (TypeError, AttributeError):
        pass  # Also acceptable for malformed input


def test_multiple_chunks_in_sequence(provider):
    """Test processing multiple chunks sequentially."""
    # Chunk 1: Start
    function1 = Mock(spec=["name", "arguments"])
    function1.name = "search"
    function1.arguments = ""

    tool1 = Mock(spec=["id", "function"])
    tool1.id = "call_1"
    tool1.function = function1

    delta1 = Mock(spec=["content", "tool_calls"])
    delta1.content = None
    delta1.tool_calls = [tool1]

    choice1 = Mock(spec=["delta", "finish_reason"])
    choice1.delta = delta1
    choice1.finish_reason = None

    chunk1 = Mock(spec=["choices"])
    chunk1.choices = [choice1]

    events1, state = provider._handle_stream_chunk(chunk1, None)
    assert len(events1) == 0
    assert state is not None

    # Chunk 2: Args
    function2 = Mock(spec=["name", "arguments"])
    function2.name = None
    function2.arguments = '{"q": "test"}'

    tool2 = Mock(spec=["id", "function"])
    tool2.id = None
    tool2.function = function2

    delta2 = Mock(spec=["content", "tool_calls"])
    delta2.content = None
    delta2.tool_calls = [tool2]

    choice2 = Mock(spec=["delta", "finish_reason"])
    choice2.delta = delta2
    choice2.finish_reason = None

    chunk2 = Mock(spec=["choices"])
    chunk2.choices = [choice2]

    events2, state = provider._handle_stream_chunk(chunk2, state)
    assert len(events2) == 0
    assert state["arguments"] == '{"q": "test"}'

    # Chunk 3: Finish
    delta3 = Mock(spec=["content", "tool_calls"])
    delta3.content = None
    delta3.tool_calls = None

    choice3 = Mock(spec=["delta", "finish_reason"])
    choice3.delta = delta3
    choice3.finish_reason = "stop"

    chunk3 = Mock(spec=["choices"])
    chunk3.choices = [choice3]

    events3, state = provider._handle_stream_chunk(chunk3, state)
    assert len(events3) == 1
    assert events3[0].type == "tool_use"
    assert state is None


def test_text_and_tool_calls_mixed(provider):
    """Text content should be emitted before tool initialization."""
    function = Mock(spec=["name", "arguments"])
    function.name = "search"
    function.arguments = ""

    tool = Mock(spec=["id", "function"])
    tool.id = "call_1"
    tool.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = "Let me search for that."
    delta.tool_calls = [tool]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices"])
    chunk.choices = [choice]

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 1
    assert events[0].type == "text"
    assert events[0].data == "Let me search for that."
    assert tool_call is not None


# JSON Parsing Tests


@pytest.mark.parametrize(
    "arguments,expected_input",
    [
        ('{"key": "value", "num": 42}', {"key": "value", "num": 42}),
        ("{}", {}),
        (
            '{"user": {"name": "John", "tags": ["a", "b"]}, "count": 10}',
            {"user": {"name": "John", "tags": ["a", "b"]}, "count": 10},
        ),
        ('{"invalid": json}', {}),
        ('{"key": "val', {}),
        ("", {}),
        ('{"text": "Hello 世界 🌍"}', {"text": "Hello 世界 🌍"}),
        (
            '{"text": "Line 1\\nLine 2\\t\\tTabbed"}',
            {"text": "Line 1\nLine 2\t\tTabbed"},
        ),
    ],
)
def test_json_parsing(provider, arguments, expected_input):
    """Various JSON inputs should be parsed correctly."""
    tool_call = {"id": "c1", "name": "test", "arguments": arguments}

    result = provider._parse_and_finalize_tool_call(tool_call)

    assert result["input"] == expected_input
    assert "arguments" not in result
