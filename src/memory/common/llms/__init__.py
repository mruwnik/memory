"""LLM provider module for unified LLM access."""

from typing import Any

import anthropic
import httpx
import httpx2
import openai
from PIL import Image


# New provider system
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
    create_provider,
)
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.openai_provider import OpenAIProvider
from memory.common import tokens

__all__ = [
    "BaseLLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "Message",
    "MessageRole",
    "MessageContent",
    "TextContent",
    "ImageContent",
    "ToolUseContent",
    "ToolResultContent",
    "ThinkingContent",
    "ToolDefinition",
    "StreamEvent",
    "LLMSettings",
    "create_provider",
    "PROVIDER_ERRORS",
]

# What a provider call can raise besides our own errors. The SDKs wrap transport
# failures only while sending; once a response streams, raw transport errors
# escape (anthropic is built on httpx2, openai on httpx).
PROVIDER_ERRORS: tuple[type[Exception], ...] = (
    anthropic.APIError,
    openai.APIError,
    httpx2.RequestError,
    httpx.RequestError,
)


def summary_settings(json_schema: dict[str, Any] | None = None) -> LLMSettings:
    return LLMSettings(temperature=0.3, max_tokens=2048, json_schema=json_schema)


def summarize(
    prompt: str,
    model: str,
    images: list[Image.Image] = [],
    system_prompt: str = "",
    json_schema: dict[str, Any] | None = None,
) -> str:
    """The response text; with `json_schema`, JSON guaranteed to match it."""
    provider = create_provider(model=model)
    content: list[MessageContent] = [TextContent(text=prompt)]
    content += [ImageContent(image=image) for image in images]
    return provider.generate(
        messages=[Message(role=MessageRole.USER, content=content)],
        system_prompt=system_prompt
        or "You are a helpful assistant that creates concise summaries and identifies key topics.",
        settings=summary_settings(json_schema),
    )


def truncate(content: str, target_tokens: int) -> str:
    target_chars = target_tokens * tokens.CHARS_PER_TOKEN
    if len(content) > target_chars:
        return content[:target_chars].rsplit(" ", 1)[0] + "..."
    return content
