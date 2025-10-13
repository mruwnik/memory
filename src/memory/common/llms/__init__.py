"""LLM provider module for unified LLM access."""

# Legacy imports for backwards compatibility
import logging

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
]

logger = logging.getLogger(__name__)


def summarize(
    prompt: str,
    model: str,
    images: list[Image.Image] = [],
    system_prompt: str = "",
) -> str:
    provider = create_provider(model=model)
    try:
        # Build message content
        content: list[MessageContent] = [TextContent(text=prompt)]
        for image in images:
            content.append(ImageContent(image=image))

        messages = [Message(role=MessageRole.USER, content=content)]
        settings_obj = LLMSettings(temperature=0.3, max_tokens=2048)

        res = provider.run_with_tools(
            messages=messages,
            system_prompt=system_prompt
            or "You are a helpful assistant that creates concise summaries and identifies key topics.",
            settings=settings_obj,
            tools={},
        )
        return res.response or ""
    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        raise


def truncate(content: str, target_tokens: int) -> str:
    target_chars = target_tokens * tokens.CHARS_PER_TOKEN
    if len(content) > target_chars:
        return content[:target_chars].rsplit(" ", 1)[0] + "..."
    return content
