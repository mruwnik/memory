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


# bla = 1
# from memory.common.llms import *
# from memory.common.llms.tools.discord import make_discord_tools
# from memory.common.db.connection import make_session
# from memory.common.db.models import *

# model = "anthropic/claude-sonnet-4-5"
# provider = create_provider(model=model)
# with make_session() as session:
#     bot = session.query(DiscordBotUser).first()
#     server = session.query(DiscordServer).first()
#     channel = server.channels[0]
#     tools = make_discord_tools(bot, None, channel, model)

# def demo(msg: str):
#     messages = [
#         Message(
#             role=MessageRole.USER,
#             content=msg,
#         )
#     ]
#     for m in provider.stream_with_tools(messages, tools):
#         print(m)
