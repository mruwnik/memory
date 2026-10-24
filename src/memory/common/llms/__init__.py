"""LLM provider module for unified LLM access."""

import anthropic
import httpx
import httpx2
import openai

from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.base import BaseLLMProvider
from memory.common.llms.openai_provider import OpenAIProvider

__all__ = [
    "BaseLLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
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
