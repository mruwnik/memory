import anthropic
import openai
import pytest

from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.openai_provider import OpenAIProvider
from memory.common.llms.registry import provider_class


@pytest.mark.parametrize("model", ["anthropic/claude-sonnet-5-5", "anthropic/anything"])
def test_provider_class_resolves_by_prefix(model):
    assert provider_class(model) is AnthropicProvider


@pytest.mark.parametrize("model", ["nope/x", "no-slash"])
def test_provider_class_rejects_unknown_providers(model):
    with pytest.raises(ValueError, match="unknown provider"):
        provider_class(model)


def test_openai_has_no_agent_models_yet():
    with pytest.raises(ValueError, match="does not support agent runs"):
        OpenAIProvider.model_info("gpt-5")


@pytest.mark.parametrize(
    "cls,sdk",
    [(AnthropicProvider, anthropic), (OpenAIProvider, openai)],
)
def test_only_a_401_rejects_the_key_and_a_403_only_fails_the_request(cls, sdk):
    # A 403 also means a model, workspace or region the key can't use.
    assert cls.KEY_REJECTED == (sdk.AuthenticationError,)
    assert cls.KEY_FORBIDDEN == (sdk.PermissionDeniedError,)
