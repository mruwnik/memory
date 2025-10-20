import pytest
from PIL import Image

from memory.common.llms.base import (
    Message,
    MessageRole,
    TextContent,
    ImageContent,
    ToolUseContent,
    ToolResultContent,
    ThinkingContent,
    LLMSettings,
    StreamEvent,
    create_provider,
)
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.openai_provider import OpenAIProvider
from memory.common import settings


def test_message_role_enum():
    assert MessageRole.SYSTEM == "system"
    assert MessageRole.USER == "user"
    assert MessageRole.ASSISTANT == "assistant"
    assert MessageRole.TOOL == "tool"


def test_text_content_creation():
    content = TextContent(text="hello")
    assert content.type == "text"
    assert content.text == "hello"
    assert content.valid


def test_text_content_to_dict():
    content = TextContent(text="hello")
    result = content.to_dict()
    assert result == {"type": "text", "text": "hello"}


def test_text_content_empty_invalid():
    content = TextContent(text="")
    assert not content.valid


def test_image_content_creation():
    image = Image.new("RGB", (10, 10))
    content = ImageContent(image=image)
    assert content.type == "image"
    assert content.image == image
    assert content.valid


def test_image_content_with_detail():
    image = Image.new("RGB", (10, 10))
    content = ImageContent(image=image, detail="high")
    assert content.detail == "high"


def test_tool_use_content_creation():
    content = ToolUseContent(id="t1", name="test_tool", input={"arg": "value"})
    assert content.type == "tool_use"
    assert content.id == "t1"
    assert content.name == "test_tool"
    assert content.input == {"arg": "value"}
    assert content.valid


def test_tool_use_content_to_dict():
    content = ToolUseContent(id="t1", name="test", input={"key": "val"})
    result = content.to_dict()
    assert result == {
        "type": "tool_use",
        "id": "t1",
        "name": "test",
        "input": {"key": "val"},
    }


def test_tool_result_content_creation():
    content = ToolResultContent(
        tool_use_id="t1",
        content="result",
        is_error=False,
    )
    assert content.type == "tool_result"
    assert content.tool_use_id == "t1"
    assert content.content == "result"
    assert not content.is_error
    assert content.valid


def test_tool_result_content_with_error():
    content = ToolResultContent(
        tool_use_id="t1",
        content="error message",
        is_error=True,
    )
    assert content.is_error


def test_thinking_content_creation():
    content = ThinkingContent(thinking="reasoning...", signature="sig")
    assert content.type == "thinking"
    assert content.thinking == "reasoning..."
    assert content.signature == "sig"
    assert content.valid


def test_thinking_content_invalid_without_signature():
    content = ThinkingContent(thinking="reasoning...")
    assert not content.valid


def test_message_simple_string_content():
    msg = Message(role=MessageRole.USER, content="hello")
    assert msg.role == MessageRole.USER
    assert msg.content == "hello"


def test_message_list_content():
    content_list = [TextContent(text="hello"), TextContent(text="world")]
    msg = Message(role=MessageRole.USER, content=content_list)
    assert msg.role == MessageRole.USER
    assert len(msg.content) == 2


def test_message_to_dict_string():
    msg = Message(role=MessageRole.USER, content="hello")
    result = msg.to_dict()
    assert result == {"role": "user", "content": "hello"}


def test_message_to_dict_list():
    msg = Message(
        role=MessageRole.USER,
        content=[TextContent(text="hello"), TextContent(text="world")],
    )
    result = msg.to_dict()
    assert result["role"] == "user"
    assert len(result["content"]) == 2
    assert result["content"][0] == {"type": "text", "text": "hello"}


def test_message_assistant_factory():
    msg = Message.assistant(
        TextContent(text="response"),
        ToolUseContent(id="t1", name="tool", input={}),
    )
    assert msg.role == MessageRole.ASSISTANT
    assert len(msg.content) == 2


def test_message_assistant_filters_invalid_content():
    msg = Message.assistant(
        TextContent(text="valid"),
        TextContent(text=""),  # Invalid - empty
    )
    assert len(msg.content) == 1
    assert msg.content[0].text == "valid"


def test_message_user_factory():
    msg = Message.user(text="hello")
    assert msg.role == MessageRole.USER
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextContent)


def test_message_user_with_tool_result():
    tool_result = ToolResultContent(tool_use_id="t1", content="result")
    msg = Message.user(text="hello", tool_result=tool_result)
    assert len(msg.content) == 2


def test_stream_event_creation():
    event = StreamEvent(type="text", data="hello")
    assert event.type == "text"
    assert event.data == "hello"


def test_stream_event_with_signature():
    event = StreamEvent(type="thinking", signature="sig123")
    assert event.signature == "sig123"


def test_llm_settings_defaults():
    settings = LLMSettings()
    assert settings.temperature == 0.7
    assert settings.max_tokens == 2048
    assert settings.top_p is None
    assert settings.stop_sequences is None
    assert settings.stream is False


def test_llm_settings_custom():
    settings = LLMSettings(
        temperature=0.5,
        max_tokens=1000,
        top_p=0.9,
        stop_sequences=["STOP"],
        stream=True,
    )
    assert settings.temperature == 0.5
    assert settings.max_tokens == 1000
    assert settings.top_p == 0.9
    assert settings.stop_sequences == ["STOP"]
    assert settings.stream is True


def test_create_provider_anthropic():
    provider = create_provider(
        model="anthropic/claude-3-opus-20240229",
        api_key="test-key",
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-3-opus-20240229"


def test_create_provider_openai():
    provider = create_provider(
        model="openai/gpt-4o",
        api_key="test-key",
    )
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o"


def test_create_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider(model="unknown/model", api_key="test-key")


def test_create_provider_uses_default_model():
    """If no model provided, should use SUMMARIZER_MODEL from settings."""
    provider = create_provider(api_key="test-key")
    # Should create a provider (type depends on settings.SUMMARIZER_MODEL)
    assert provider is not None


def test_create_provider_anthropic_with_thinking():
    provider = create_provider(
        model="anthropic/claude-opus-4",
        api_key="test-key",
        enable_thinking=True,
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.enable_thinking is True


def test_create_provider_missing_anthropic_key():
    # Temporarily clear the API key from settings
    original_key = settings.ANTHROPIC_API_KEY
    try:
        settings.ANTHROPIC_API_KEY = ""
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            create_provider(model="anthropic/claude-3-opus-20240229")
    finally:
        settings.ANTHROPIC_API_KEY = original_key


def test_create_provider_missing_openai_key():
    # Temporarily clear the API key from settings
    original_key = settings.OPENAI_API_KEY
    try:
        settings.OPENAI_API_KEY = ""
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_provider(model="openai/gpt-4o")
    finally:
        settings.OPENAI_API_KEY = original_key
