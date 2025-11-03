import pytest
from unittest.mock import Mock, AsyncMock
from PIL import Image

from memory.common.llms.openai_provider import OpenAIProvider
from memory.common.llms.base import (
    Message,
    MessageRole,
    TextContent,
    ImageContent,
    ToolUseContent,
    ToolResultContent,
    LLMSettings,
    StreamEvent,
)
from memory.common.llms.tools import ToolDefinition


@pytest.fixture
def provider():
    return OpenAIProvider(api_key="test-key", model="gpt-4o")


@pytest.fixture
def reasoning_provider():
    return OpenAIProvider(api_key="test-key", model="o1-preview")


def test_initialization(provider):
    assert provider.api_key == "test-key"
    assert provider.model == "gpt-4o"


def test_client_lazy_loading(provider):
    assert provider._client is None
    client = provider.client
    assert client is not None
    assert provider._client is not None


def test_async_client_lazy_loading(provider):
    assert provider._async_client is None
    client = provider.async_client
    assert client is not None
    assert provider._async_client is not None


@pytest.mark.parametrize(
    "model, expected",
    [
        ("gpt-4o", False),
        ("o1-preview", True),
        ("o1-mini", True),
        ("gpt-4-turbo", True),
        ("gpt-3.5-turbo", True),
    ],
)
def test_is_reasoning_model(model, expected):
    provider = OpenAIProvider(api_key="test-key", model=model)
    assert provider._is_reasoning_model() == expected


def test_convert_text_content(provider):
    content = TextContent(text="hello world")
    result = provider._convert_text_content(content)
    assert result == {"type": "text", "text": "hello world"}


def test_convert_image_content(provider):
    image = Image.new("RGB", (100, 100), color="red")
    content = ImageContent(image=image)
    result = provider._convert_image_content(content)

    assert result["type"] == "image_url"
    assert "image_url" in result
    assert result["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_convert_image_content_with_detail(provider):
    image = Image.new("RGB", (100, 100), color="red")
    content = ImageContent(image=image, detail="high")
    result = provider._convert_image_content(content)

    assert result["image_url"]["detail"] == "high"


def test_convert_tool_use_content(provider):
    content = ToolUseContent(
        id="t1",
        name="test_tool",
        input={"arg": "value"},
    )
    result = provider._convert_tool_use_content(content)

    assert result["id"] == "t1"
    assert result["type"] == "function"
    assert result["function"]["name"] == "test_tool"
    assert '{"arg": "value"}' in result["function"]["arguments"]


def test_convert_tool_result_content(provider):
    content = ToolResultContent(
        tool_use_id="t1",
        content="result content",
        is_error=False,
    )
    result = provider._convert_tool_result_content(content)

    assert result["role"] == "tool"
    assert result["tool_call_id"] == "t1"
    assert result["content"] == "result content"


def test_convert_messages_simple(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    result = provider._convert_messages(messages)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "test"


def test_convert_messages_with_tool_result(provider):
    """Tool results should become separate messages with 'tool' role."""
    messages = [
        Message(
            role=MessageRole.USER,
            content=[ToolResultContent(tool_use_id="t1", content="result")],
        )
    ]
    result = provider._convert_messages(messages)

    assert len(result) == 1
    assert result[0]["role"] == "tool"
    assert result[0]["tool_call_id"] == "t1"


def test_convert_messages_with_tool_use(provider):
    """Tool use content should become tool_calls field."""
    messages = [
        Message.assistant(
            TextContent(text="thinking..."),
            ToolUseContent(id="t1", name="test", input={}),
        )
    ]
    result = provider._convert_messages(messages)

    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert "tool_calls" in result[0]
    assert len(result[0]["tool_calls"]) == 1


def test_convert_messages_mixed_content(provider):
    """Messages with both text and tool results should be split."""
    messages = [
        Message(
            role=MessageRole.USER,
            content=[
                TextContent(text="user text"),
                ToolResultContent(tool_use_id="t1", content="result"),
            ],
        )
    ]
    result = provider._convert_messages(messages)

    # Should create two messages: one user message and one tool message
    assert len(result) == 2
    assert result[0]["role"] == "tool"
    assert result[1]["role"] == "user"


def test_convert_tools(provider):
    tools = [
        ToolDefinition(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {"arg": {"type": "string"}}},
            function=lambda x: "result",
        )
    ]
    result = provider._convert_tools(tools)

    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "test_tool"
    assert result[0]["function"]["description"] == "A test tool"
    assert result[0]["function"]["parameters"] == tools[0].input_schema


def test_build_request_kwargs_basic(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings(temperature=0.5, max_tokens=1000)

    kwargs = provider._build_request_kwargs(messages, None, None, None, settings)

    assert kwargs["model"] == "gpt-4o"
    assert kwargs["temperature"] == 0.5
    assert kwargs["max_tokens"] == 1000
    assert len(kwargs["messages"]) == 1


def test_build_request_kwargs_with_system_prompt_standard_model(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings()

    kwargs = provider._build_request_kwargs(
        messages, "system prompt", None, None, settings
    )

    # For gpt-4o, system prompt becomes system message
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][0]["content"] == "system prompt"


def test_build_request_kwargs_with_system_prompt_reasoning_model(
    reasoning_provider,
):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings()

    kwargs = reasoning_provider._build_request_kwargs(
        messages, "system prompt", None, None, settings
    )

    # For o1 models, system prompt becomes developer message
    assert kwargs["messages"][0]["role"] == "developer"
    assert kwargs["messages"][0]["content"] == "system prompt"


def test_build_request_kwargs_reasoning_model_uses_max_completion_tokens(
    reasoning_provider,
):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings(max_tokens=2000)

    kwargs = reasoning_provider._build_request_kwargs(
        messages, None, None, None, settings
    )

    # Reasoning models use max_completion_tokens
    assert "max_completion_tokens" in kwargs
    assert kwargs["max_completion_tokens"] == 2000
    assert "max_tokens" not in kwargs


def test_build_request_kwargs_reasoning_model_no_temperature(reasoning_provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings(temperature=0.7)

    kwargs = reasoning_provider._build_request_kwargs(
        messages, None, None, None, settings
    )

    # Reasoning models don't support temperature
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


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
    assert kwargs["tool_choice"] == "auto"


def test_build_request_kwargs_with_stream(provider):
    messages = [Message(role=MessageRole.USER, content="test")]
    settings = LLMSettings()

    kwargs = provider._build_request_kwargs(
        messages, None, None, None, settings, stream=True
    )

    assert kwargs["stream"] is True


def test_parse_and_finalize_tool_call(provider):
    tool_call = {
        "id": "t1",
        "name": "test",
        "arguments": '{"key": "value"}',
    }

    result = provider._parse_and_finalize_tool_call(tool_call)

    assert result["id"] == "t1"
    assert result["name"] == "test"
    assert result["input"] == {"key": "value"}
    assert "arguments" not in result


def test_parse_and_finalize_tool_call_invalid_json(provider):
    tool_call = {
        "id": "t1",
        "name": "test",
        "arguments": '{"invalid json',
    }

    result = provider._parse_and_finalize_tool_call(tool_call)

    # Should default to empty dict on parse error
    assert result["input"] == {}


def test_handle_stream_chunk_text_content(provider):
    chunk = Mock(
        choices=[
            Mock(
                delta=Mock(content="hello", tool_calls=None),
                finish_reason=None,
            )
        ],
        usage=Mock(prompt_tokens=10, completion_tokens=5),
    )

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 1
    assert events[0].type == "text"
    assert events[0].data == "hello"
    assert tool_call is None


def test_handle_stream_chunk_tool_call_start(provider):
    function = Mock(spec=["name", "arguments"])
    function.name = "test_tool"
    function.arguments = ""

    tool_call_mock = Mock(spec=["id", "function"])
    tool_call_mock.id = "t1"
    tool_call_mock.function = function

    delta = Mock(spec=["content", "tool_calls"])
    delta.content = None
    delta.tool_calls = [tool_call_mock]

    choice = Mock(spec=["delta", "finish_reason"])
    choice.delta = delta
    choice.finish_reason = None

    chunk = Mock(spec=["choices", "usage"])
    chunk.choices = [choice]
    chunk.usage = Mock(prompt_tokens=10, completion_tokens=5)

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 0
    assert tool_call is not None
    assert tool_call["id"] == "t1"
    assert tool_call["name"] == "test_tool"


def test_handle_stream_chunk_tool_call_arguments(provider):
    current_tool = {"id": "t1", "name": "test", "arguments": '{"ke'}
    chunk = Mock(
        choices=[
            Mock(
                delta=Mock(
                    content=None,
                    tool_calls=[
                        Mock(
                            id=None,
                            function=Mock(name=None, arguments='y": "val"}'),
                        )
                    ],
                ),
                finish_reason=None,
            )
        ],
        usage=Mock(prompt_tokens=10, completion_tokens=5),
    )

    events, tool_call = provider._handle_stream_chunk(chunk, current_tool)

    assert len(events) == 0
    assert tool_call["arguments"] == '{"key": "val"}'


def test_handle_stream_chunk_finish_with_tool_call(provider):
    current_tool = {"id": "t1", "name": "test", "arguments": '{"key": "value"}'}
    chunk = Mock(
        choices=[
            Mock(
                delta=Mock(content=None, tool_calls=None),
                finish_reason="tool_calls",
            )
        ],
        usage=Mock(prompt_tokens=10, completion_tokens=5),
    )

    events, tool_call = provider._handle_stream_chunk(chunk, current_tool)

    assert len(events) == 1
    assert events[0].type == "tool_use"
    assert events[0].data["id"] == "t1"
    assert events[0].data["input"] == {"key": "value"}
    assert tool_call is None


def test_handle_stream_chunk_empty_choices(provider):
    chunk = Mock(choices=[], usage=Mock(prompt_tokens=10, completion_tokens=5))

    events, tool_call = provider._handle_stream_chunk(chunk, None)

    assert len(events) == 0
    assert tool_call is None


def test_generate_basic(provider, mock_openai_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    # The conftest fixture already sets up the mock response
    result = provider.generate(messages)

    assert isinstance(result, str)
    assert len(result) > 0
    provider.client.chat.completions.create.assert_called_once()


def test_stream_basic(provider, mock_openai_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    events = list(provider.stream(messages))

    # Should get text events and done event
    assert len(events) > 0
    text_events = [e for e in events if e.type == "text"]
    assert len(text_events) > 0
    assert events[-1].type == "done"


@pytest.mark.asyncio
async def test_agenerate_basic(provider, mock_openai_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    # Mock the async client
    mock_response = Mock(
        choices=[Mock(message=Mock(content="async response"))],
        usage=Mock(prompt_tokens=10, completion_tokens=20),
    )
    provider.async_client.chat.completions.create = AsyncMock(
        return_value=mock_response
    )

    result = await provider.agenerate(messages)

    assert result == "async response"


@pytest.mark.asyncio
async def test_astream_basic(provider, mock_openai_client):
    messages = [Message(role=MessageRole.USER, content="test")]

    # Mock async streaming
    async def async_stream():
        yield Mock(
            choices=[
                Mock(delta=Mock(content="async", tool_calls=None), finish_reason=None)
            ],
            usage=Mock(prompt_tokens=10, completion_tokens=5),
        )
        yield Mock(
            choices=[
                Mock(delta=Mock(content=" test", tool_calls=None), finish_reason="stop")
            ],
            usage=Mock(prompt_tokens=10, completion_tokens=10),
        )

    provider.async_client.chat.completions.create = AsyncMock(
        return_value=async_stream()
    )

    events = []
    async for event in provider.astream(messages):
        events.append(event)

    assert len(events) > 0
    text_events = [e for e in events if e.type == "text"]
    assert len(text_events) > 0


def test_stream_with_tool_call(provider, mock_openai_client):
    """Test streaming with a complete tool call."""

    def stream_with_tool(*args, **kwargs):
        if kwargs.get("stream"):
            # First chunk - tool call start
            function1 = Mock(spec=["name", "arguments"])
            function1.name = "test_tool"
            function1.arguments = ""

            tool_call1 = Mock(spec=["id", "function"])
            tool_call1.id = "t1"
            tool_call1.function = function1

            delta1 = Mock(spec=["content", "tool_calls"])
            delta1.content = None
            delta1.tool_calls = [tool_call1]

            choice1 = Mock(spec=["delta", "finish_reason"])
            choice1.delta = delta1
            choice1.finish_reason = None

            chunk1 = Mock(spec=["choices"])
            chunk1.choices = [choice1]

            # Second chunk - tool arguments
            function2 = Mock(spec=["name", "arguments"])
            function2.name = None
            function2.arguments = '{"arg": "val"}'

            tool_call2 = Mock(spec=["id", "function"])
            tool_call2.id = None
            tool_call2.function = function2

            delta2 = Mock(spec=["content", "tool_calls"])
            delta2.content = None
            delta2.tool_calls = [tool_call2]

            choice2 = Mock(spec=["delta", "finish_reason"])
            choice2.delta = delta2
            choice2.finish_reason = None

            chunk2 = Mock(spec=["choices"])
            chunk2.choices = [choice2]

            # Third chunk - finish
            delta3 = Mock(spec=["content", "tool_calls"])
            delta3.content = None
            delta3.tool_calls = None

            choice3 = Mock(spec=["delta", "finish_reason"])
            choice3.delta = delta3
            choice3.finish_reason = "tool_calls"

            chunk3 = Mock(spec=["choices"])
            chunk3.choices = [choice3]

            return iter([chunk1, chunk2, chunk3])

    provider.client.chat.completions.create.side_effect = stream_with_tool

    messages = [Message(role=MessageRole.USER, content="test")]
    events = list(provider.stream(messages))

    tool_events = [e for e in events if e.type == "tool_use"]
    assert len(tool_events) == 1
    assert tool_events[0].data["id"] == "t1"
    assert tool_events[0].data["name"] == "test_tool"
    assert tool_events[0].data["input"] == {"arg": "val"}


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
