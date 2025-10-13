"""OpenAI LLM provider implementation."""

import logging
from typing import Any, AsyncIterator, Iterator, Optional

import openai

from memory.common.llms.base import (
    BaseLLMProvider,
    ImageContent,
    LLMSettings,
    Message,
    MessageContent,
    MessageRole,
    StreamEvent,
    TextContent,
    ThinkingContent,
    ToolDefinition,
    ToolResultContent,
    ToolUseContent,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI LLM provider with streaming and tool support."""

    def _initialize_client(self) -> openai.OpenAI:
        """Initialize the OpenAI client."""
        return openai.OpenAI(api_key=self.api_key)

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """
        Convert our Message format to OpenAI format.

        Args:
            messages: List of messages in our format

        Returns:
            List of messages in OpenAI format
        """
        openai_messages = []

        for msg in messages:
            if isinstance(msg.content, str):
                openai_messages.append({"role": msg.role.value, "content": msg.content})
            else:
                # Handle multi-part content
                content_parts = []
                for item in msg.content:
                    if isinstance(item, TextContent):
                        content_parts.append({"type": "text", "text": item.text})
                    elif isinstance(item, ImageContent):
                        encoded_image = self.encode_image(item.image)
                        image_part: dict[str, Any] = {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded_image}"
                            },
                        }
                        if item.detail:
                            image_part["image_url"]["detail"] = item.detail
                        content_parts.append(image_part)
                    elif isinstance(item, ToolUseContent):
                        # OpenAI doesn't have tool_use in content, it's a separate field
                        # We'll handle this by adding a tool_calls field to the message
                        pass
                    elif isinstance(item, ToolResultContent):
                        # OpenAI handles tool results as separate "tool" role messages
                        openai_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": item.tool_use_id,
                                "content": item.content,
                            }
                        )
                        continue
                    elif isinstance(item, ThinkingContent):
                        # OpenAI doesn't have native thinking support in most models
                        # We can add it as text with a special marker
                        content_parts.append(
                            {
                                "type": "text",
                                "text": f"[Thinking: {item.thinking}]",
                            }
                        )

                # Check if this message has tool calls
                tool_calls = [
                    item for item in msg.content if isinstance(item, ToolUseContent)
                ]

                message_dict: dict[str, Any] = {"role": msg.role.value}

                if content_parts:
                    message_dict["content"] = content_parts

                if tool_calls:
                    message_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": str(tc.input)},
                        }
                        for tc in tool_calls
                    ]

                openai_messages.append(message_dict)

        return openai_messages

    def _convert_tools(
        self, tools: Optional[list[ToolDefinition]]
    ) -> Optional[list[dict[str, Any]]]:
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

    def generate(
        self,
        messages: list[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[list[ToolDefinition]] = None,
        settings: Optional[LLMSettings] = None,
    ) -> str:
        """Generate a non-streaming response."""
        settings = settings or LLMSettings()

        openai_messages = self._convert_messages(messages)

        # Add system prompt as first message if provided
        if system_prompt:
            openai_messages.insert(
                0, {"role": "system", "content": system_prompt}
            )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
        }

        if settings.stop_sequences:
            kwargs["stop"] = settings.stop_sequences

        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    def stream(
        self,
        messages: list[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[list[ToolDefinition]] = None,
        settings: Optional[LLMSettings] = None,
    ) -> Iterator[StreamEvent]:
        """Generate a streaming response."""
        settings = settings or LLMSettings()

        openai_messages = self._convert_messages(messages)

        # Add system prompt as first message if provided
        if system_prompt:
            openai_messages.insert(
                0, {"role": "system", "content": system_prompt}
            )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
            "stream": True,
        }

        if settings.stop_sequences:
            kwargs["stop"] = settings.stop_sequences

        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            stream = self.client.chat.completions.create(**kwargs)

            current_tool_call: Optional[dict[str, Any]] = None

            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Handle text content
                if delta.content:
                    yield StreamEvent(type="text", data=delta.content)

                # Handle tool calls
                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        if tool_call.id:
                            # New tool call starting
                            if current_tool_call:
                                # Yield the previous one
                                yield StreamEvent(
                                    type="tool_use", data=current_tool_call
                                )
                            current_tool_call = {
                                "id": tool_call.id,
                                "name": tool_call.function.name or "",
                                "arguments": tool_call.function.arguments or "",
                            }
                        elif current_tool_call and tool_call.function.arguments:
                            # Continue building the current tool call
                            current_tool_call["arguments"] += (
                                tool_call.function.arguments
                            )

                # Check if stream is finished
                if chunk.choices[0].finish_reason:
                    if current_tool_call:
                        yield StreamEvent(type="tool_use", data=current_tool_call)
                        current_tool_call = None

            yield StreamEvent(type="done")

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))

    async def agenerate(
        self,
        messages: list[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[list[ToolDefinition]] = None,
        settings: Optional[LLMSettings] = None,
    ) -> str:
        """Generate a non-streaming response asynchronously."""
        settings = settings or LLMSettings()

        # Use async client
        async_client = openai.AsyncOpenAI(api_key=self.api_key)

        openai_messages = self._convert_messages(messages)

        # Add system prompt as first message if provided
        if system_prompt:
            openai_messages.insert(
                0, {"role": "system", "content": system_prompt}
            )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
        }

        if settings.stop_sequences:
            kwargs["stop"] = settings.stop_sequences

        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = await async_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    async def astream(
        self,
        messages: list[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[list[ToolDefinition]] = None,
        settings: Optional[LLMSettings] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streaming response asynchronously."""
        settings = settings or LLMSettings()

        # Use async client
        async_client = openai.AsyncOpenAI(api_key=self.api_key)

        openai_messages = self._convert_messages(messages)

        # Add system prompt as first message if provided
        if system_prompt:
            openai_messages.insert(
                0, {"role": "system", "content": system_prompt}
            )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
            "stream": True,
        }

        if settings.stop_sequences:
            kwargs["stop"] = settings.stop_sequences

        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            stream = await async_client.chat.completions.create(**kwargs)

            current_tool_call: Optional[dict[str, Any]] = None

            async for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Handle text content
                if delta.content:
                    yield StreamEvent(type="text", data=delta.content)

                # Handle tool calls
                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        if tool_call.id:
                            # New tool call starting
                            if current_tool_call:
                                # Yield the previous one
                                yield StreamEvent(
                                    type="tool_use", data=current_tool_call
                                )
                            current_tool_call = {
                                "id": tool_call.id,
                                "name": tool_call.function.name or "",
                                "arguments": tool_call.function.arguments or "",
                            }
                        elif current_tool_call and tool_call.function.arguments:
                            # Continue building the current tool call
                            current_tool_call["arguments"] += (
                                tool_call.function.arguments
                            )

                # Check if stream is finished
                if chunk.choices[0].finish_reason:
                    if current_tool_call:
                        yield StreamEvent(type="tool_use", data=current_tool_call)
                        current_tool_call = None

            yield StreamEvent(type="done")

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))
