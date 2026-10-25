import pytest

from memory.agents.prebuilt import PREBUILT_AGENTS
from memory.agents.spec import spec_errors


@pytest.mark.parametrize(
    "slug,model", [(a.slug, m) for a in PREBUILT_AGENTS.values() for m in a.models]
)
def test_prebuilt_agents_are_valid_on_every_model_they_name(slug, model):
    assert spec_errors(PREBUILT_AGENTS[slug].spec(model)) == []
