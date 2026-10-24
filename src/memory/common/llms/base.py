"""Base classes and types for LLM providers."""

import logging
from abc import ABC
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from memory.common.llms.neutral import (
    Completion,
    GenerationParams,
    ModelInfo,
    NeutralMessage,
    Replay,
)
from memory.common.llms.tools import ToolDefinition
from memory.common.llms.usage import UsageTracker, RedisUsageTracker

logger = logging.getLogger(__name__)


@dataclass
class Usage:
    """Usage data for an LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class NoExtras(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    provider: str = ""
    MODELS: ClassVar[dict[str, ModelInfo]] = {}
    # What acomplete raises when the provider refuses the API key itself.
    KEY_REJECTED: ClassVar[tuple[type[Exception], ...]] = ()
    # What acomplete raises when a working key may not make this request (a model,
    # workspace, region or beta it can't use); the key stays usable for others.
    KEY_FORBIDDEN: ClassVar[tuple[type[Exception], ...]] = ()
    Extras: ClassVar[type[BaseModel]] = NoExtras

    @classmethod
    def model_info(cls, model: str) -> ModelInfo:
        if not cls.MODELS:
            raise ValueError(f"provider {cls.provider!r} does not support agent runs")
        if model not in cls.MODELS:
            known = ", ".join(sorted(cls.MODELS))
            raise ValueError(
                f"unknown {cls.provider} model {model!r}; agent-capable models: {known}"
            )
        return cls.MODELS[model]

    async def acomplete(
        self,
        *,
        system: str,
        messages: list[NeutralMessage],
        replay: list[Replay | None],
        tools: list[ToolDefinition],
        tool_choice: Literal["auto", "required"],
        params: GenerationParams,
    ) -> Completion:
        """One assistant turn. `replay[i]` is this provider's opaque data for `messages[i]`, or None."""
        raise NotImplementedError(f"provider {self.provider!r} does not support agent runs")

    def __init__(
        self, api_key: str, model: str, usage_tracker: UsageTracker | None = None
    ):
        """
        Initialize the LLM provider.

        Args:
            api_key: API key for the provider
            model: Model identifier
        """
        self.api_key = api_key
        self.model = model
        self.usage_tracker: UsageTracker = usage_tracker or RedisUsageTracker()

    def log_usage(self, usage: Usage):
        """Log usage data."""
        logger.debug(
            f"Token usage: {usage.input_tokens} input, {usage.output_tokens} output, {usage.total_tokens} total"
        )
        self.usage_tracker.record_usage(
            model=f"{self.provider}/{self.model}",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
