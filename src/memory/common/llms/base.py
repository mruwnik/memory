"""Base classes and types for LLM providers."""

from abc import ABC
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

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
