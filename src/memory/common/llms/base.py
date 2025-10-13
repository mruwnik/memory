"""Base classes and types for LLM providers."""

import base64
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Iterator, Literal, Optional, Union

from PIL import Image

from memory.common import settings
from memory.common.llms.tools import ToolCall, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class MessageRole(str, Enum):
    """Message roles for chat history."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class TextContent:
    """Text content in a message."""

    type: Literal["text"] = "text"
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {"type": "text", "text": self.text}


@dataclass
class ImageContent:
    """Image content in a message."""

    type: Literal["image"] = "image"
    image: Image.Image = None  # type: ignore
    detail: Optional[str] = None  # For OpenAI: "low", "high", "auto"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        # Note: Image will be encoded by provider-specific implementation
        return {"type": "image", "image": self.image}


@dataclass
class ToolUseContent:
    """Tool use request from the assistant."""

    type: Literal["tool_use"] = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = None  # type: ignore

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }


@dataclass
class ToolResultContent:
    """Tool result from tool execution."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass
class ThinkingContent:
    """Thinking/reasoning content from the assistant (extended thinking)."""

    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "type": "thinking",
            "thinking": self.thinking,
            "signature": self.signature,
        }


MessageContent = Union[
    TextContent, ImageContent, ToolUseContent, ToolResultContent, ThinkingContent
]


@dataclass
class Turn:
    """A turn in the conversation."""

    response: str | None
    thinking: str | None
    tool_calls: dict[str, ToolResult] | None


@dataclass
class Message:
    """A message in the conversation history."""

    role: MessageRole
    content: Union[str, list[MessageContent]]

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary format."""
        if isinstance(self.content, str):
            return {"role": self.role.value, "content": self.content}
        content_list = [item.to_dict() for item in self.content]
        return {"role": self.role.value, "content": content_list}

    @staticmethod
    def assistant(
        text: TextContent | None = None,
        thinking: ThinkingContent | None = None,
        tool_use: ToolUseContent | None = None,
    ) -> "Message":
        parts = []
        if text:
            parts.append(text)
        if thinking:
            parts.append(thinking)
        if tool_use:
            parts.append(tool_use)
        return Message(role=MessageRole.ASSISTANT, content=parts)

    @staticmethod
    def user(
        text: str | None = None, tool_result: ToolResultContent | None = None
    ) -> "Message":
        parts = []
        if text:
            parts.append(TextContent(text=text))
        if tool_result:
            parts.append(tool_result)
        return Message(role=MessageRole.USER, content=parts)


@dataclass
class StreamEvent:
    """An event from the streaming response."""

    type: Literal["text", "tool_use", "tool_result", "thinking", "error", "done"]
    data: Any = None
    signature: str | None = None


@dataclass
class LLMSettings:
    """Settings for LLM API calls."""

    temperature: float = 0.7
    max_tokens: int = 2048
    # Don't set by default - some models don't allow both temp and top_p
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    stream: bool = False


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    def __init__(self, api_key: str, model: str):
        """
        Initialize the LLM provider.

        Args:
            api_key: API key for the provider
            model: Model identifier
        """
        self.api_key = api_key
        self.model = model
        self._client: Any = None

    @abstractmethod
    def _initialize_client(self) -> Any:
        """Initialize the provider-specific client."""
        pass

    @property
    def client(self) -> Any:
        """Lazy-load the client."""
        if self._client is None:
            self._client = self._initialize_client()
        return self._client

    def execute_tool(
        self,
        tool_call: ToolCall,
        tool_handlers: dict[str, ToolDefinition],
    ) -> ToolResultContent:
        """
        Execute a tool call.

        Args:
            tool_call: Tool call
            tool_handlers: Dict mapping tool names to handler functions

        Returns:
            ToolResultContent with result or error
        """
        name = tool_call.get("name")
        tool_use_id = tool_call.get("id")
        input = tool_call.get("input")

        if not name:
            return ToolResultContent(
                tool_use_id=tool_use_id,
                content="Tool name missing",
                is_error=True,
            )

        if not (tool := tool_handlers.get(name)):
            return ToolResultContent(
                tool_use_id=tool_use_id,
                content=f"Tool '{name}' not found",
                is_error=True,
            )

        try:
            return ToolResultContent(
                tool_use_id=tool_use_id,
                content=tool(input),
                is_error=False,
            )
        except Exception as e:
            logger.error(f"Tool '{name}' failed: {e}", exc_info=True)
            return ToolResultContent(
                tool_use_id=tool_use_id,
                content=str(e),
                is_error=True,
            )

    def encode_image(self, image: Image.Image) -> str:
        """
        Encode PIL Image to base64 string.

        Args:
            image: PIL Image to encode

        Returns:
            Base64 encoded string
        """
        buffer = io.BytesIO()
        # Convert to RGB if necessary (for RGBA, etc.)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(buffer, format="JPEG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _convert_text_content(self, content: TextContent) -> dict[str, Any]:
        """Convert TextContent to provider format. Override for custom format."""
        return content.to_dict()

    def _convert_image_content(self, content: ImageContent) -> dict[str, Any]:
        """Convert ImageContent to provider format. Override for custom format."""
        return content.to_dict()

    def _convert_tool_use_content(self, content: ToolUseContent) -> dict[str, Any]:
        """Convert ToolUseContent to provider format. Override for custom format."""
        return content.to_dict()

    def _convert_tool_result_content(
        self, content: ToolResultContent
    ) -> dict[str, Any]:
        """Convert ToolResultContent to provider format. Override for custom format."""
        return content.to_dict()

    def _convert_thinking_content(self, content: ThinkingContent) -> dict[str, Any]:
        """Convert ThinkingContent to provider format. Override for custom format."""
        return content.to_dict()

    def _convert_message_content(self, content: MessageContent) -> dict[str, Any]:
        """
        Convert a MessageContent item to provider format.

        Dispatches to type-specific converters that can be overridden.
        """
        if isinstance(content, TextContent):
            return self._convert_text_content(content)
        elif isinstance(content, ImageContent):
            return self._convert_image_content(content)
        elif isinstance(content, ToolUseContent):
            return self._convert_tool_use_content(content)
        elif isinstance(content, ToolResultContent):
            return self._convert_tool_result_content(content)
        elif isinstance(content, ThinkingContent):
            return self._convert_thinking_content(content)
        else:
            raise ValueError(f"Unknown content type: {type(content)}")

    def _convert_message(self, message: Message) -> dict[str, Any]:
        """
        Convert a Message to provider format.

        Can be overridden for provider-specific handling (e.g., filtering system messages).
        """
        return message.to_dict()

    def _should_include_message(self, message: Message) -> bool:
        """
        Determine if a message should be included in the request.

        Override to filter messages (e.g., Anthropic filters SYSTEM messages).

        Args:
            message: Message to check

        Returns:
            True if message should be included
        """
        return True

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """
        Convert a list of messages to provider format.

        Uses _should_include_message for filtering and _convert_message for conversion.
        """
        return [
            self._convert_message(msg)
            for msg in messages
            if self._should_include_message(msg)
        ]

    def _convert_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        """
        Convert a single ToolDefinition to provider format.

        Default format matches Anthropic. Override for other providers (e.g., OpenAI uses functions).
        """
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    def _convert_tools(
        self, tools: list[ToolDefinition] | None
    ) -> Optional[list[dict[str, Any]]]:
        """Convert tool definitions to provider format."""
        if not tools:
            return None
        return [self._convert_tool(tool) for tool in tools]

    @abstractmethod
    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """
        Generate a non-streaming response.

        Args:
            messages: Conversation history
            system_prompt: Optional system prompt
            tools: Optional list of tools the LLM can use
            settings: Optional settings for the generation

        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> Iterator[StreamEvent]:
        """
        Generate a streaming response.

        Args:
            messages: Conversation history
            system_prompt: Optional system prompt
            tools: Optional list of tools the LLM can use
            settings: Optional settings for the generation

        Yields:
            StreamEvent objects containing text chunks, tool uses, or errors
        """
        pass

    @abstractmethod
    async def agenerate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """
        Generate a non-streaming response asynchronously.

        Args:
            messages: Conversation history
            system_prompt: Optional system prompt
            tools: Optional list of tools the LLM can use
            settings: Optional settings for the generation

        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    async def astream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        Generate a streaming response asynchronously.

        Args:
            messages: Conversation history
            system_prompt: Optional system prompt
            tools: Optional list of tools the LLM can use
            settings: Optional settings for the generation

        Yields:
            StreamEvent objects containing text chunks, tool uses, or errors
        """
        pass

    @abstractmethod
    def stream_with_tools(
        self,
        messages: list[Message],
        tools: dict[str, ToolDefinition],
        settings: LLMSettings | None = None,
        system_prompt: str | None = None,
        max_iterations: int = 10,
    ) -> Iterator[StreamEvent]:
        pass

    def run_with_tools(
        self,
        messages: list[Message],
        tools: dict[str, ToolDefinition],
        settings: LLMSettings | None = None,
        system_prompt: str | None = None,
        max_iterations: int = 10,
    ) -> Turn:
        thinking, response, tool_calls = "", "", {}
        for event in self.stream_with_tools(
            messages=messages,
            tools=tools,
            settings=settings,
            system_prompt=system_prompt,
            max_iterations=max_iterations,
        ):
            if event.type == "thinking":
                thinking += event.data
            elif event.type == "tool_use":
                tool_calls[event.data["id"]] = {
                    "name": event.data["name"],
                    "input": event.data["input"],
                    "output": "",
                }
            elif event.type == "text":
                response += event.data
            elif event.type == "tool_result":
                current = tool_calls.get(event.data["tool_use_id"]) or {}
                tool_calls[event.data["tool_use_id"]] = {
                    "name": event.data.get("name") or current.get("name"),
                    "input": event.data.get("input") or current.get("input"),
                    "output": event.data.get("content"),
                }
        return Turn(
            thinking=thinking or None,
            response=response or None,
            tool_calls=tool_calls or None,
        )


def create_provider(
    model: str | None = None,
    api_key: str | None = None,
    enable_thinking: bool = False,
) -> BaseLLMProvider:
    """
    Create an LLM provider based on the model name.

    Args:
        model: Model identifier (e.g., "claude-3-opus-20240229", "gpt-4").
               If not provided, uses SUMMARIZER_MODEL from settings.
        api_key: Optional API key. If not provided, uses keys from settings.
        enable_thinking: Enable extended thinking for supported models (Claude Opus 4+, Sonnet 4+, Sonnet 3.7)

    Returns:
        An initialized LLM provider

    Raises:
        ValueError: If the provider cannot be determined from the model name
    """
    # Use default model from settings if not provided
    if model is None:
        model = settings.SUMMARIZER_MODEL

    provider, model = model.split("/", 1)

    if provider == "anthropic":
        # Anthropic models
        if api_key is None:
            api_key = settings.ANTHROPIC_API_KEY

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found in settings. "
                "Please set it in your .env file."
            )

        from memory.common.llms.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=api_key, model=model, enable_thinking=enable_thinking
        )

    # Could add OpenAI support here in the future
    # elif "gpt" in model_lower or model.startswith("openai"):
    #     ...

    else:
        raise ValueError(
            f"Unknown provider for model: {model}. "
            f"Supported providers: Anthropic (claude-*)"
        )
