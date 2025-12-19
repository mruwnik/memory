"""Anthropic LLM provider implementation."""

import json
import logging
from urllib.parse import urlparse
from typing import Any, AsyncIterator, Iterator

import anthropic

from memory.common.llms.base import (
    BaseLLMProvider,
    ImageContent,
    MCPServer,
    LLMSettings,
    Message,
    MessageRole,
    StreamEvent,
    ToolDefinition,
    Usage,
)

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic LLM provider with streaming, tool support, and extended thinking."""

    provider = "anthropic"

    # Models that support extended thinking
    THINKING_MODELS = {
        "claude-opus-4",
        "claude-opus-4-1",
        "claude-sonnet-4-0",
        "claude-sonnet-3-7",
        "claude-sonnet-4-5",
    }

    def __init__(self, api_key: str, model: str, enable_thinking: bool = False):
        """
        Initialize the Anthropic provider.

        Args:
            api_key: Anthropic API key
            model: Model identifier
            enable_thinking: Enable extended thinking for supported models
        """
        super().__init__(api_key, model)
        self.enable_thinking = enable_thinking
        self._async_client: anthropic.AsyncAnthropic | None = None

    def _initialize_client(self) -> anthropic.Anthropic:
        """Initialize the Anthropic client."""
        return anthropic.Anthropic(api_key=self.api_key)

    @property
    def async_client(self) -> anthropic.AsyncAnthropic:
        """Lazy-load the async client."""
        if self._async_client is None:
            self._async_client = anthropic.AsyncAnthropic(api_key=self.api_key)
        return self._async_client

    def _convert_image_content(self, content: ImageContent) -> dict[str, Any]:
        """Convert ImageContent to Anthropic's base64 source format."""
        encoded_image = self.encode_image(content.image)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": encoded_image,
            },
        }

    def _convert_message(self, message: Message) -> dict[str, Any]:
        # Handle string content directly
        if isinstance(message.content, str):
            return {"role": message.role.value, "content": message.content}

        # Convert content items, handling ImageContent specially
        content_list = []
        for item in message.content:
            if isinstance(item, ImageContent):
                content_list.append(self._convert_image_content(item))
            else:
                content_list.append(item.to_dict())

        # Sort assistant messages to put thinking last
        if message.role == MessageRole.ASSISTANT:
            content_list = sorted(content_list, key=lambda x: x["type"] != "thinking")

        return {"role": message.role.value, "content": content_list}

    def _should_include_message(self, message: Message) -> bool:
        """Filter out system messages (handled separately in Anthropic)."""
        return message.role != MessageRole.SYSTEM

    def _supports_thinking(self) -> bool:
        """Check if the current model supports extended thinking."""
        model_lower = self.model.lower()
        return any(supported in model_lower for supported in self.THINKING_MODELS)

    def _build_request_kwargs(
        self,
        messages: list[Message],
        system_prompt: str | None,
        tools: list[ToolDefinition] | None,
        mcp_servers: list[MCPServer] | None,
        settings: LLMSettings,
    ) -> dict[str, Any]:
        """Build common request kwargs for API calls."""
        anthropic_messages = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "extra_headers": {
                "anthropic-beta": "web-fetch-2025-09-10,mcp-client-2025-04-04"
            },
        }

        # Only include top_p if explicitly set
        if settings.top_p is not None:
            kwargs["top_p"] = settings.top_p

        if system_prompt:
            kwargs["system"] = system_prompt

        if settings.stop_sequences:
            kwargs["stop_sequences"] = settings.stop_sequences

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        if mcp_servers:

            def format_server(server: MCPServer) -> dict[str, Any]:
                conf: dict[str, Any] = {
                    "type": "url",
                    "url": server.url,
                    "name": server.name,
                    "authorization_token": server.token,
                }
                if server.allowed_tools:
                    conf["tool_configuration"] = {
                        "allowed_tools": server.allowed_tools,
                    }
                return conf

            kwargs["extra_body"] = {
                "mcp_servers": [format_server(server) for server in mcp_servers]
            }

        # Enable extended thinking if requested and model supports it
        if self.enable_thinking and self._supports_thinking():
            thinking_budget = min(10000, settings.max_tokens - 1024)
            if thinking_budget >= 1024:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
                # When thinking is enabled: temperature must be 1, can't use top_p
                kwargs["temperature"] = 1.0
                kwargs.pop("top_p", None)

        return kwargs

    def _handle_stream_event(
        self, event: Any, current_tool_use: dict[str, Any] | None
    ) -> tuple[StreamEvent | None, dict[str, Any] | None]:
        """
        Handle a streaming event and return StreamEvent and updated tool state.

        Returns:
            Tuple of (StreamEvent or None, updated current_tool_use or None)
        """
        event_type = getattr(event, "type", None)
        # Handle error events
        if event_type == "error":
            error = getattr(event, "error", None)
            error_msg = str(error) if error else "Unknown error"
            return StreamEvent(type="error", data=error_msg), current_tool_use

        if event_type == "content_block_start":
            block = getattr(event, "content_block", None)
            if not block:
                return None, current_tool_use

            block_type = getattr(block, "type", None)

            # Handle various tool types (tool_use, mcp_tool_use, server_tool_use)
            if block_type in ("tool_use", "mcp_tool_use", "server_tool_use"):
                # In content_block_start, input may already be present (empty dict)
                block_input = getattr(block, "input", None)
                current_tool_use = {
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": block_input if block_input is not None else "",
                    "server_name": getattr(block, "server_name", None),
                    "is_server_call": block_type != "tool_use",
                }

            # Handle tool result blocks
            elif hasattr(block, "tool_use_id"):
                tool_result = {
                    "id": getattr(block, "tool_use_id", ""),
                    "result": getattr(block, "content", ""),
                }
                return StreamEvent(
                    type="tool_result", data=tool_result
                ), current_tool_use

            # For non-tool blocks (text, thinking), we don't need to track state
            return None, current_tool_use

        elif event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            if not delta:
                return None, current_tool_use

            delta_type = getattr(delta, "type", None)

            if delta_type == "text_delta":
                text = getattr(delta, "text", "")
                return StreamEvent(type="text", data=text), current_tool_use

            elif delta_type == "thinking_delta":
                thinking = getattr(delta, "thinking", "")
                return StreamEvent(type="thinking", data=thinking), current_tool_use

            elif delta_type == "signature_delta":
                # Handle thinking signature for extended thinking
                signature = getattr(delta, "signature", "")
                return StreamEvent(
                    type="thinking", signature=signature
                ), current_tool_use

            elif delta_type == "input_json_delta":
                if current_tool_use is None:
                    # Edge case: received input_json_delta without tool_use start
                    logger.warning("Received input_json_delta without tool_use context")
                    return None, None

                # Only accumulate if input is still a string (being built up)
                if isinstance(current_tool_use.get("input"), str):
                    partial_json = getattr(delta, "partial_json", "")
                    current_tool_use["input"] += partial_json
                # else: input was already set as a dict in content_block_start

                return None, current_tool_use

        elif event_type == "content_block_stop":
            if current_tool_use:
                # Use the parsed input from the content block if available
                # This handles empty inputs {} more reliably than parsing
                content_block = getattr(event, "content_block", None)
                if content_block and hasattr(content_block, "input"):
                    current_tool_use["input"] = content_block.input
                else:
                    # Fallback: parse accumulated JSON string
                    input_str = current_tool_use.get("input", "")
                    if isinstance(input_str, str):
                        # Need to parse the accumulated string
                        if not input_str or input_str.isspace():
                            # Empty or whitespace-only input
                            current_tool_use["input"] = {}
                        else:
                            try:
                                current_tool_use["input"] = json.loads(input_str)
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    f"Failed to parse tool input '{input_str}': {e}"
                                )
                                current_tool_use["input"] = {}
                    # else: input is already parsed

                tool_data = {
                    "id": current_tool_use.get("id", ""),
                    "name": current_tool_use.get("name", ""),
                    "input": current_tool_use.get("input", {}),
                }
                # Include server info if present
                if current_tool_use.get("server_name"):
                    tool_data["server_name"] = current_tool_use["server_name"]
                if current_tool_use.get("is_server_call"):
                    tool_data["is_server_call"] = current_tool_use["is_server_call"]

                # Emit different event type for MCP server tools
                if current_tool_use.get("is_server_call"):
                    return StreamEvent(type="server_tool_use", data=tool_data), None
                return StreamEvent(type="tool_use", data=tool_data), None

        elif event_type == "message_delta":
            # Handle token usage information
            if usage := getattr(event, "usage", None):
                self.log_usage(
                    Usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        total_tokens=usage.input_tokens + usage.output_tokens,
                    )
                )

            delta = getattr(event, "delta", None)
            if delta:
                stop_reason = getattr(delta, "stop_reason", None)
                if stop_reason == "max_tokens":
                    return StreamEvent(
                        type="error", data="Max tokens reached"
                    ), current_tool_use

            return None, current_tool_use

        elif event_type == "message_stop":
            # Final event - clean up any pending state
            if current_tool_use:
                logger.warning(
                    f"Message ended with incomplete tool use: {current_tool_use}"
                )
            return StreamEvent(type="done"), None

        # Unknown event type - log but don't fail
        if event_type and event_type not in (
            "message_start",
            "message_delta",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_stop",
        ):
            logger.debug(f"Unknown event type: {event_type}")

        return None, current_tool_use

    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """Generate a non-streaming response."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, mcp_servers, settings
        )

        try:
            response = self.client.messages.create(**kwargs)
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    def stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        settings: LLMSettings | None = None,
    ) -> Iterator[StreamEvent]:
        """Generate a streaming response."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, mcp_servers, settings
        )

        try:
            with self.client.messages.stream(**kwargs) as stream:
                current_tool_use: dict[str, Any] | None = None

                for event in stream:
                    stream_event, current_tool_use = self._handle_stream_event(
                        event, current_tool_use
                    )
                    if stream_event:
                        yield stream_event

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))

    async def agenerate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """Generate a non-streaming response asynchronously."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, mcp_servers, settings
        )

        try:
            response = await self.async_client.messages.create(**kwargs)
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    async def astream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        settings: LLMSettings | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streaming response asynchronously."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, mcp_servers, settings
        )

        try:
            async with self.async_client.messages.stream(**kwargs) as stream:
                current_tool_use: dict[str, Any] | None = None

                async for event in stream:
                    stream_event, current_tool_use = self._handle_stream_event(
                        event, current_tool_use
                    )
                    if stream_event:
                        yield stream_event

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))
