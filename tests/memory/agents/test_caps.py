from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.agents.caps import (
    CapExceeded,
    daily_pools,
    effective_cap,
    key_allowance,
    per_run_cap,
    record_spend,
    spend_last_24h,
)
from memory.common import settings
from memory.common.db.models import LLMProviderKey, LLMSpend

D = Decimal


@pytest.mark.parametrize(
    "values,expected",
    [
        ((None, None), D("1")),
        ((D("0.25"), None), D("0.25")),
        ((D("5"), None), D("1")),
        ((D("0.5"), D("0.2"), D("0.9")), D("0.2")),
    ],
)
def test_effective_cap_strictest_wins_and_unset_is_ignored(values, expected):
    assert effective_cap(D("1"), *values) == expected


@pytest.mark.parametrize(
    "values,expected",
    [
        ((None,), D("1.00")),  # AGENT_DEFAULT_USER_KEY_RUN_COST_USD
        ((D("3"),), D("3")),  # an agent's own cap is not held to any server ceiling
        ((D("3"), D("0.5")), D("0.5")),
    ],
)
def test_per_run_cap_is_the_agents_cap_or_the_user_key_default(monkeypatch, values, expected):
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_RUN_COST_USD", D("1.00"))
    assert per_run_cap(*values) == expected


def make_key(db, user, cap=None):
    key = LLMProviderKey(user_id=user.id, provider="anthropic", label="k", max_cost_usd_per_day=cap)
    key.key = "sk-test"
    db.add(key)
    db.commit()
    return key


def test_spend_accumulates_in_one_bucket_per_agent_and_hour(db_session, regular_user):
    key = make_key(db_session, regular_user)
    for cost in ("0.10", "0.25"):
        record_spend(db_session, key_id=key.id, agent="system:hyde", cost=D(cost))
    record_spend(db_session, key_id=key.id, agent="system:summarise", cost=D("1"))
    db_session.commit()

    buckets = {b.agent: (b.cost_usd, b.calls) for b in db_session.query(LLMSpend)}
    assert buckets == {"system:hyde": (D("0.35"), 2), "system:summarise": (D("1"), 1)}


@pytest.mark.parametrize("hours_ago,counted", [(0, D("2")), (23, D("2")), (26, D("0"))])
def test_spend_counts_for_a_rolling_day(db_session, regular_user, hours_ago, counted):
    key = make_key(db_session, regular_user)
    hour = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(
        minute=0, second=0, microsecond=0
    )
    db_session.add(
        LLMSpend(provider_key_id=key.id, agent="system:hyde", hour=hour, cost_usd=D("2"), calls=1)
    )
    db_session.commit()

    assert spend_last_24h(db_session, key.id) == counted


@pytest.mark.parametrize(
    "cap,spent,left", [(D("5"), D("1.5"), D("3.5")), (None, D("1"), D("19"))]
)
def test_key_allowance_is_the_key_pool_less_todays_spend(
    db_session, regular_user, monkeypatch, cap, spent, left
):
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_DAILY_COST_USD", D("20"))
    key = make_key(db_session, regular_user, cap)
    record_spend(db_session, key_id=key.id, agent="system:summarise", cost=spent)
    db_session.commit()

    assert key_allowance(db_session, key) == left


def test_a_spent_key_pool_refuses_more_calls(db_session, regular_user):
    key = make_key(db_session, regular_user, D("1"))
    record_spend(db_session, key_id=key.id, agent="system:summarise", cost=D("1"))
    db_session.commit()

    with pytest.raises(CapExceeded, match="provider key pool: spent \\$1.00 of \\$1.00"):
        key_allowance(db_session, key)


def test_agent_runs_see_spend_outside_runs_in_the_key_pool(db_session, regular_user):
    key = make_key(db_session, regular_user, D("5"))
    record_spend(db_session, key_id=key.id, agent="system:hyde", cost=D("2"))
    db_session.commit()

    (pool,) = daily_pools(
        db_session, spec_daily_cap=None, definition=None, api_key=None, provider_key=key
    )
    assert pool.spent == D("2")
