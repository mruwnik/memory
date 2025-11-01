"""OpenAI LLM provider implementation."""

import json
import logging
from typing import Any, AsyncIterator, Iterator

import openai

from memory.common.llms.base import (
    BaseLLMProvider,
    ImageContent,
    LLMSettings,
    Message,
    StreamEvent,
    TextContent,
    ToolDefinition,
    ToolResultContent,
    ToolUseContent,
    Usage,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI LLM provider with streaming and tool support."""

    provider = "openai"

    # Models that use max_completion_tokens instead of max_tokens
    # These are reasoning models with different parameter requirements
    NON_REASONING_MODELS = {"gpt-4o"}

    def __init__(self, api_key: str, model: str):
        """
        Initialize the OpenAI provider.

        Args:
            api_key: OpenAI API key
            model: Model identifier
        """
        super().__init__(api_key, model)
        self._async_client: openai.AsyncOpenAI | None = None

    def _is_reasoning_model(self) -> bool:
        """
        Check if the current model is a reasoning model (o1 series).

        Reasoning models have different parameter requirements:
        - Use max_completion_tokens instead of max_tokens
        - Don't support temperature (always uses temperature=1)
        - Don't support top_p
        - Don't support system messages via system parameter
        """
        return self.model.lower() not in self.NON_REASONING_MODELS

    def _initialize_client(self) -> openai.OpenAI:
        """Initialize the OpenAI client."""
        return openai.OpenAI(api_key=self.api_key)

    @property
    def async_client(self) -> openai.AsyncOpenAI:
        """Lazy-load the async client."""
        if self._async_client is None:
            self._async_client = openai.AsyncOpenAI(api_key=self.api_key)
        return self._async_client

    def _convert_text_content(self, content: TextContent) -> dict[str, Any]:
        """Convert TextContent to OpenAI format."""
        return {"type": "text", "text": content.text}

    def _convert_image_content(self, content: ImageContent) -> dict[str, Any]:
        """Convert ImageContent to OpenAI image_url format."""
        encoded_image = self.encode_image(content.image)
        image_part: dict[str, Any] = {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
        }
        if content.detail:
            image_part["image_url"]["detail"] = content.detail
        return image_part

    def _convert_tool_use_content(self, content: ToolUseContent) -> dict[str, Any]:
        """Convert ToolUseContent to provider format. Override for custom format."""
        return {
            "id": content.id,
            "type": "function",
            "function": {
                "name": content.name,
                "arguments": json.dumps(content.input),
            },
        }

    def _convert_tool_result_content(
        self, content: ToolResultContent
    ) -> dict[str, Any]:
        """Convert ToolResultContent to provider format. Override for custom format."""
        return {
            "role": "tool",
            "tool_call_id": content.tool_use_id,
            "content": content.content,
        }

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """
        Convert messages to OpenAI format.

        OpenAI has special requirements:
        - ToolResultContent creates separate "tool" role messages
        - ToolUseContent becomes tool_calls field on assistant messages
        - One input Message can produce multiple output messages

        Returns:
            Flat list of OpenAI-formatted message dicts
        """
        openai_messages: list[dict[str, Any]] = []

        for message in messages:
            # Handle simple string content
            if isinstance(message.content, str):
                openai_messages.append(
                    {"role": message.role.value, "content": message.content}
                )
                continue

            # Handle multi-part content
            content_parts: list[dict[str, Any]] = []
            tool_calls_list: list[dict[str, Any]] = []

            for item in message.content:
                if isinstance(item, ToolResultContent):
                    openai_messages.append(self._convert_tool_result_content(item))
                elif isinstance(item, ToolUseContent):
                    tool_calls_list.append(self._convert_tool_use_content(item))
                else:
                    content_parts.append(self._convert_message_content(item))

            if content_parts or tool_calls_list:
                msg_dict: dict[str, Any] = {"role": message.role.value}

                if content_parts:
                    msg_dict["content"] = content_parts
                elif tool_calls_list:
                    # Assistant messages with tool calls need content field (use empty string)
                    msg_dict["content"] = ""

                if tool_calls_list:
                    msg_dict["tool_calls"] = tool_calls_list

                openai_messages.append(msg_dict)

        return openai_messages

    def _convert_tools(
        self, tools: list[ToolDefinition] | None
    ) -> list[dict[str, Any]] | None:
        """
        Convert our tool definitions to OpenAI format.

        Args:
            tools: List of tool definitions

        Returns:
            List of tools in OpenAI format
        """
        if not tools:
            return None

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    def _build_request_kwargs(
        self,
        messages: list[Message],
        system_prompt: str | None,
        tools: list[ToolDefinition] | None,
        settings: LLMSettings,
        stream: bool = False,
    ) -> dict[str, Any]:
        """
        Build common request kwargs for API calls.

        Args:
            messages: Conversation history
            system_prompt: Optional system prompt
            tools: Optional list of tools
            settings: LLM settings
            stream: Whether to enable streaming

        Returns:
            Dictionary of kwargs for OpenAI API call
        """
        openai_messages = self._convert_messages(messages)
        is_reasoning = self._is_reasoning_model()

        # Log info for reasoning models on first use
        if is_reasoning:
            logger.debug(
                f"Using reasoning model {self.model}: "
                "max_completion_tokens will be used, temperature/top_p ignored"
            )

        # Reasoning models (o1) don't support system parameter
        # System message must be added as a developer message instead
        if system_prompt:
            if is_reasoning:
                # For o1 models, add system prompt as a developer message
                openai_messages.insert(
                    0, {"role": "developer", "content": system_prompt}
                )
            else:
                # For other models, add as system message
                openai_messages.insert(0, {"role": "system", "content": system_prompt})

        # Reasoning models use max_completion_tokens instead of max_tokens
        max_tokens_key = "max_completion_tokens" if is_reasoning else "max_tokens"

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            max_tokens_key: settings.max_tokens,
        }

        # Reasoning models don't support temperature or top_p
        if not is_reasoning:
            kwargs["temperature"] = settings.temperature
            kwargs["top_p"] = settings.top_p

        if stream:
            kwargs["stream"] = True

        if settings.stop_sequences:
            kwargs["stop"] = settings.stop_sequences

        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        return kwargs

    def _parse_and_finalize_tool_call(
        self, tool_call: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Parse the accumulated tool call arguments and prepare for yielding.

        Args:
            tool_call: Tool call dict with 'arguments' field (JSON string)

        Returns:
            Tool call dict with parsed 'input' field (dict)
        """
        try:
            tool_call["input"] = json.loads(tool_call["arguments"])
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to parse tool arguments '{tool_call['arguments']}': {e}"
            )
            tool_call["input"] = {}
        del tool_call["arguments"]
        return tool_call

    def _handle_stream_chunk(
        self,
        chunk: Any,
        current_tool_call: dict[str, Any] | None,
    ) -> tuple[list[StreamEvent], dict[str, Any] | None]:
        """
        Handle a single streaming chunk and return events and updated tool state.

        Args:
            chunk: Streaming chunk from OpenAI
            current_tool_call: Current tool call being accumulated (or None)

        Returns:
            Tuple of (list of StreamEvents to yield, updated current_tool_call)
        """
        events: list[StreamEvent] = []

        # Handle usage information (comes in final chunk with empty choices)
        if hasattr(chunk, "usage") and chunk.usage:
            usage = chunk.usage
            self.log_usage(
                Usage(
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                )
            )

        if not chunk.choices:
            return events, current_tool_call

        delta = chunk.choices[0].delta

        # Handle text content
        if delta.content:
            events.append(StreamEvent(type="text", data=delta.content))

        # Handle tool calls
        if delta.tool_calls:
            for tool_call in delta.tool_calls:
                if tool_call.id:
                    # New tool call starting
                    if current_tool_call:
                        # Yield the previous one with parsed input
                        finalized = self._parse_and_finalize_tool_call(
                            current_tool_call
                        )
                        events.append(StreamEvent(type="tool_use", data=finalized))
                    current_tool_call = {
                        "id": tool_call.id,
                        "name": tool_call.function.name or "",
                        "arguments": tool_call.function.arguments or "",
                    }
                elif current_tool_call and tool_call.function.arguments:
                    # Continue building the current tool call
                    current_tool_call["arguments"] += tool_call.function.arguments

        # Check if stream is finished
        if chunk.choices[0].finish_reason:
            if current_tool_call:
                # Parse the final tool call arguments
                finalized = self._parse_and_finalize_tool_call(current_tool_call)
                events.append(StreamEvent(type="tool_use", data=finalized))
                current_tool_call = None

        return events, current_tool_call

    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """Generate a non-streaming response."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings, stream=False
        )

        try:
            response = self.client.chat.completions.create(**kwargs)
            usage = response.usage
            self.log_usage(
                Usage(
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                )
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    def stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> Iterator[StreamEvent]:
        """Generate a streaming response."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings, stream=True
        )

        if kwargs.get("stream"):
            kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = self.client.chat.completions.create(**kwargs)
            current_tool_call: dict[str, Any] | None = None

            for chunk in stream:
                events, current_tool_call = self._handle_stream_chunk(
                    chunk, current_tool_call
                )
                yield from events

            yield StreamEvent(type="done")

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))

    async def agenerate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """Generate a non-streaming response asynchronously."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings, stream=False
        )

        try:
            response = await self.async_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    async def astream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streaming response asynchronously."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings, stream=True
        )

        try:
            stream = await self.async_client.chat.completions.create(**kwargs)
            current_tool_call: dict[str, Any] | None = None

            async for chunk in stream:
                events, current_tool_call = self._handle_stream_chunk(
                    chunk, current_tool_call
                )
                for event in events:
                    yield event

            yield StreamEvent(type="done")

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))
