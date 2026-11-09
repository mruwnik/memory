# pyright: reportFunctionMemberAccess=false
import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from memory.agents import cancel
from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.mcp_client import memory_tools
from memory.agents.spec import AgentSpec
from memory.api.MCP.servers import agent
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    APIKey,
    LLMProviderKey,
)
from tests.conftest import mcp_auth_context

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_TIMEOUT_SEC", 1800)


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.token):
        created = await agent.upsert.fn(name="digest", spec=SPEC)
        updated = await agent.upsert.fn(
            name="digest2", spec=SPEC | {"max_turns": 5}, definition_id=created["id"]
        )

    assert updated["id"] == created["id"]
    assert (updated["name"], updated["spec"]["max_turns"]) == ("digest2", 5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec,expected",
    [
        (SPEC | {"tools": ["agent_delete"]}, "can never be used inside a run"),
        (
            SPEC | {"tools": ["people_merge"]},
            "tool 'people_merge' is not visible to you",
        ),
        (SPEC | {"bogus": 1}, "invalid spec"),
    ],
)
async def test_upsert_rejects_with_reasons(
    db_session, regular_user, user_session, fake_providers, spec, expected
):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=expected):
            await agent.upsert.fn(name="bad", spec=spec)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,expected",
    [("   ", "name is required"), ("x" * 101, "at most 100 characters")],
)
async def test_upsert_checks_the_name(
    db_session, regular_user, user_session, fake_providers, name, expected
):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=expected):
            await agent.upsert.fn(name=name, spec=SPEC)


@pytest.mark.asyncio
async def test_upsert_strips_the_name(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.token):
        created = await agent.upsert.fn(name="  digest  ", spec=SPEC)

    assert created["name"] == "digest"


@pytest.mark.asyncio
async def test_run_input_is_capped(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="input is longer than 200000 characters"):
            await agent.run.fn(input="x" * 200_001, agent=SPEC)


@pytest.mark.asyncio
async def test_catalog_lists_only_your_external_servers(db_session, admin_session):
    with mcp_auth_context(admin_session.token):
        catalog = await agent.catalog.fn()

    assert catalog["mcp_servers"] == []


@pytest.mark.asyncio
async def test_duplicate_names_are_refused(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.token):
        await agent.upsert.fn(name="digest", spec=SPEC)
        with pytest.raises(ValueError, match="already have an agent named 'digest'"):
            await agent.upsert.fn(name="digest", spec=SPEC)


@pytest.mark.asyncio
async def test_other_users_definitions_are_unknown(
    db_session, admin_user, user_session, fake_providers
):
    d = AgentDefinition(user_id=admin_user.id, name="mine", spec=SPEC)
    db_session.add(d)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=f"unknown agent definition {d.id}"):
            await agent.fetch.fn(definition_id=d.id)
        with pytest.raises(ValueError, match=f"unknown agent definition {d.id}"):
            await agent.run.fn(input="hi", agent=d.id)


@pytest.mark.asyncio
async def test_run_a_definition_and_an_ad_hoc_spec(
    db_session, regular_user, user_session, fake_providers, no_celery_dispatch
):
    with mcp_auth_context(user_session.token):
        d = await agent.upsert.fn(name="digest", spec=SPEC)
        by_id = await agent.run.fn(input="hi", agent=d["id"])
        by_name = await agent.run.fn(input="hi", agent="digest")
        inline = await agent.run.fn(input="hi", agent=SPEC)

    runs = {r.id: r for r in db_session.query(AgentRun)}
    refs = [(runs[r["run_id"]].definition_id, runs[r["run_id"]].agent) for r in (by_id, by_name, inline)]
    assert refs == [(d["id"], None), (d["id"], None), (None, None)]
    assert {r.trigger_kind for r in runs.values()} == {"manual"}
    assert no_celery_dispatch.call_count == 3
    assert db_session.query(AgentDefinition).count() == 1


def provider_keys(db, user, statuses: dict[str, str]) -> dict[str, LLMProviderKey]:
    keys = {}
    for provider, status in statuses.items():
        key = LLMProviderKey(
            user_id=user.id, provider=provider, label=provider, is_default=True, status=status
        )
        key.key = "sk-x"
        db.add(key)
        keys[provider] = key
    db.commit()
    return keys


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statuses,payer,model",
    [
        ({"anthropic": "ok"}, "anthropic", "anthropic/claude-sonnet-5-5"),
        ({"openai": "ok"}, "openai", "openai/gpt-6.1-sol"),
        ({"anthropic": "ok", "openai": "ok"}, "anthropic", "anthropic/claude-sonnet-5-5"),
        ({"anthropic": "invalid", "openai": "ok"}, "openai", "openai/gpt-6.1-sol"),
    ],
)
async def test_run_a_prebuilt_agent_on_the_callers_key(
    db_session, regular_user, user_session, scripted_llm, no_celery_dispatch, statuses, payer, model
):
    keys = provider_keys(db_session, regular_user, statuses)

    with mcp_auth_context(user_session.token, scopes=["agents"]):
        started = await agent.run.fn(input="Is the sky green?", agent="fact-check")

    run = db_session.get(AgentRun, started["run_id"])
    assert run is not None
    assert (run.definition_id, run.provider_key_id, run.agent) == (
        None,
        keys[payer].id,
        "fact-check",
    )
    assert (run.spec["model"], run.spec["tools"]) == (model, ["web_search", "web_fetch"])


@pytest.mark.asyncio
async def test_a_prebuilt_agent_blames_the_key_the_caller_has(
    db_session, regular_user, user_session, scripted_llm
):
    provider_keys(db_session, regular_user, {"openai": "invalid"})

    with mcp_auth_context(user_session.token, scopes=["agents"]):
        with pytest.raises(ValueError, match="openai"):
            await agent.run.fn(input="Is the sky green?", agent="fact-check")


@pytest.mark.asyncio
async def test_unknown_prebuilt_agents_are_refused(db_session, user_session, fake_providers):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="unknown agent 'nope'"):
            await agent.run.fn(input="hi", agent="nope")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,model",
    [("anthropic", "anthropic/claude-haiku-5-5"), ("openai", "openai/gpt-6-luna")],
)
async def test_run_a_system_agent_on_the_callers_key(
    db_session, regular_user, user_session, scripted_llm, no_celery_dispatch, provider, model
):
    keys = provider_keys(db_session, regular_user, {provider: "ok"})

    with mcp_auth_context(user_session.token, scopes=["agents"]):
        started = await agent.run.fn(input="some text", agent="system:summarise")

    run = db_session.get(AgentRun, started["run_id"])
    assert run is not None
    assert (run.agent, run.provider_key_id, run.spec["model"], run.spec["max_turns"]) == (
        "system:summarise", keys[provider].id, model, 1
    )


@pytest.mark.asyncio
async def test_runs_via_an_api_key_are_attributed_to_it(
    db_session, regular_user, fake_providers
):
    key = APIKey.create(user_id=regular_user.id, name="k")
    db_session.add(key)
    db_session.commit()

    with mcp_auth_context(key.key, scopes=["agents"]):
        result = await agent.run.fn(input="hi", agent=SPEC)

    run = db_session.get(AgentRun, result["run_id"])
    assert run is not None
    assert (run.api_key_id, run.trigger_kind) == (key.id, "mcp")


@pytest.mark.asyncio
async def test_cap_refusals_surface_as_errors(
    db_session, regular_user, user_session, fake_providers
):
    db_session.add(
        AgentRun(
            user_id=regular_user.id,
            spec=SPEC,
            input="x",
            max_cost_usd=Decimal("1"),
            trigger_kind="manual",
            cost_usd=settings.AGENT_DEFAULT_USER_KEY_DAILY_COST_USD,
            status="done",
            provider_key_id=fake_providers.keys["anthropic"].id,
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="provider key pool"):
            await agent.run.fn(input="hi", agent=SPEC)


def seeded_run(db, user, status="running"):
    r = AgentRun(
        user_id=user.id,
        spec=SPEC,
        input="x",
        max_cost_usd=Decimal("1"),
        trigger_kind="manual",
        status=status,
    )
    db.add(r)
    db.commit()
    db.add_all(
        [
            AgentRunStep(
                run_id=r.id,
                seq=i,
                kind="user",
                content=[{"type": "text", "text": str(i)}],
            )
            for i in range(3)
        ]
    )
    db.commit()
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize("after_seq,expected", [(-1, [0, 1, 2]), (1, [2]), (2, [])])
async def test_wait_returns_steps_after_seq(
    db_session, regular_user, user_session, after_seq, expected
):
    r = seeded_run(db_session, regular_user, status="done")

    with mcp_auth_context(user_session.token):
        result = await agent.wait.fn(run_id=r.id, after_seq=after_seq)

    assert [s["seq"] for s in result["steps"]] == expected
    assert result["run"]["spec"] == SPEC


@pytest.mark.asyncio
@pytest.mark.parametrize("after_seq,expected", [(-1, [0, 1]), (1, [2])])
async def test_wait_pages_long_transcripts(
    db_session, regular_user, user_session, after_seq, expected
):
    r = seeded_run(db_session, regular_user, status="done")

    with mcp_auth_context(user_session.token):
        result = await agent.wait.fn(run_id=r.id, after_seq=after_seq, limit=2)

    assert [s["seq"] for s in result["steps"]] == expected


@pytest.mark.asyncio
async def test_other_users_runs_are_unknown(db_session, admin_user, user_session):
    r = seeded_run(db_session, admin_user)

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=f"unknown run {r.id}"):
            await agent.wait.fn(run_id=r.id)


@pytest.mark.asyncio
async def test_cancel_pending_revokes_and_cancels_immediately(
    db_session, regular_user, user_session
):
    r = seeded_run(db_session, regular_user, status="pending")

    with patch.object(cancel.app.control, "revoke") as revoke:
        with mcp_auth_context(user_session.token):
            result = await agent.cancel.fn(run_id=r.id)

    db_session.expire_all()
    found = db_session.get(AgentRun, r.id)
    assert result["status"] == "cancelled"
    revoke.assert_called_once_with(r.id)
    # A worker that claimed the run concurrently stops at its first check.
    assert found is not None
    assert found.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_cancel_running_sets_the_flag(db_session, regular_user, user_session):
    r = seeded_run(db_session, regular_user, status="running")

    with mcp_auth_context(user_session.token):
        result = await agent.cancel.fn(run_id=r.id)

    db_session.expire_all()
    found = db_session.get(AgentRun, r.id)
    assert result["status"] == "running"
    assert found is not None
    assert found.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_star_toggles(db_session, regular_user, user_session):
    r = seeded_run(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        assert (await agent.star.fn(run_id=r.id, starred=True))["starred"] is True
        assert (await agent.star.fn(run_id=r.id, starred=False))["starred"] is False


@pytest.mark.asyncio
async def test_list_runs_shows_only_your_own(
    db_session, regular_user, admin_user, user_session
):
    mine = seeded_run(db_session, regular_user)
    seeded_run(db_session, admin_user)

    with mcp_auth_context(user_session.token):
        result = await agent.list_runs.fn()

    assert [r["id"] for r in result["runs"]] == [mine.id]


@pytest.mark.asyncio
async def test_list_runs_filters_by_prebuilt_agent(db_session, regular_user, user_session):
    seeded_run(db_session, regular_user)
    fact_check = seeded_run(db_session, regular_user)
    fact_check.agent = "fact-check"
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await agent.list_runs.fn(agent="fact-check")

    assert [r["id"] for r in result["runs"]] == [fact_check.id]


@pytest.mark.asyncio
async def test_list_runs_pages_through_runs_sharing_a_created_at(
    db_session, regular_user, user_session
):
    runs = [seeded_run(db_session, regular_user) for _ in range(5)]
    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for r in runs:
        r.created_at = stamp
    db_session.commit()

    seen: list[str] = []
    cursor: dict = {}
    with mcp_auth_context(user_session.token):
        for _ in range(5):
            page = (await agent.list_runs.fn(limit=2, **cursor))["runs"]
            if not page:
                break
            seen += [r["id"] for r in page]
            cursor = {"before": page[-1]["created_at"], "before_id": page[-1]["id"]}

    assert sorted(seen) == sorted(r.id for r in runs)
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_catalog_lists_visible_tools_models_and_ceilings(
    db_session, admin_session
):
    with mcp_auth_context(admin_session.token):
        catalog = await agent.catalog.fn()

    names = {t["name"] for t in catalog["tools"]}
    assert "meta_get_user" in names
    assert {n for n in names if n.startswith("agent_")} == {"agent_run", "agent_wait", "agent_cancel"}
    assert catalog["builtins"] == ["web_fetch", "web_search"]
    assert [a["slug"] for a in catalog["agents"]] == ["fact-check"]
    sonnet = next(
        m for m in catalog["models"] if m["model"] == "anthropic/claude-sonnet-5-5"
    )
    assert sonnet["capabilities"]["reasoning_levels"] == [
        "high",
        "low",
        "medium",
        "off",
    ]
    assert catalog["ceilings"] == {
        "max_timeout_sec": 1800,
        "max_runs_per_hour": settings.AGENT_MAX_RUNS_PER_HOUR,
        "default_user_key_run_cost_usd": str(settings.AGENT_DEFAULT_USER_KEY_RUN_COST_USD),
        "max_spawn_depth": settings.AGENT_MAX_SPAWN_DEPTH,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defaults,model",
    [
        (["anthropic"], "anthropic/claude-haiku-5-5"),
        (["openai"], "openai/gpt-6-luna"),
        (["anthropic", "openai"], "anthropic/claude-haiku-5-5"),
    ],
)
async def test_system_agents_are_listed_with_the_model_the_viewers_keys_run(
    db_session, regular_user, user_session, defaults, model
):
    for provider in defaults:
        row = LLMProviderKey(user_id=regular_user.id, provider=provider, label=provider, is_default=True)
        row.key = "sk-x"
        db_session.add(row)
    db_session.commit()

    with mcp_auth_context(user_session.token, scopes=["agents"]):
        listed = (await agent.system_list.fn())["agents"]

    entry = next(a for a in listed if a["slug"] == "system:summarise")
    assert (entry["has_key"], entry["problem"], entry["model"]) == (True, None, model)


@pytest.mark.asyncio
async def test_system_agents_without_a_default_key_show_the_fix(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.token, scopes=["agents"]):
        listed = (await agent.system_list.fn())["agents"]

    assert {a["problem"] for a in listed} == {
        "Skipped until you add a default Anthropic or OpenAI key."
    }


@pytest.mark.asyncio
async def test_listed_definitions_carry_their_problems(
    db_session, regular_user, user_session, fake_providers
):
    db_session.add(
        AgentDefinition(
            user_id=regular_user.id,
            name="old",
            description="",
            spec={"system_prompt": "x", "model": "anthropic/m", "provider_key": 99999},
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.token, scopes=["agents"]):
        (definition,) = (await agent.list_all.fn())["definitions"]

    assert definition["problems"] == [
        "API key 99999 is not yours or was deleted; pick another key for this agent"
    ]


@pytest.mark.asyncio
async def test_tools_missing_from_the_cache_are_flagged_but_do_not_stop_runs(
    db_session, regular_user, user_session, fake_providers, connected_server
):
    spec = SPEC | {"mcp_servers": [{"server_id": connected_server.id, "tools": ["gone"]}]}
    definition = AgentDefinition(user_id=regular_user.id, name="d", description="", spec=spec)
    db_session.add(definition)
    db_session.commit()

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=AgentSpec.model_validate(spec),
        input="go",
        trigger=TriggerInfo(kind="cron"),
        definition=definition,
    )
    with mcp_auth_context(user_session.token, scopes=["agents"]):
        (listed,) = (await agent.list_all.fn())["definitions"]

    assert run.status == "pending"
    assert listed["problems"] == [
        f"tool 'gone' is no longer offered by MCP server '{connected_server.name}'"
    ]


def object_schema(params: dict, name: str) -> dict:
    """The object branch of a parameter's schema (its only branch, or its anyOf object)."""
    schema = params["properties"][name]
    return next(s for s in schema.get("anyOf", [schema]) if s.get("type") == "object")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,param", [("run", "agent"), ("upsert", "spec")])
async def test_spec_parameters_list_the_spec_fields_inline(tool, param):
    tools = await agent.agent_mcp.get_tools()
    schema = object_schema(tools[tool].parameters, param)

    assert set(schema["properties"]) == set(AgentSpec.model_fields)
    assert schema["required"] == ["system_prompt"]
    assert "$ref" not in json.dumps(schema)
    assert "$defs" not in json.dumps(tools[tool].parameters)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,param", [("run", "agent"), ("upsert", "spec")])
async def test_spec_parameters_take_a_provider_key_by_id_or_name(tool, param):
    tools = await agent.agent_mcp.get_tools()
    provider_key = object_schema(tools[tool].parameters, param)["properties"]["provider_key"]

    assert [t["type"] for t in provider_key["anyOf"]] == ["integer", "string", "null"]


@pytest.mark.parametrize(
    "spec,hinted",
    [
        ({"system_prompt": "x", "name": "digest"}, True),
        ({"system_prompt": "x", "description": "d"}, True),
        ({"model": "anthropic/m"}, False),
        ({"system_prompt": "x", "requires": {"bogus": []}}, False),
    ],
)
def test_an_unknown_spec_field_lists_the_accepted_ones(spec, hinted):
    with pytest.raises(ValueError, match="invalid spec") as raised:
        agent.parse_spec(spec)

    assert ("accepted fields: " in str(raised.value)) == hinted
    assert ("only system_prompt is required" in str(raised.value)) == hinted


@pytest.mark.asyncio
async def test_run_with_an_unknown_inline_field_names_the_accepted_fields(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="agent_upsert arguments, not spec fields"):
            await agent.run.fn(input="hi", agent=SPEC | {"name": "digest"})


@pytest.mark.asyncio
@pytest.mark.parametrize("notify", [True, False])
async def test_run_stores_whether_to_notify(
    db_session, regular_user, user_session, fake_providers, notify
):
    with mcp_auth_context(user_session.token):
        started = await agent.run.fn(input="hi", agent=SPEC, notify=notify)

    found = db_session.get(AgentRun, started["run_id"])
    assert found is not None
    assert found.notify_on_finish is notify
    assert found.serialize()["notify_on_finish"] is notify


RUN_TOOLS = ["agent_run", "agent_wait", "agent_list_runs"]


def calling_parent(db, user) -> AgentRun:
    """A running run whose tools let it start, follow and list runs."""
    parent = AgentRun(
        user_id=user.id,
        spec=SPEC | {"tools": RUN_TOOLS},
        input="x",
        max_cost_usd=Decimal("1"),
        trigger_kind="manual",
        status="running",
        scopes=["*"],
    )
    db.add(parent)
    db.commit()
    return parent


async def call_as_run(token: str, run_id: str, tool: str, arguments: dict) -> tuple[str, bool]:
    async with memory_tools(token, ["*"], run_id=run_id) as tools:
        return await tools.call(tool, arguments)


@pytest.mark.asyncio
async def test_a_run_cannot_ask_to_notify(db_session, admin_user, admin_session, fake_providers):
    parent = calling_parent(db_session, admin_user)

    text, is_error = await call_as_run(
        admin_session.token, parent.id, "agent_run", {"input": "go", "agent": SPEC, "notify": True}
    )

    assert is_error
    assert "runs started by runs can't ask to notify" in text
    assert db_session.query(AgentRun).count() == 1


def ended_run(db, user, status="done", **fields) -> AgentRun:
    r = AgentRun(
        user_id=user.id,
        spec=SPEC,
        input="x",
        max_cost_usd=Decimal("1"),
        trigger_kind=fields.pop("trigger_kind", "manual"),
        status=status,
        result={"text": "the answer"},
        finished_at=fields.pop("finished_at", datetime.now(timezone.utc)),
        **fields,
    )
    db.add(r)
    db.commit()
    return r


def seen_at(db, run_id: str) -> datetime | None:
    db.expire_all()
    found = db.get(AgentRun, run_id)
    assert found is not None
    return found.seen_at


@pytest.mark.asyncio
async def test_result_only_returns_the_run_without_spec_or_steps(
    db_session, regular_user, user_session
):
    r = seeded_run(db_session, regular_user, status="done")
    r.result = {"text": "the answer"}
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await agent.wait.fn(run_id=r.id, result_only=True)

    assert list(result) == ["run"]
    assert "spec" not in result["run"]
    assert (result["run"]["status"], result["run"]["result"]) == ("done", {"text": "the answer"})


async def finish_later(run_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    with make_session() as db:
        found = db.get(AgentRun, run_id)
        assert found is not None
        found.status = "done"
        found.finished_at = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_result_only_waits_past_new_steps_until_the_run_ends(
    db_session, regular_user, user_session, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_MAX_WAIT_SEC", 10)
    r = seeded_run(db_session, regular_user, status="running")

    with mcp_auth_context(user_session.token):
        result, _ = await asyncio.gather(
            agent.wait.fn(run_id=r.id, wait=10, result_only=True), finish_later(r.id, 1.5)
        )

    assert result["run"]["status"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,seen", [("done", True), ("failed", True), ("running", False)])
async def test_wait_marks_the_owners_ended_run_seen(
    db_session, regular_user, user_session, status, seen
):
    r = seeded_run(db_session, regular_user, status=status)

    with mcp_auth_context(user_session.token):
        await agent.wait.fn(run_id=r.id)

    assert (seen_at(db_session, r.id) is not None) is seen


@pytest.mark.asyncio
@pytest.mark.parametrize("result_only", [True, False])
async def test_an_admin_waiting_on_anothers_run_leaves_it_unseen(
    db_session, regular_user, admin_user, admin_session, result_only
):
    r = ended_run(db_session, regular_user)

    with mcp_auth_context(admin_session.token):
        await agent.wait.fn(run_id=r.id, result_only=result_only)

    assert seen_at(db_session, r.id) is None


@pytest.mark.asyncio
async def test_a_run_waiting_on_its_child_leaves_it_unseen(db_session, admin_user, admin_session):
    parent = calling_parent(db_session, admin_user)
    child = ended_run(db_session, admin_user, parent_run_id=parent.id, depth=1, trigger_kind="agent")

    text, is_error = await call_as_run(admin_session.token, parent.id, "agent_wait", {"run_id": child.id})

    assert not is_error, text
    assert seen_at(db_session, child.id) is None


@pytest.mark.asyncio
async def test_unseen_lists_only_ended_runs_the_caller_started(
    db_session, regular_user, admin_user, user_session
):
    manual = ended_run(db_session, regular_user)
    over_api_key = ended_run(db_session, regular_user, status="cancelled", trigger_kind="mcp")
    ended_run(db_session, regular_user, parent_run_id=manual.id, depth=1, trigger_kind="agent")
    ended_run(db_session, regular_user, trigger_kind="ingest")
    ended_run(db_session, regular_user, trigger_kind="tick")
    ended_run(db_session, regular_user, status="running", finished_at=None)
    ended_run(db_session, regular_user, seen_at=datetime.now(timezone.utc))
    ended_run(db_session, admin_user)

    with mcp_auth_context(user_session.token):
        result = await agent.list_runs.fn(unseen=True)

    assert {r["id"] for r in result["runs"]} == {manual.id, over_api_key.id}


@pytest.mark.asyncio
async def test_an_unseen_listing_marks_its_runs_seen(db_session, regular_user, user_session):
    runs = [ended_run(db_session, regular_user) for _ in range(2)]

    with mcp_auth_context(user_session.token):
        first = await agent.list_runs.fn(unseen=True)
        second = await agent.list_runs.fn(unseen=True)

    assert len(first["runs"]) == 2
    assert second["runs"] == []
    assert all(seen_at(db_session, r.id) is not None for r in runs)


@pytest.mark.asyncio
async def test_a_plain_listing_leaves_runs_unseen(db_session, regular_user, user_session):
    r = ended_run(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        await agent.list_runs.fn()

    assert seen_at(db_session, r.id) is None


@pytest.mark.asyncio
async def test_a_run_waited_on_is_no_longer_unseen(db_session, regular_user, user_session):
    waited = ended_run(db_session, regular_user)
    other = ended_run(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        await agent.wait.fn(run_id=waited.id, result_only=True)
        result = await agent.list_runs.fn(unseen=True)

    assert [r["id"] for r in result["runs"]] == [other.id]


@pytest.mark.asyncio
async def test_a_run_listing_unseen_runs_leaves_them_unseen(db_session, admin_user, admin_session):
    parent = calling_parent(db_session, admin_user)
    r = ended_run(db_session, admin_user)

    text, is_error = await call_as_run(admin_session.token, parent.id, "agent_list_runs", {"unseen": True})

    assert not is_error, text
    assert seen_at(db_session, r.id) is None
