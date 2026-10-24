# pyright: reportFunctionMemberAccess=false
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
    APIKeyType,
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
    with mcp_auth_context(user_session.id):
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
            SPEC | {"tools": ["meta_get_user"]},
            "tool 'meta_get_user' is not visible to you",
        ),
        (SPEC | {"bogus": 1}, "invalid spec"),
    ],
)
async def test_upsert_rejects_with_reasons(
    db_session, regular_user, user_session, fake_providers, spec, expected
):
    with mcp_auth_context(user_session.id):
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
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=expected):
            await agent.upsert.fn(name=name, spec=SPEC)


@pytest.mark.asyncio
async def test_upsert_strips_the_name(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.id):
        created = await agent.upsert.fn(name="  digest  ", spec=SPEC)

    assert created["name"] == "digest"


@pytest.mark.asyncio
async def test_run_input_is_capped(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="input is longer than 200000 characters"):
            await agent.run.fn(input="x" * 200_001, spec=SPEC)


@pytest.mark.asyncio
async def test_catalog_lists_only_your_external_servers(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        catalog = await agent.catalog.fn()

    assert catalog["mcp_servers"] == []


@pytest.mark.asyncio
async def test_duplicate_names_are_refused(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.id):
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

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=f"unknown agent definition {d.id}"):
            await agent.fetch.fn(definition_id=d.id)
        with pytest.raises(ValueError, match=f"unknown agent definition {d.id}"):
            await agent.run.fn(input="hi", definition_id=d.id)


@pytest.mark.asyncio
async def test_run_a_definition_and_an_ad_hoc_spec(
    db_session, regular_user, user_session, fake_providers, no_celery_dispatch
):
    with mcp_auth_context(user_session.id):
        d = await agent.upsert.fn(name="digest", spec=SPEC)
        by_definition = await agent.run.fn(input="hi", definition_id=d["id"])
        ad_hoc = await agent.run.fn(input="hi", spec=SPEC)

    runs = {r.id: r for r in db_session.query(AgentRun)}
    assert runs[by_definition["run_id"]].definition_id == d["id"]
    assert runs[ad_hoc["run_id"]].definition_id is None
    assert {r.trigger_kind for r in runs.values()} == {"manual"}
    assert no_celery_dispatch.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"definition_id": 1, "spec": SPEC}])
async def test_run_needs_exactly_one_source(
    db_session, user_session, fake_providers, kwargs
):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="exactly one of definition_id or spec"):
            await agent.run.fn(input="hi", **kwargs)


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
async def test_one_time_keys_cannot_manage_agents(
    db_session, regular_user, fake_providers
):
    # verify_token has already spent the key by the time a tool runs.
    key = APIKey.create(user_id=regular_user.id, key_type=APIKeyType.ONE_TIME)
    key.revoked = True
    db_session.add(key)
    db_session.commit()

    with mcp_auth_context(key.key, scopes=["agents"]):
        with pytest.raises(ValueError, match="one-time API keys can't manage agents"):
            await agent.catalog.fn()


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

    with mcp_auth_context(user_session.id):
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

    with mcp_auth_context(user_session.id):
        result = await agent.wait.fn(run_id=r.id, after_seq=after_seq)

    assert [s["seq"] for s in result["steps"]] == expected
    assert result["run"]["spec"] == SPEC


@pytest.mark.asyncio
@pytest.mark.parametrize("after_seq,expected", [(-1, [0, 1]), (1, [2])])
async def test_wait_pages_long_transcripts(
    db_session, regular_user, user_session, after_seq, expected
):
    r = seeded_run(db_session, regular_user, status="done")

    with mcp_auth_context(user_session.id):
        result = await agent.wait.fn(run_id=r.id, after_seq=after_seq, limit=2)

    assert [s["seq"] for s in result["steps"]] == expected


@pytest.mark.asyncio
async def test_other_users_runs_are_unknown(db_session, admin_user, user_session):
    r = seeded_run(db_session, admin_user)

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=f"unknown run {r.id}"):
            await agent.wait.fn(run_id=r.id)


@pytest.mark.asyncio
async def test_cancel_pending_revokes_and_cancels_immediately(
    db_session, regular_user, user_session
):
    r = seeded_run(db_session, regular_user, status="pending")

    with patch.object(agent.celery_app.control, "revoke") as revoke:
        with mcp_auth_context(user_session.id):
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

    with mcp_auth_context(user_session.id):
        result = await agent.cancel.fn(run_id=r.id)

    db_session.expire_all()
    found = db_session.get(AgentRun, r.id)
    assert result["status"] == "running"
    assert found is not None
    assert found.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_star_toggles(db_session, regular_user, user_session):
    r = seeded_run(db_session, regular_user)

    with mcp_auth_context(user_session.id):
        assert (await agent.star.fn(run_id=r.id, starred=True))["starred"] is True
        assert (await agent.star.fn(run_id=r.id, starred=False))["starred"] is False


@pytest.mark.asyncio
async def test_list_runs_shows_only_your_own(
    db_session, regular_user, admin_user, user_session
):
    mine = seeded_run(db_session, regular_user)
    seeded_run(db_session, admin_user)

    with mcp_auth_context(user_session.id):
        result = await agent.list_runs.fn()

    assert [r["id"] for r in result["runs"]] == [mine.id]


@pytest.mark.asyncio
async def test_catalog_lists_visible_tools_models_and_ceilings(
    db_session, admin_session
):
    with mcp_auth_context(admin_session.id):
        catalog = await agent.catalog.fn()

    names = {t["name"] for t in catalog["tools"]}
    assert "meta_get_user" in names
    assert not any(n.startswith("agent_") for n in names)
    assert "meta_create_one_time_key" not in names
    assert catalog["builtins"] == ["web_fetch", "web_search"]
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
async def test_system_agents_are_listed_with_whether_the_viewer_can_pay(
    db_session, regular_user, user_session, llm_key
):
    with mcp_auth_context(user_session.id, scopes=["agents"]):
        listed = (await agent.system_list.fn())["agents"]

    entry = next(a for a in listed if a["slug"] == "system:summarise")
    assert (entry["has_key"], entry["problem"], entry["provider"]) == (True, None, "anthropic")


@pytest.mark.asyncio
async def test_system_agents_without_a_default_key_show_the_fix(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id, scopes=["agents"]):
        listed = (await agent.system_list.fn())["agents"]

    assert {a["problem"] for a in listed} == {
        "add a default anthropic key to run this; until then it is skipped"
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
            spec={"system_prompt": "x", "model": "anthropic/m", "provider_key": "server"},
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.id, scopes=["agents"]):
        (definition,) = (await agent.list_all.fn())["definitions"]

    assert definition["problems"] == [
        "set to the server's key, which no longer exists; pick one of your keys"
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
    with mcp_auth_context(user_session.id, scopes=["agents"]):
        (listed,) = (await agent.list_all.fn())["definitions"]

    assert run.status == "pending"
    assert listed["problems"] == [
        f"tool 'gone' is no longer offered by MCP server '{connected_server.name}'"
    ]
