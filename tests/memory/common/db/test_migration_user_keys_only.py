import importlib.util
from pathlib import Path

import pytest

from memory.common.db.models import AgentDefinition, LLMProviderKey

MIGRATION = Path(__file__).parents[4] / "db/migrations/versions/20261029_user_keys_only.py"


def migration():
    spec = importlib.util.spec_from_file_location("user_keys_only", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def define(db, user, spec):
    definition = AgentDefinition(user_id=user.id, name="a", description="", spec=spec)
    db.add(definition)
    db.commit()
    return definition


def default_key(db, user, provider):
    key = LLMProviderKey(user_id=user.id, provider=provider, label=provider, is_default=True)
    key.key = "sk-test"
    db.add(key)
    db.commit()
    return key


@pytest.mark.parametrize(
    "spec",
    [
        {"system_prompt": "x", "model": "anthropic/claude-sonnet-5-5", "provider_key": "server"},
        {"system_prompt": "x", "provider_key": "server"},  # no model: DEFAULT_AGENT_MODEL is anthropic
    ],
)
def test_server_pinned_definitions_move_to_the_owners_default_key(db_session, regular_user, spec):
    key = default_key(db_session, regular_user, "anthropic")
    definition = define(db_session, regular_user, spec)

    migration().pin_server_definitions(db_session.connection())
    db_session.expire_all()

    assert db_session.get(AgentDefinition, definition.id).spec["provider_key"] == key.id


def test_server_pinned_definitions_without_a_matching_default_lose_the_pin(db_session, regular_user):
    default_key(db_session, regular_user, "openai")
    spec = {"system_prompt": "x", "model": "anthropic/claude-sonnet-5-5", "provider_key": "server"}
    definition = define(db_session, regular_user, spec)

    migration().pin_server_definitions(db_session.connection())
    db_session.expire_all()

    assert "provider_key" not in db_session.get(AgentDefinition, definition.id).spec
