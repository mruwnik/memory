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
from memory.common.llms.neutral import AgentUsage

D = Decimal
HAIKU = "anthropic/claude-haiku"


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


def make_key(db, user, cap=None, label="k"):
    key = LLMProviderKey(
        user_id=user.id, provider="anthropic", label=label, max_cost_usd_per_day=cap
    )
    key.key = "sk-test"
    db.add(key)
    db.commit()
    return key


def spend(db, key_id, agent, cost, user_id=None, model=HAIKU, usage=AgentUsage()):
    record_spend(
        db,
        key_id=key_id,
        user_id=user_id,
        agent=agent,
        model=model,
        cost=cost,
        usage=usage,
    )


def test_spend_accumulates_in_one_bucket_per_user_agent_model_and_hour(
    db_session, regular_user, admin_user
):
    key = make_key(db_session, regular_user)
    for user_id in (None, None, regular_user.id, admin_user.id):
        spend(
            db_session,
            key.id,
            "system:hyde",
            D("0.10"),
            user_id,
            usage=AgentUsage(1, 2, 3, 4),
        )
    spend(db_session, key.id, "system:hyde", D("1"), model="openai/gpt-mini")
    spend(db_session, key.id, "system:summarise", D("1"))
    db_session.commit()

    buckets = {
        (b.user_id, b.agent, b.model): (
            b.cost_usd,
            b.calls,
            b.input_tokens,
            b.output_tokens,
            b.cache_read_tokens,
            b.cache_write_tokens,
        )
        for b in db_session.query(LLMSpend)
    }
    assert buckets == {
        (None, "system:hyde", HAIKU): (D("0.2"), 2, 2, 4, 6, 8),
        (regular_user.id, "system:hyde", HAIKU): (D("0.1"), 1, 1, 2, 3, 4),
        (admin_user.id, "system:hyde", HAIKU): (D("0.1"), 1, 1, 2, 3, 4),
        (None, "system:hyde", "openai/gpt-mini"): (D("1"), 1, 0, 0, 0, 0),
        (None, "system:summarise", HAIKU): (D("1"), 1, 0, 0, 0, 0),
    }


def test_deleting_keys_keeps_their_spend_out_of_every_pool(db_session, regular_user):
    first, second = (make_key(db_session, regular_user, label=n) for n in "ab")
    # Identical buckets but for the key: both survive as history once it is gone.
    for key_id in (first.id, second.id):
        spend(db_session, key_id, "system:hyde", D("2"), regular_user.id)
    db_session.commit()
    first_id = first.id

    db_session.delete(first)
    db_session.delete(second)
    db_session.commit()

    orphans = db_session.query(LLMSpend.provider_key_id, LLMSpend.cost_usd).all()
    assert orphans == [(None, D("2")), (None, D("2"))]
    assert spend_last_24h(db_session, first_id) == D("0")


def test_deleting_a_user_keeps_the_spend_made_for_them(db_session, regular_user, admin_user):
    key = make_key(db_session, admin_user)
    spend(db_session, key.id, "system:hyde", D("2"), regular_user.id)
    db_session.commit()

    db_session.delete(regular_user)
    db_session.commit()

    assert db_session.query(LLMSpend.user_id, LLMSpend.cost_usd).all() == [(None, D("2"))]


@pytest.mark.parametrize(
    "hours_ago,kind,counted",
    [
        (0, "memory", D("2")),
        (23, "memory", D("2")),
        (26, "memory", D("0")),
        # Purged runs folded into spend already counted while they were runs
        (23, "run", D("0")),
    ],
)
def test_spend_counts_for_a_rolling_day(db_session, regular_user, hours_ago, kind, counted):
    key = make_key(db_session, regular_user)
    hour = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(
        minute=0, second=0, microsecond=0
    )
    db_session.add(
        LLMSpend(
            provider_key_id=key.id,
            agent="system:hyde",
            kind=kind,
            hour=hour,
            cost_usd=D("2"),
            calls=1,
        )
    )
    db_session.commit()

    assert spend_last_24h(db_session, key.id) == counted


@pytest.mark.parametrize("cap,spent,left", [(D("5"), D("1.5"), D("3.5")), (None, D("1"), D("19"))])
def test_key_allowance_is_the_key_pool_less_todays_spend(
    db_session, regular_user, monkeypatch, cap, spent, left
):
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_DAILY_COST_USD", D("20"))
    key = make_key(db_session, regular_user, cap)
    spend(db_session, key.id, "system:summarise", spent)
    db_session.commit()

    assert key_allowance(db_session, key) == left


def test_a_spent_key_pool_refuses_more_calls(db_session, regular_user):
    key = make_key(db_session, regular_user, D("1"))
    spend(db_session, key.id, "system:summarise", D("1"))
    db_session.commit()

    with pytest.raises(CapExceeded, match="provider key pool: spent \\$1.00 of \\$1.00"):
        key_allowance(db_session, key)


def test_agent_runs_see_spend_outside_runs_in_the_key_pool(db_session, regular_user):
    key = make_key(db_session, regular_user, D("5"))
    spend(db_session, key.id, "system:hyde", D("2"))
    db_session.commit()

    (pool,) = daily_pools(
        db_session, spec_daily_cap=None, definition=None, api_key=None, provider_key=key
    )
    assert pool.spent == D("2")
