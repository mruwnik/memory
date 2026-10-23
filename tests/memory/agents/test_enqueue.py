from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.agents.caps import CapExceeded
from memory.agents.enqueue import SpecInvalid, TriggerInfo, enqueue_run
from memory.agents.spec import AgentSpec
from memory.common import settings
from memory.common.celery_app import RUN_AGENT
from memory.common.db.models import AgentDefinition, AgentRun, APIKey

SPEC = {"system_prompt": "x", "model": "fake-rich/m", "reasoning": "high"}
MANUAL = TriggerInfo(kind="manual")


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_RUN_COST_USD", Decimal("1.00"))
    monkeypatch.setattr(settings, "AGENT_MAX_DAILY_COST_USD", Decimal("10.00"))


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
    assert run.max_cost_usd == Decimal("1.00")
    no_celery_dispatch.assert_called_once_with(RUN_AGENT, args=[run.id], task_id=run.id)


def test_invalid_spec_is_refused_before_insert(
    db_session, regular_user, fake_providers
):
    bad = spec(model="fake-poor/m", reasoning="medium", max_output_tokens=10)

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
        (None, None, Decimal("1.00")),
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
    spent(db_session, regular_user, "9.75")

    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=spec(), input="hi", trigger=MANUAL
    )

    assert run.max_cost_usd == Decimal("0.25")


@pytest.mark.parametrize(
    "spent_today,explicit,expected",
    [
        ("0", None, 16_000),  # the default, under the 64000 model limit
        ("9.99", None, 5_000),  # $0.01 left buys 5000 tokens at $2/MTok
        ("9.99", 1_000, 1_000),  # an explicit value is kept
    ],
)
def test_unset_max_output_tokens_resolves_into_the_snapshot(
    db_session, regular_user, fake_providers, spent_today, explicit, expected
):
    spent(db_session, regular_user, spent_today)

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
        ("user_pool", "user pool: spent $10.00 of $10.00 in the last 24h"),
        ("key_pool", "API key pool: spent $2.00 of $2.00 in the last 24h"),
        ("definition_pool", "definition pool: spent $3.00 of $3.00 in the last 24h"),
    ],
)
def test_spent_pools_refuse_with_the_pool_named(
    db_session, regular_user, fake_providers, setup, expected
):
    d = definition(db_session, regular_user, max_cost_usd_per_day="3")
    k = key(db_session, regular_user, daily_cap=Decimal("2"))
    spend = {
        "user_pool": ({}, "10"),
        "key_pool": ({"api_key_id": k.id}, "2"),
        "definition_pool": ({"definition_id": d.id}, "3"),
    }[setup]
    spent(db_session, regular_user, spend[1], **spend[0])
    target = {
        "user_pool": {},
        "key_pool": {"api_key": k},
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


def test_extra_api_keys_do_not_multiply_the_users_daily_ceiling(
    db_session, regular_user, fake_providers
):
    first = key(db_session, regular_user)
    second = key(db_session, regular_user)
    spent(db_session, regular_user, "6", api_key_id=first.id)
    spent(db_session, regular_user, "4", api_key_id=second.id)

    with pytest.raises(CapExceeded, match=r"user pool: spent \$10.00 of \$10.00"):
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
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )

    run = enqueue_run(
        db_session, user_id=regular_user.id, spec=spec(), input="hi", trigger=MANUAL
    )

    assert run.max_cost_usd == Decimal("1.00")


def test_stored_cap_above_a_lowered_ceiling_is_inert(
    db_session, regular_user, fake_providers, monkeypatch
):
    k = key(db_session, regular_user, run_cap=Decimal("0.80"))
    monkeypatch.setattr(settings, "AGENT_MAX_RUN_COST_USD", Decimal("0.50"))

    run = enqueue_run(
        db_session,
        user_id=regular_user.id,
        spec=spec(max_output_tokens=1000),
        input="hi",
        trigger=MANUAL,
        api_key=k,
    )

    assert run.max_cost_usd == Decimal("0.50")


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
