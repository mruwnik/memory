import anthropic
import openai
import pytest

from memory.agents.spec import AgentSpec, spec_errors
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.openai_provider import OpenAIProvider
from memory.common.llms.registry import all_models, provider_class


@pytest.mark.parametrize("model", ["anthropic/claude-sonnet-5-5", "anthropic/anything"])
def test_provider_class_resolves_by_prefix(model):
    assert provider_class(model) is AnthropicProvider


@pytest.mark.parametrize("model", ["nope/x", "no-slash"])
def test_provider_class_rejects_unknown_providers(model):
    with pytest.raises(ValueError, match="unknown provider"):
        provider_class(model)


@pytest.mark.parametrize("cls", [AnthropicProvider, OpenAIProvider])
def test_an_unknown_model_names_the_agent_capable_ones(cls):
    with pytest.raises(ValueError, match="agent-capable models: "):
        cls.model_info("gpt-1")


@pytest.mark.parametrize(
    "cls,sdk",
    [(AnthropicProvider, anthropic), (OpenAIProvider, openai)],
)
def test_only_a_401_rejects_the_key_and_a_403_only_fails_the_request(cls, sdk):
    # A 403 also means a model, workspace or region the key can't use.
    assert cls.KEY_REJECTED == (sdk.AuthenticationError,)
    assert cls.KEY_FORBIDDEN == (sdk.PermissionDeniedError,)


@pytest.mark.parametrize(
    "cls,sdk",
    [(AnthropicProvider, anthropic), (OpenAIProvider, openai)],
)
def test_a_404_means_the_model_is_gone(cls, sdk):
    assert cls.MODEL_UNAVAILABLE == (sdk.NotFoundError,)


@pytest.mark.parametrize("model", sorted(all_models()))
def test_every_catalog_model_takes_a_default_spec(model):
    assert spec_errors(AgentSpec(system_prompt="s", model=model)) == []
