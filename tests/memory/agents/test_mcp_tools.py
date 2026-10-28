# pyright: reportFunctionMemberAccess=false
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.spec import AgentSpec
from memory.api.MCP.servers import agent
from memory.common import settings
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
        (SPEC | {"tools": ["agent_run"]}, "can never be used inside a run"),
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
            await agent.run.fn(input="x" * 200_001, spec=SPEC)


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
            await agent.run.fn(input="hi", definition_id=d.id)


@pytest.mark.asyncio
async def test_run_a_definition_and_an_ad_hoc_spec(
    db_session, regular_user, user_session, fake_providers, no_celery_dispatch
):
    with mcp_auth_context(user_session.token):
        d = await agent.upsert.fn(name="digest", spec=SPEC)
        by_definition = await agent.run.fn(input="hi", definition_id=d["id"])
        ad_hoc = await agent.run.fn(input="hi", spec=SPEC)

    runs = {r.id: r for r in db_session.query(AgentRun)}
    assert runs[by_definition["run_id"]].definition_id == d["id"]
    assert runs[ad_hoc["run_id"]].definition_id is None
    assert {r.trigger_kind for r in runs.values()} == {"manual"}
    assert no_celery_dispatch.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"definition_id": 1, "spec": SPEC},
        {"spec": SPEC, "agent": "fact-check"},
    ],
)
async def test_run_needs_exactly_one_source(
    db_session, user_session, fake_providers, kwargs
):
    with mcp_auth_context(user_session.token):
        with pytest.raises(
            ValueError, match="exactly one of definition_id, spec or agent"
        ):
            await agent.run.fn(input="hi", **kwargs)


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
    assert (run.definition_id, run.provider_key_id, run.trigger_ref) == (
        None,
        keys[payer].id,
        {"agent": "fact-check"},
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
async def test_runs_via_an_api_key_are_attributed_to_it(
    db_session, regular_user, fake_providers
):
    key = APIKey.create(user_id=regular_user.id, name="k")
    db_session.add(key)
    db_session.commit()

    with mcp_auth_context(key.key, scopes=["agents"]):
        result = await agent.run.fn(input="hi", spec=SPEC)

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
            await agent.run.fn(input="hi", spec=SPEC)


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

    with patch.object(agent.celery_app.control, "revoke") as revoke:
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
    fact_check.trigger_ref = {"agent": "fact-check"}
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
    assert not any(n.startswith("agent_") for n in names)
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
