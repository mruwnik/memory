"""Model-string → provider lookup for agent runs ("anthropic/claude-sonnet-5-5")."""

from collections.abc import Callable

from memory.common import settings
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.base import BaseLLMProvider
from memory.common.llms.neutral import ModelInfo
from memory.common.llms.openai_provider import OpenAIProvider

PROVIDERS: dict[str, type[BaseLLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}
API_KEYS: dict[str, Callable[[], str]] = {
    "anthropic": lambda: settings.ANTHROPIC_API_KEY,
    "openai": lambda: settings.OPENAI_API_KEY,
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


def get_provider(model: str, api_key: str | None = None) -> BaseLLMProvider:
    """The provider for `model`, on `api_key`, or on the server's key when it is None."""
    provider, name = split_model(model)
    if api_key == "":
        raise ValueError(f"the {provider} API key for this run is empty")
    if api_key is None:
        api_key = API_KEYS[provider]()
    if not api_key:
        raise ValueError(
            f"{provider.upper()}_API_KEY is not set; agent runs on {model} cannot start"
        )
    return PROVIDERS[provider](api_key=api_key, model=name)


def all_models() -> dict[str, ModelInfo]:
    return {
        f"{p}/{m}": info
        for p, cls in PROVIDERS.items()
        for m, info in cls.MODELS.items()
    }
