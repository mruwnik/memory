# pyright: reportFunctionMemberAccess=false
from datetime import datetime, timezone

import pytest

from memory.agents import triggers
from memory.api.MCP.servers import agent
from memory.common import settings
from memory.common.db.models import AgentDefinition, AgentTrigger, APIKey
from tests.conftest import mcp_auth_context

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
CONFIG = {"filter": {"tags_any": ["ai"]}}


def definition(db, user, spec=SPEC):
    d = AgentDefinition(user_id=user.id, name="d", spec=spec)
    db.add(d)
    db.commit()
    return d


@pytest.mark.asyncio
async def test_triggers_are_created_listed_updated_and_deleted(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        created = await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)
        updated = await agent.trigger_upsert.fn(
            definition_id=d.id,
            trigger_id=created["id"],
            config=CONFIG | {"delivery": {"mode": "batch", "window_sec": 600}, "on_edit": True},
            enabled=False,
        )
        listed = await agent.triggers.fn(definition_id=d.id)
        await agent.trigger_delete.fn(trigger_id=created["id"])

    assert (created["kind"], created["enabled"], created["config"]["template"]) == (
        "ingest",
        True,
        triggers.DEFAULT_TEMPLATE,
    )
    assert created["config"]["on_edit"] is False
    assert (
        updated["enabled"],
        updated["config"]["delivery"]["window_sec"],
        updated["config"]["on_edit"],
    ) == (False, 600, True)
    assert [t["id"] for t in listed["triggers"]] == [created["id"]]
    assert db_session.query(AgentTrigger).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,expected",
    [
        ({"filter": {}}, "set at least one of"),
        ({"filter": {"regex": "("}}, "regex does not compile"),
        ({"filter": {"tags_any": ["x"]}, "nope": 1}, "Extra inputs"),
    ],
)
async def test_invalid_configs_are_refused_with_reasons(
    db_session, regular_user, user_session, fake_providers, config, expected
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=expected):
            await agent.trigger_upsert.fn(definition_id=d.id, config=config)


@pytest.mark.asyncio
async def test_re_enabling_clears_a_tripped_breaker(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    t = AgentTrigger(
        definition_id=d.id,
        kind="ingest",
        config=CONFIG,
        enabled=False,
        state={"tripped": {"reason": "rate", "at": "now"}},
    )
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        enabled = await agent.trigger_upsert.fn(
            definition_id=d.id, trigger_id=t.id, config=CONFIG, enabled=True
        )

    assert (enabled["enabled"], enabled["state"]) == (True, {})


@pytest.mark.asyncio
async def test_a_key_cannot_trigger_tools_it_could_not_run(
    db_session, regular_user, fake_providers
):
    d = definition(db_session, regular_user, SPEC | {"tools": ["core_search"]})
    key = APIKey.create(user_id=regular_user.id, name="k", scopes=["agents"])
    db_session.add(key)
    db_session.commit()

    with mcp_auth_context(key.key, scopes=["agents"]):
        with pytest.raises(ValueError, match="tool 'core_search' is not visible to you"):
            await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)


@pytest.mark.asyncio
async def test_a_trigger_remembers_the_key_that_made_it(
    db_session, regular_user, fake_providers
):
    d = definition(db_session, regular_user)
    key = APIKey.create(user_id=regular_user.id, name="k", scopes=["agents"])
    db_session.add(key)
    db_session.commit()

    with mcp_auth_context(key.key, scopes=["agents"]):
        created = await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)

    assert (created["api_key_id"], created["via_api_key"]) == (key.id, True)


@pytest.mark.asyncio
async def test_other_users_triggers_are_unknown(
    db_session, admin_user, regular_user, user_session, fake_providers
):
    theirs = definition(db_session, admin_user)
    t = AgentTrigger(definition_id=theirs.id, kind="ingest", config=CONFIG)
    mine = definition(db_session, regular_user)
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="unknown agent definition"):
            await agent.triggers.fn(definition_id=theirs.id)
        with pytest.raises(ValueError, match=f"unknown trigger {t.id}"):
            await agent.trigger_upsert.fn(
                definition_id=mine.id, trigger_id=t.id, config=CONFIG
            )
        with pytest.raises(ValueError, match=f"unknown trigger {t.id}"):
            await agent.trigger_delete.fn(trigger_id=t.id)


@pytest.mark.asyncio
async def test_list_all_summarises_triggers(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    db_session.add_all(
        [
            AgentTrigger(definition_id=d.id, kind="ingest", config=CONFIG),
            AgentTrigger(
                definition_id=d.id,
                kind="ingest",
                config=CONFIG,
                enabled=False,
                state={"tripped": {"reason": "rate", "at": "now"}},
            ),
        ]
    )
    db_session.commit()

    with mcp_auth_context(user_session.token):
        listed = await agent.list_all.fn()

    [entry] = listed["definitions"]
    assert entry["triggers"] == {"total": 2, "enabled": 1, "tripped": 1}


@pytest.mark.asyncio
async def test_enabled_triggers_are_capped_per_user(
    db_session, regular_user, user_session, fake_providers, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_MAX_TRIGGERS_PER_USER", 1)
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        first = await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)
        paused = await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG, enabled=False)
        edited = await agent.trigger_upsert.fn(
            definition_id=d.id, trigger_id=first["id"], config=CONFIG
        )
        with pytest.raises(ValueError, match="AGENT_MAX_TRIGGERS_PER_USER"):
            await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)
        with pytest.raises(ValueError, match="AGENT_MAX_TRIGGERS_PER_USER"):
            await agent.trigger_upsert.fn(
                definition_id=d.id, trigger_id=paused["id"], config=CONFIG, enabled=True
            )

    assert (paused["enabled"], edited["enabled"]) == (False, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tools,warned",
    [([], []), (["web_fetch"], ["web_fetch"]), (["meta_notify_user"], ["meta_notify_user"])],
)
async def test_write_and_egress_tools_are_flagged(
    db_session, admin_user, admin_session, fake_providers, admin_keys, tools, warned
):
    d = definition(db_session, admin_user, SPEC | {"tools": tools})

    with mcp_auth_context(admin_session.token):
        created = await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)

    assert [w for w in warned if any(w in x for x in created["warnings"])] == warned
    assert len(created["warnings"]) == len(warned[:1])


@pytest.fixture
def dropped(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "drop_buffer", calls.append)
    return calls


@pytest.mark.asyncio
async def test_pausing_works_after_access_is_lost(
    db_session, regular_user, user_session, fake_providers, dropped
):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        created = await agent.trigger_upsert.fn(definition_id=d.id, config=CONFIG)
    d.spec = SPEC | {"tools": ["meta_get_user"]}
    db_session.commit()

    with mcp_auth_context(user_session.token):
        paused = await agent.trigger_upsert.fn(
            definition_id=d.id, trigger_id=created["id"], enabled=False
        )

    assert paused["enabled"] is False
    assert dropped == [created["id"]]


@pytest.mark.asyncio
async def test_switching_off_batch_drops_the_buffer(
    db_session, regular_user, user_session, fake_providers, dropped
):
    d = definition(db_session, regular_user)
    batched = CONFIG | {"delivery": {"mode": "batch", "window_sec": 600}}
    with mcp_auth_context(user_session.token):
        created = await agent.trigger_upsert.fn(definition_id=d.id, config=batched)
        await agent.trigger_upsert.fn(
            definition_id=d.id, trigger_id=created["id"], config=batched | {"template": "x"}
        )
        kept = list(dropped)
        await agent.trigger_upsert.fn(definition_id=d.id, trigger_id=created["id"], config=CONFIG)

    assert (kept, dropped) == ([], [created["id"]])


@pytest.mark.asyncio
async def test_re_enabling_moves_active_since_forward(
    db_session, regular_user, user_session, fake_providers, dropped
):
    d = definition(db_session, regular_user)
    t = AgentTrigger(
        definition_id=d.id,
        kind="ingest",
        config=CONFIG,
        enabled=False,
        active_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        enabled = await agent.trigger_upsert.fn(
            definition_id=d.id, trigger_id=t.id, enabled=True
        )

    assert datetime.fromisoformat(enabled["active_since"]) > datetime(
        2026, 10, 1, tzinfo=timezone.utc
    )
