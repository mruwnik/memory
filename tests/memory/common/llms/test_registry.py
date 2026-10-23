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
