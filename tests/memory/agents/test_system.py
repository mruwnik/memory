import pytest

from memory.agents.spec import AgentSpec, spec_errors
from memory.agents.system import SOURCE_PROVIDER, SYSTEM_AGENTS, system_agent
from memory.common.llms.tools import is_strict_schema


@pytest.mark.parametrize("agent", list(SYSTEM_AGENTS.values()), ids=list(SYSTEM_AGENTS))
def test_every_system_agent_is_a_valid_agent_spec(agent):
    assert spec_errors(AgentSpec.model_validate(agent.spec_dict())) == []


@pytest.mark.parametrize("agent", list(SYSTEM_AGENTS.values()), ids=list(SYSTEM_AGENTS))
def test_slugs_are_namespaced_and_match_their_key(agent):
    assert SYSTEM_AGENTS[agent.slug] is agent
    assert agent.slug.startswith("system:")


@pytest.mark.parametrize(
    "agent",
    [a for a in SYSTEM_AGENTS.values() if a.result_schema],
    ids=[slug for slug, a in SYSTEM_AGENTS.items() if a.result_schema],
)
def test_result_schemas_are_strict_because_parse_result_trusts_strict_tool_use(agent):
    assert is_strict_schema(agent.result_schema)


def test_ingest_agents_share_the_provider_source_keys_are_checked_against():
    assert {a.provider for a in SYSTEM_AGENTS.values() if a.use == "ingest"} == {SOURCE_PROVIDER}


def test_unknown_slugs_are_refused():
    with pytest.raises(ValueError, match="unknown system agent 'system:nope'"):
        system_agent("system:nope")


STRICT_OBJECT = {
    "type": "object",
    "properties": {"a": {"type": "string"}},
    "required": ["a"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "schema,strict",
    [
        (STRICT_OBJECT, True),
        ({"anyOf": [STRICT_OBJECT, {"type": "null"}]}, True),
        ({"anyOf": [{"type": "object"}, {"type": "null"}]}, False),
        ({"oneOf": [{"type": "string"}]}, False),
        ({"$ref": "#/$defs/x"}, False),
        ({"allOf": [{"type": "string"}]}, False),
    ],
)
def test_strict_schema_rejects_what_strict_tool_use_refuses(schema, strict):
    assert is_strict_schema(schema) is strict
