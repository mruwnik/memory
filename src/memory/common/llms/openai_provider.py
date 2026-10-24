"""OpenAI provider: registered for key handling, with no agent-capable models yet."""

import openai

from memory.common.llms.base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    provider = "openai"
    KEY_REJECTED = (openai.AuthenticationError,)
    KEY_FORBIDDEN = (openai.PermissionDeniedError,)
