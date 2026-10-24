"""Model-string → provider lookup for agent runs ("anthropic/claude-sonnet-5-5")."""

from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.base import BaseLLMProvider
from memory.common.llms.neutral import ModelInfo
from memory.common.llms.openai_provider import OpenAIProvider

PROVIDERS: dict[str, type[BaseLLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def split_model(model: str) -> tuple[str, str]:
    provider, sep, name = model.partition("/")
    if not sep or provider not in PROVIDERS:
        raise ValueError(
            f"unknown provider in model {model!r}; expected one of: "
            f"{', '.join(sorted(PROVIDERS))} as '<provider>/<model>'"
        )
    return provider, name


def provider_class(model: str) -> type[BaseLLMProvider]:
    return PROVIDERS[split_model(model)[0]]


def get_provider(model: str, api_key: str) -> BaseLLMProvider:
    """The provider for `model`, on the user's `api_key`."""
    provider, name = split_model(model)
    if not api_key:
        raise ValueError(f"the {provider} API key for this run is empty")
    return PROVIDERS[provider](api_key=api_key, model=name)


def all_models() -> dict[str, ModelInfo]:
    return {
        f"{p}/{m}": info
        for p, cls in PROVIDERS.items()
        for m, info in cls.MODELS.items()
    }
