import pytest
from typing import Any, cast
from unittest.mock import Mock
from anthropic import transform_schema
from PIL import Image

from memory.api.search.query_analysis import _build_schema
from memory.api.search.scorer import SCORE_SCHEMA
from memory.common.summarizer import SUMMARY_SCHEMA
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.base import (
    Message,
    MessageRole,
    TextContent,
    ImageContent,
    ThinkingContent,
    LLMSettings,
)
from memory.common.llms.tools import MCPServer, ToolDefinition
from memory.workers.tasks.meetings import EXTRACTION_SCHEMA


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="test-key", model="claude-haiku-5-5")


def test_initialization(provider):
    assert provider.api_key == "test-key"
    assert provider.model == "claude-haiku-5-5"
    assert provider.enable_thinking is False


def test_client_lazy_loading(provider):
    assert provider._client is None
    client = provider.client
    assert client is not None
    assert provider._client is not None
    # Second call should return same instance
    assert provider.client is client


def test_async_client_lazy_loading(provider):
    assert provider._async_client is None
    client = provider.async_client
    assert client is not None
    assert provider._async_client is not None


def test_convert_text_content(provider):
    content = TextContent(text="hello world")
    result = provider._convert_text_content(content)
    assert result == {"type": "text", "text": "hello world"}


def test_convert_image_content(provider):
    image = Image.new("RGB", (100, 100), color="red")
    content = ImageContent(image=image)
    result = provider._convert_image_content(content)

    assert result["type"] == "image"
    assert result["source"]["type"] == "base64"
    assert result["source"]["media_type"] == "image/jpeg"
    assert isinstance(result["source"]["data"], str)


def test_should_include_message_filters_system(provider):
    system_msg = Message(role=MessageRole.SYSTEM, content="system prompt")
    user_msg = Message(role=MessageRole.USER, content="user message")

    assert provider._should_include_message(system_msg) is False
    assert provider._should_include_message(user_msg) is True


@pytest.mark.parametrize(
    "messages, expected_count",
    [
        ([Message(role=MessageRole.USER, content="test")], 1),
        ([Message(role=MessageRole.SYSTEM, content="test")], 0),
        (
            [
                Message(role=MessageRole.SYSTEM, content="system"),
                Message(role=MessageRole.USER, content="user"),
            ],
            1,
        ),
    ],
)
def test_convert_messages(provider, messages, expected_count):
    result = provider._convert_messages(messages)
    assert len(result) == expected_count


def test_convert_tool(provider):
    tool = ToolDefinition(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object", "properties": {}},
        function=lambda x: "result",
    )
    result = provider._convert_tool(tool)

    assert result["name"] == "test_tool"
    assert result["description"] == "A test tool"
    assert result["input_schema"] == {"type": "object", "properties": {}}


def test_build_request_kwargs_basic(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings(max_tokens=1000)

    kwargs = provider._build_request_kwargs(messages, None, None, None, settings)

    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["max_tokens"] == 1000
    assert len(kwargs["messages"]) == 1


def test_build_request_kwargs_never_sends_sampling_params(provider):
    """API version 2026-10-21 rejects temperature/top_p/top_k, even at defaults."""
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings(temperature=0.2, top_p=0.9)

    kwargs = provider._build_request_kwargs(messages, None, None, None, settings)

    assert {"temperature", "top_p", "top_k"}.isdisjoint(kwargs)


def test_build_request_kwargs_with_system_prompt(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings()

    kwargs = provider._build_request_kwargs(
        messages, "system prompt", None, None, settings
    )

    assert kwargs["system"] == "system prompt"


def test_build_request_kwargs_caches_system_prompt(provider):
    messages = [Message(role=MessageRole.USER, content="test")]

    kwargs = provider._build_request_kwargs(
        messages, "system prompt", None, None, LLMSettings(cache_system_prompt=True)
    )

    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "system prompt",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_build_request_kwargs_with_tools(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    tools = [
        ToolDefinition(
            name="test",
            description="test",
            input_schema={},
            function=lambda x: "result",
        )
    ]
    settings = LLMSettings()

    kwargs = provider._build_request_kwargs(messages, None, tools, None, settings)

    assert "tools" in kwargs
    assert len(kwargs["tools"]) == 1


@pytest.mark.parametrize(
    "model, enable_thinking, expected_thinking, expected_output_config",
    [
        ("claude-sonnet-5-5", True, {"type": "adaptive", "display": "summarized"}, None),
        ("claude-sonnet-5-5", False, None, {"effort": "low"}),
        ("claude-haiku-5-5", False, None, {"effort": "low"}),
        ("claude-opus-4-6", True, {"type": "adaptive", "display": "summarized"}, None),
        # Legacy models 400 on both adaptive thinking and effort
        ("claude-haiku-4-5", True, None, None),
        ("claude-haiku-4-5", False, None, None),
        ("claude-sonnet-4-5-20250929", False, None, None),
        ("claude-opus-4-1", True, None, None),
        ("claude-3-haiku-20240307", False, None, None),
    ],
)
def test_build_request_kwargs_thinking(
    model, enable_thinking, expected_thinking, expected_output_config
):
    provider = AnthropicProvider(
        api_key="test-key", model=model, enable_thinking=enable_thinking
    )
    messages = [Message(role=MessageRole.USER, content="test")]

    kwargs = provider._build_request_kwargs(messages, None, None, None, LLMSettings())

    assert kwargs.get("thinking") == expected_thinking
    assert kwargs.get("output_config") == expected_output_config


SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "string"}},
    "required": ["x"],
    "additionalProperties": False,
}
FORMAT = {"type": "json_schema", "schema": SCHEMA}


@pytest.mark.parametrize(
    "model, enable_thinking, expected_output_config",
    [
        ("claude-haiku-5-5", False, {"effort": "low", "format": FORMAT}),
        ("claude-sonnet-5-5", True, {"format": FORMAT}),
        # Legacy models reject effort but support structured outputs
        ("claude-haiku-4-5", False, {"format": FORMAT}),
    ],
)
def test_build_request_kwargs_json_schema(model, enable_thinking, expected_output_config):
    provider = AnthropicProvider(
        api_key="test-key", model=model, enable_thinking=enable_thinking
    )
    messages = [Message(role=MessageRole.USER, content="test")]

    kwargs = provider._build_request_kwargs(
        messages, None, None, None, LLMSettings(json_schema=SCHEMA)
    )

    assert kwargs["output_config"] == expected_output_config


@pytest.mark.parametrize(
    "schema",
    [
        SUMMARY_SCHEMA,
        SCORE_SCHEMA,
        EXTRACTION_SCHEMA,
        _build_schema(["forum", "book"]),
        _build_schema([]),
    ],
)
def test_json_schemas_are_already_strict(schema):
    # The SDK's strict-mode normaliser leaves a schema the API accepts untouched
    assert transform_schema(schema) == schema


@pytest.mark.parametrize(
    "server, expected_toolset",
    [
        (
            MCPServer(name="srv", url="https://mcp.example", token="tok"),
            {"type": "mcp_toolset", "mcp_server_name": "srv"},
        ),
        (
            MCPServer(
                name="srv",
                url="https://mcp.example",
                token="tok",
                allowed_tools=["a", "b"],
            ),
            {
                "type": "mcp_toolset",
                "mcp_server_name": "srv",
                "default_config": {"enabled": False},
                "configs": {"a": {"enabled": True}, "b": {"enabled": True}},
            },
        ),
        (
            MCPServer(
                name="srv",
                url="https://mcp.example",
                token="tok",
                disabled_tools=["c"],
            ),
            {
                "type": "mcp_toolset",
                "mcp_server_name": "srv",
                "configs": {"c": {"enabled": False}},
            },
        ),
        (
            MCPServer(
                name="srv",
                url="https://mcp.example",
                token="tok",
                allowed_tools=["a", "c"],
                disabled_tools=["c"],
            ),
            {
                "type": "mcp_toolset",
                "mcp_server_name": "srv",
                "default_config": {"enabled": False},
                "configs": {"a": {"enabled": True}, "c": {"enabled": False}},
            },
        ),
    ],
)
def test_build_request_kwargs_mcp_servers(provider, server, expected_toolset):
    messages = [Message(role=MessageRole.USER, content="test")]

    kwargs = provider._build_request_kwargs(
        messages, None, None, [server], LLMSettings()
    )

    assert kwargs["extra_headers"] == {"anthropic-beta": "mcp-client-2025-11-20"}
    assert kwargs["extra_body"] == {
        "mcp_servers": [
            {
                "type": "url",
                "url": "https://mcp.example",
                "name": "srv",
                "authorization_token": "tok",
            }
        ]
    }
    assert kwargs["tools"] == [expected_toolset]


def test_build_request_kwargs_without_mcp_sends_no_beta(provider):
    messages = [Message(role=MessageRole.USER, content="test")]

    kwargs = provider._build_request_kwargs(messages, None, None, None, LLMSettings())

    assert "extra_headers" not in kwargs
    assert "extra_body" not in kwargs


def test_handle_stream_event_text_delta(provider):
    event = Mock(
        type="content_block_delta",
        delta=Mock(type="text_delta", text="hello"),
    )

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is not None
    assert stream_event.type == "text"
    assert stream_event.data == "hello"
    assert tool_use is None


def test_handle_stream_event_thinking_delta(provider):
    event = Mock(
        type="content_block_delta",
        delta=Mock(type="thinking_delta", thinking="reasoning..."),
    )

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is not None
    assert stream_event.type == "thinking"
    assert stream_event.data == "reasoning..."


def test_handle_stream_event_tool_use_start(provider):
    block = Mock(spec=["type", "id", "name", "input"])
    block.type = "tool_use"
    block.id = "tool-1"
    block.name = "test_tool"
    block.input = {}

    event = Mock(spec=["type", "content_block"])
    event.type = "content_block_start"
    event.content_block = block

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is None
    assert tool_use is not None
    assert tool_use["id"] == "tool-1"
    assert tool_use["name"] == "test_tool"
    assert tool_use["input"] == {}


def test_handle_stream_event_tool_input_delta(provider):
    current_tool = {"id": "tool-1", "name": "test", "input": '{"ke'}
    event = Mock(
        type="content_block_delta",
        delta=Mock(type="input_json_delta", partial_json='y": "val'),
    )

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert stream_event is None
    assert tool_use["input"] == '{"key": "val'


def test_handle_stream_event_tool_use_complete(provider):
    current_tool = {
        "id": "tool-1",
        "name": "test_tool",
        "input": '{"key": "value"}',
    }
    event = Mock(
        type="content_block_stop",
        content_block=Mock(input={"key": "value"}),
    )

    stream_event, tool_use = provider._handle_stream_event(event, current_tool)

    assert stream_event is not None
    assert stream_event.type == "tool_use"
    assert stream_event.data["id"] == "tool-1"
    assert stream_event.data["name"] == "test_tool"
    assert stream_event.data["input"] == {"key": "value"}
    assert tool_use is None


def test_handle_stream_event_message_stop(provider):
    event = Mock(type="message_stop")

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is not None
    assert stream_event.type == "done"
    assert tool_use is None


def test_handle_stream_event_error(provider):
    event = Mock(type="error", error="API error")

    stream_event, tool_use = provider._handle_stream_event(event, None)

    assert stream_event is not None
    assert stream_event.type == "error"
    assert "API error" in stream_event.data


def test_generate_basic(provider, mock_anthropic_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    # Mock the response properly
    mock_block = Mock(spec=["type", "text"])
    mock_block.type = "text"
    mock_block.text = "test summary"

    mock_response = Mock(spec=["content"])
    mock_response.content = [mock_block]

    provider.client.messages.create.return_value = mock_response

    result = provider.generate(messages)

    assert result == "test summary"
    provider.client.messages.create.assert_called_once()


def test_stream_basic(provider, mock_anthropic_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    events = list(provider.stream(messages))

    # Should get text event and done event
    assert len(events) > 0
    assert any(e.type == "text" for e in events)
    provider.client.messages.stream.assert_called_once()


@pytest.mark.asyncio
async def test_agenerate_basic(provider, mock_anthropic_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    result = await provider.agenerate(messages)

    assert "test summary" in result
    provider.async_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_astream_basic(provider, mock_anthropic_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    events = []
    async for event in provider.astream(messages):
        events.append(event)

    assert len(events) > 0
    assert any(e.type == "text" for e in events)


def test_convert_message_sorts_thinking_content(provider):
    """Thinking content should be sorted so non-thinking comes before thinking."""
    message = Message.assistant(
        ThinkingContent(thinking="reasoning", signature="sig"),
        TextContent(text="response"),
    )

    result = provider._convert_message(message)

    assert result["role"] == "assistant"
    # The sort key (x["type"] != "thinking") sorts thinking type to beginning
    # because "thinking" != "thinking" is False, which sorts before True
    content_types = [c["type"] for c in result["content"]]
    assert "text" in content_types
    assert "thinking" in content_types
    # Verify thinking comes before non-thinking (sorted by key)
    thinking_idx = content_types.index("thinking")
    text_idx = content_types.index("text")
    assert thinking_idx < text_idx


def test_execute_tool_success(provider):
    tool_call = {"id": "t1", "name": "test", "input": {"arg": "value"}}
    tools = {
        "test": ToolDefinition(
            name="test",
            description="test",
            input_schema={},
            function=cast(Any, lambda x: f"result: {x['arg']}"),
        )
    }

    result = provider.execute_tool(tool_call, tools)

    assert result.tool_use_id == "t1"
    assert result.content == "result: value"
    assert result.is_error is False


def test_execute_tool_missing_name(provider):
    tool_call = {"id": "t1", "input": {}}
    tools = {}

    result = provider.execute_tool(tool_call, tools)

    assert result.tool_use_id == "t1"
    assert "missing" in result.content.lower()
    assert result.is_error is True


def test_execute_tool_not_found(provider):
    tool_call = {"id": "t1", "name": "nonexistent", "input": {}}
    tools = {}

    result = provider.execute_tool(tool_call, tools)

    assert result.tool_use_id == "t1"
    assert "not found" in result.content.lower()
    assert result.is_error is True


def test_execute_tool_exception(provider):
    tool_call = {"id": "t1", "name": "test", "input": {}}
    tools = {
        "test": ToolDefinition(
            name="test",
            description="test",
            input_schema={},
            function=cast(Any, lambda x: 1 / 0),  # Raises ZeroDivisionError
        )
    }

    result = provider.execute_tool(tool_call, tools)

    assert result.tool_use_id == "t1"
    assert result.is_error is True
    assert "division" in result.content.lower()


def test_encode_image(provider):
    image = Image.new("RGB", (10, 10), color="blue")

    encoded = provider.encode_image(image)

    assert isinstance(encoded, str)
    assert len(encoded) > 0


def test_encode_image_rgba(provider):
    """RGBA images should be converted to RGB."""
    image = Image.new("RGBA", (10, 10), color=(255, 0, 0, 128))

    encoded = provider.encode_image(image)

    assert isinstance(encoded, str)
    assert len(encoded) > 0
