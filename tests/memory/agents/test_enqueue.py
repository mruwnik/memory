from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.agents.caps import CapExceeded
from memory.agents.enqueue import (
    SpecInvalid,
    TriggerInfo,
    enqueue_run,
    stored_spec_problems,
)
from memory.agents.spec import AgentSpec
from memory.common import settings
from memory.common.celery_app import RUN_AGENT
from memory.common.db.models import AgentDefinition, AgentRun, APIKey
from tests.memory.agents.test_provider_keys import MODEL, add_key

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
MANUAL = TriggerInfo(kind="manual")


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_RUN_COST_USD", Decimal("2.00"))
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_DAILY_COST_USD", Decimal("6.00"))


def spec(**overrides):
    return AgentSpec.model_validate(SPEC | overrides)


def definition(db, user, **overrides):
    d = AgentDefinition(
        user_id=user.id, name="d", spec=spec(**overrides).model_dump(mode="json")
    )
    db.add(d)
    db.commit()
    return d


def key(db, user, run_cap=None, daily_cap=None):
    k = APIKey.create(user_id=user.id, name="k")
    k.agent_max_run_cost_usd, k.agent_max_daily_cost_usd = run_cap, daily_cap
    db.add(k)
    db.commit()
    return k


def spent(db, user, cost, **overrides):
    db.add(
        AgentRun(
            user_id=user.id,
            spec=SPEC,
            input="old",
            max_cost_usd=Decimal("1"),
            trigger_kind="manual",
            cost_usd=Decimal(cost),
            status="done",
            **overrides,
        )
    )
    db.commit()


def test_enqueue_inserts_pending_and_dispatches(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=spec(), input="hi", trigger=MANUAL
    )

    assert run.status == "pending"
    assert run.spec == spec(max_output_tokens=16_000).model_dump(mode="json")
    assert run.max_cost_usd == Decimal("2.00")
    assert run.provider_key_id == fake_providers.keys["anthropic"].id
    no_celery_dispatch.assert_called_once_with(RUN_AGENT, args=[run.id], task_id=run.id)


def test_invalid_spec_is_refused_before_insert(
    db_session, regular_user, fake_providers
):
    bad = spec(model="openai/m", reasoning="medium", max_output_tokens=10)

    with pytest.raises(SpecInvalid, match="reasoning 'medium' is not supported"):
        enqueue_run(
            db_session, user_id=regular_user.id, spec=bad, input="hi", trigger=MANUAL
        )

    assert db_session.query(AgentRun).count() == 0


def test_overlong_input_is_refused_before_insert(
    db_session, regular_user, fake_providers
):
    with pytest.raises(ValueError, match="input is longer than 200000 characters"):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=spec(),
            input="x" * 200_001,
            trigger=MANUAL,
        )

    assert db_session.query(AgentRun).count() == 0


@pytest.mark.parametrize(
    "spec_cap,key_cap,expected",
    [
        (None, None, Decimal("2.00")),
        (Decimal("0.40"), None, Decimal("0.40")),
        (None, Decimal("0.30"), Decimal("0.30")),
        (Decimal("0.40"), Decimal("0.30"), Decimal("0.30")),
        (Decimal("0.20"), Decimal("0.30"), Decimal("0.20")),
    ],
)
def test_run_cap_is_the_strictest_set_value(
    db_session, regular_user, fake_providers, spec_cap, key_cap, expected
):
    k = key(db_session, regular_user, run_cap=key_cap)

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=spec(max_cost_usd=spec_cap),
        input="hi",
        trigger=MANUAL,
        api_key=k,
    )

    assert run.max_cost_usd == expected


def test_run_cap_shrinks_to_what_is_left_in_the_day(
    db_session, regular_user, fake_providers
):
    spent(db_session, regular_user, "5.75", provider_key_id=fake_providers.keys["anthropic"].id)

    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=spec(), input="hi", trigger=MANUAL
    )

    assert run.max_cost_usd == Decimal("0.25")


@pytest.mark.parametrize(
    "spent_today,explicit,expected",
    [
        ("0", None, 16_000),  # the default, under the 64000 model limit
        ("5.99", None, 5_000),  # $0.01 left buys 5000 tokens at $2/MTok
        ("5.99", 1_000, 1_000),  # an explicit value is kept
    ],
)
def test_unset_max_output_tokens_resolves_into_the_snapshot(
    db_session, regular_user, fake_providers, spent_today, explicit, expected
):
    spent(
        db_session,
        regular_user,
        spent_today,
        provider_key_id=fake_providers.keys["anthropic"].id,
    )

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=spec(max_output_tokens=explicit),
        input="hi",
        trigger=MANUAL,
    )

    assert run.spec["max_output_tokens"] == expected


@pytest.mark.parametrize(
    "setup,expected",
    [
        ("key_pool", "provider key pool: spent $6.00 of $6.00 in the last 24h"),
        ("api_key_pool", "API key pool: spent $2.00 of $2.00 in the last 24h"),
        ("definition_pool", "definition pool: spent $3.00 of $3.00 in the last 24h"),
    ],
)
def test_spent_pools_refuse_with_the_pool_named(
    db_session, regular_user, fake_providers, setup, expected
):
    d = definition(db_session, regular_user, max_cost_usd_per_day="3")
    k = key(db_session, regular_user, daily_cap=Decimal("2"))
    spend = {
        "key_pool": ({"provider_key_id": fake_providers.keys["anthropic"].id}, "6"),
        "api_key_pool": ({"api_key_id": k.id}, "2"),
        "definition_pool": ({"definition_id": d.id}, "3"),
    }[setup]
    spent(db_session, regular_user, spend[1], **spend[0])
    target = {
        "key_pool": {},
        "api_key_pool": {"api_key": k},
        "definition_pool": {"definition": d},
    }[setup]

    with pytest.raises(CapExceeded, match=expected.replace("$", r"\$")):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=spec(max_cost_usd_per_day="3"),
            input="hi",
            trigger=MANUAL,
            **target,
        )


def test_extra_api_keys_do_not_multiply_the_provider_keys_daily_cap(
    db_session, regular_user, fake_providers
):
    first = key(db_session, regular_user)
    second = key(db_session, regular_user)
    pays = fake_providers.keys["anthropic"].id
    spent(db_session, regular_user, "4", api_key_id=first.id, provider_key_id=pays)
    spent(db_session, regular_user, "2", api_key_id=second.id, provider_key_id=pays)

    with pytest.raises(CapExceeded, match=r"provider key pool: spent \$6.00 of \$6.00"):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=spec(),
            input="hi",
            trigger=MANUAL,
            api_key=second,
        )


def test_session_runs_snapshot_the_scopes_sessions_get_over_http(
    db_session, regular_user, fake_providers
):
    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=spec(), input="hi", trigger=MANUAL
    )

    assert run.scopes == ["read", "teams", "write"]


@pytest.mark.parametrize(
    "key_scopes,expected",
    [
        (None, ["teams"]),  # a key without its own scopes inherits the user's
        (["read"], ["read"]),
    ],
)
def test_key_runs_snapshot_the_keys_scopes(
    db_session, regular_user, fake_providers, key_scopes, expected
):
    k = APIKey.create(user_id=regular_user.id, name="k", scopes=key_scopes)
    db_session.add(k)
    db_session.commit()

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=spec(),
        input="hi",
        trigger=MANUAL,
        api_key=k,
    )

    assert run.scopes == expected


def test_spend_older_than_24h_does_not_count(db_session, regular_user, fake_providers):
    spent(
        db_session,
        regular_user,
        "10",
        provider_key_id=fake_providers.keys["anthropic"].id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )

    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=spec(), input="hi", trigger=MANUAL
    )

    assert run.max_cost_usd == Decimal("2.00")


def test_dedupe_key_returns_the_existing_run(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    d = definition(db_session, regular_user)
    trigger = TriggerInfo(kind="cron", dedupe_key="exec-1")

    first = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=spec(),
        input="hi",
        trigger=trigger,
        definition=d,
    )
    second = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=spec(),
        input="hi",
        trigger=trigger,
        definition=d,
    )

    assert second.id == first.id
    assert no_celery_dispatch.call_count == 1


def rated_definition(db, user, monkeypatch, spec_rate, ceiling, earlier):
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", ceiling)
    d = AgentDefinition(
        user_id=user.id, name="d", spec=SPEC | {"max_runs_per_hour": spec_rate}
    )
    db.add(d)
    db.commit()
    for _ in range(earlier):
        spent(db, user, "0", definition_id=d.id)
    # Outside the window, so never counted.
    spent(
        db,
        user,
        "0",
        definition_id=d.id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    return d


def start_definition(db, user, d):
    return enqueue_run(
        db,
        user_id=user.id,
        spec=AgentSpec.model_validate(d.spec),
        input="hi",
        trigger=MANUAL,
        definition=d,
    )


@pytest.mark.parametrize(
    "spec_rate,ceiling,earlier", [(None, 3, 2), (2, 3, 1)]
)
def test_runs_within_the_hourly_limit_start(
    db_session, regular_user, fake_providers, monkeypatch, spec_rate, ceiling, earlier
):
    d = rated_definition(db_session, regular_user, monkeypatch, spec_rate, ceiling, earlier)

    assert start_definition(db_session, regular_user, d).status == "pending"


@pytest.mark.parametrize(
    "spec_rate,ceiling,earlier,limit", [(None, 3, 3, 3), (2, 3, 2, 2)]
)
def test_runs_beyond_the_hourly_limit_are_refused(
    db_session, regular_user, fake_providers, monkeypatch, spec_rate, ceiling, earlier, limit
):
    d = rated_definition(db_session, regular_user, monkeypatch, spec_rate, ceiling, earlier)

    with pytest.raises(CapExceeded, match=f"{earlier} runs in the last hour.*limit {limit}"):
        start_definition(db_session, regular_user, d)


# --- user provider keys ---------------------------------------------------------


def own_key_spec(**overrides):
    return spec(model=MODEL, reasoning="medium", **overrides)


@pytest.mark.parametrize(
    "run_cap,expected",
    [
        (None, Decimal("2.00")),  # AGENT_DEFAULT_USER_KEY_RUN_COST_USD
        (Decimal("5"), Decimal("5")),  # an agent's own cap is not held to a ceiling
    ],
)
def test_runs_are_capped_by_the_agent_or_the_user_key_default(
    db_session, regular_user, run_cap, expected
):
    k = add_key(db_session, regular_user, is_default=True)

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=own_key_spec(max_cost_usd=run_cap),
        input="hi",
        trigger=MANUAL,
    )

    assert run.provider_key_id == k.id
    assert run.max_cost_usd == expected


def test_a_keys_daily_cap_limits_its_runs(db_session, regular_user):
    k = add_key(db_session, regular_user, "capped", max_cost_usd_per_day=Decimal("3"))
    spent(db_session, regular_user, "2.5", provider_key_id=k.id)
    pinned = own_key_spec(provider_key=k.id)

    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=pinned, input="hi", trigger=MANUAL
    )
    spent(db_session, regular_user, "0.5", provider_key_id=k.id)

    assert run.max_cost_usd == Decimal("0.5")
    with pytest.raises(CapExceeded, match=r"provider key pool: spent \$3.00 of \$3.00"):
        enqueue_run(
            db_session, user_id=regular_user.id, spec=pinned, input="hi", trigger=MANUAL
        )


@pytest.mark.parametrize(
    "key_cap,agent_cap,expected",
    [
        (None, None, Decimal("1.00")),  # AGENT_DEFAULT_USER_KEY_DAILY_COST_USD - spent
        (Decimal("10"), None, Decimal("2.00")),
        (None, "10", Decimal("2.00")),
    ],
)
def test_own_keys_get_a_default_daily_cap_the_key_or_agent_can_raise(
    db_session, regular_user, key_cap, agent_cap, expected
):
    k = add_key(db_session, regular_user, is_default=True, max_cost_usd_per_day=key_cap)
    d = definition(
        db_session,
        regular_user,
        model=MODEL,
        reasoning="medium",
        max_cost_usd_per_day=agent_cap,
    )
    spent(db_session, regular_user, "5", provider_key_id=k.id)

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=AgentSpec.model_validate(d.spec),
        input="hi",
        trigger=MANUAL,
        definition=d,
    )

    assert run.max_cost_usd == expected


def test_the_agents_own_daily_cap_counts_all_spend_through_the_definition(
    db_session, regular_user
):
    k = add_key(db_session, regular_user, is_default=True)
    d = definition(
        db_session,
        regular_user,
        model=MODEL,
        reasoning="medium",
        max_cost_usd_per_day="3",
    )
    spent(db_session, regular_user, "1.5", definition_id=d.id, provider_key_id=k.id)
    spent(db_session, regular_user, "1.5", definition_id=d.id)

    with pytest.raises(CapExceeded, match="definition pool"):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=AgentSpec.model_validate(d.spec),
            input="hi",
            trigger=MANUAL,
            definition=d,
        )


def test_own_key_runs_keep_the_api_keys_run_cap(db_session, regular_user):
    add_key(db_session, regular_user, is_default=True)
    api_key = key(db_session, regular_user, run_cap=Decimal("0.30"))

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=own_key_spec(),
        input="hi",
        trigger=MANUAL,
        api_key=api_key,
    )

    assert run.max_cost_usd == Decimal("0.30")


def test_without_a_key_for_the_models_provider_no_run_starts(db_session, regular_user):
    with pytest.raises(ValueError, match="add an API key for anthropic"):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=own_key_spec(),
            input="hi",
            trigger=MANUAL,
        )
    assert db_session.query(AgentRun).count() == 0


@pytest.mark.parametrize(
    "spec,expected",
    [
        (
            {"system_prompt": "x", "model": "anthropic/m", "provider_key": 99999},
            "is not yours or was deleted",
        ),
        (
            {
                "system_prompt": "x",
                "model": "anthropic/m",
                "mcp_servers": [{"server_id": 99999, "tools": ["t"]}],
            },
            "MCP server 99999 is not yours",
        ),
        (
            {"system_prompt": "x", "model": "anthropic/m", "bogus": "secret prompt"},
            "invalid spec: bogus: Extra inputs are not permitted",
        ),
    ],
)
def test_stored_spec_problems_name_what_stops_an_agent(
    db_session, regular_user, fake_providers, spec, expected
):
    problems = stored_spec_problems(db_session, regular_user.id, spec)

    assert any(expected in p for p in problems), problems


def test_runnable_agents_have_no_problems(db_session, regular_user, fake_providers):
    assert stored_spec_problems(db_session, regular_user.id, {"system_prompt": "x", "model": "anthropic/m"}) == []


def test_tools_a_server_stopped_offering_are_a_problem(
    db_session, regular_user, fake_providers, connected_server
):
    spec = {
        "system_prompt": "x",
        "model": "anthropic/m",
        "mcp_servers": [{"server_id": connected_server.id, "tools": ["gone"]}],
    }

    assert stored_spec_problems(db_session, regular_user.id, spec) == [
        f"tool 'gone' is no longer offered by MCP server '{connected_server.name}'"
    ]


def test_enqueue_refuses_what_stops_an_agent(db_session, regular_user, fake_providers):
    with pytest.raises(SpecInvalid, match="API key 99999 is not yours or was deleted"):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=spec(provider_key=99999),
            input="hi",
            trigger=MANUAL,
        )

    assert db_session.query(AgentRun).count() == 0
