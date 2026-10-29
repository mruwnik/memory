"""Cost caps.

Runs pay with their owner's provider key, so only the caps the user set (the
agent's, the API key's, the provider key's) and the default per-run and daily
caps apply.

Concurrent runs that start against the same remaining amount can overshoot a
daily pool by at most (concurrency - 1) x the per-run cap; that's accepted
rather than locking.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from memory.common import settings
from memory.common.db.connection import DBSession
from memory.common.db.models import AgentDefinition, AgentRun, APIKey, LLMProviderKey, LLMSpend
from memory.common.db.models.agents import TERMINAL_STATUSES
from memory.common.llms.neutral import AgentUsage


class CapExceeded(ValueError):
    pass


def effective_cap(ceiling: Decimal, *values: Decimal | None) -> Decimal:
    """Strictest wins; unset values don't participate."""
    return min([ceiling, *(v for v in values if v is not None)])


def per_run_cap(*values: Decimal | None) -> Decimal:
    """The strictest of `values` (the agent's cap first); an agent without one is held
    to AGENT_DEFAULT_USER_KEY_RUN_COST_USD."""
    spec_cap, *others = values
    return effective_cap(spec_cap or settings.AGENT_DEFAULT_USER_KEY_RUN_COST_USD, *others)


@dataclass(frozen=True)
class Pool:
    label: str
    cap: Decimal
    spent: Decimal

    @property
    def remaining(self) -> Decimal:
        return max(self.cap - self.spent, Decimal(0))


def spent_last_24h(db: DBSession, *conditions) -> Decimal:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    total = (
        db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0))
        .filter(AgentRun.created_at > since, *conditions)
        .scalar()
    )
    return Decimal(total)


def tree_spent_last_24h(db: DBSession, *conditions) -> Decimal:
    """`spent_last_24h` of the matching runs plus every run they started, at any depth."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    roots = select(AgentRun.id).where(AgentRun.created_at > since, *conditions)
    tree = roots.cte("tree", recursive=True)
    # UNION, not UNION ALL: a descendant that also matches is counted once.
    tree = tree.union(select(AgentRun.id).where(AgentRun.parent_run_id == tree.c.id))
    total = (
        db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0))
        .filter(AgentRun.id.in_(select(tree.c.id)))
        .scalar()
    )
    return Decimal(total)


def children_cost(db: DBSession, run_id: str) -> Decimal:
    """What `run_id`'s children count against its cap: a finished child what it and its
    children spent, a live one at least its own cap, which it may still spend."""
    total = Decimal(0)
    for child in db.query(AgentRun).filter(AgentRun.parent_run_id == run_id):
        spent = child.cost_usd + children_cost(db, child.id)
        total += spent if child.status in TERMINAL_STATUSES else max(spent, child.max_cost_usd)
    return total


def hour_of(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def record_spend(
    db: DBSession,
    *,
    key_id: int,
    user_id: int | None,
    agent: str,
    model: str,
    cost: Decimal,
    usage: AgentUsage,
) -> None:
    """Add one call to its bucket for this hour; the caller commits. `user_id` is who the
    call was made for, which on a shared system key is not the key's owner."""
    tokens = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
    }
    stmt = insert(LLMSpend).values(
        provider_key_id=key_id,
        user_id=user_id,
        agent=agent,
        model=model,
        hour=hour_of(datetime.now(timezone.utc)),
        cost_usd=cost,
        calls=1,
        **tokens,
    )
    added = {
        name: getattr(LLMSpend, name) + stmt.excluded[name]
        for name in ["cost_usd", "calls", *tokens]
    }
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["provider_key_id", "user_id", "agent", "model", "hour"],
            index_where=LLMSpend.provider_key_id.is_not(None),
            set_=added,
        )
    )


def spend_last_24h(db: DBSession, key_id: int) -> Decimal:
    """Spend outside runs; the bucket the window starts in counts whole."""
    since = hour_of(datetime.now(timezone.utc) - timedelta(hours=24))
    total = (
        db.query(func.coalesce(func.sum(LLMSpend.cost_usd), 0))
        .filter(LLMSpend.provider_key_id == key_id, LLMSpend.hour >= since)
        .scalar()
    )
    return Decimal(total)


def daily_pools(
    db: DBSession,
    *,
    spec_daily_cap: Decimal | None,
    definition: AgentDefinition | None,
    api_key: APIKey | None,
    provider_key: LLMProviderKey,
) -> list[Pool]:
    """The pools a run charges: each daily cap the user set (definition, API key, provider
    key), counting all spend through it. A provider key with no daily cap, on an agent with
    none, gets AGENT_DEFAULT_USER_KEY_DAILY_COST_USD."""
    pools = []
    if api_key is not None and api_key.agent_max_daily_cost_usd is not None:
        pools.append(
            Pool(
                "API key pool",
                api_key.agent_max_daily_cost_usd,
                spent_last_24h(db, AgentRun.api_key_id == api_key.id),
            )
        )
    if definition is not None and spec_daily_cap is not None:
        pools.append(
            Pool(
                "definition pool",
                spec_daily_cap,
                # Runs a definition's runs started are paid out of its pool too.
                tree_spent_last_24h(db, AgentRun.definition_id == definition.id),
            )
        )
    key_cap = provider_key.max_cost_usd_per_day
    if key_cap is None and (definition is None or spec_daily_cap is None):
        key_cap = settings.AGENT_DEFAULT_USER_KEY_DAILY_COST_USD
    if key_cap is not None:
        pools.append(
            Pool(
                "provider key pool",
                key_cap,
                spent_last_24h(db, AgentRun.provider_key_id == provider_key.id)
                + spend_last_24h(db, provider_key.id),
            )
        )
    return pools


def key_allowance(db: DBSession, key: LLMProviderKey) -> Decimal:
    """What `key` may still spend today on calls outside agent runs."""
    pools = daily_pools(db, spec_daily_cap=None, definition=None, api_key=None, provider_key=key)
    for pool in pools:
        if pool.remaining <= 0:
            raise CapExceeded(
                f"{pool.label}: spent ${pool.spent:.2f} of ${pool.cap:.2f} in the last 24h; "
                "try again later or raise the key's daily cap"
            )
    return min(p.remaining for p in pools)


def resolve_run_cap(
    db: DBSession,
    *,
    run_cap: Decimal | None,
    spec_daily_cap: Decimal | None,
    definition: AgentDefinition | None,
    api_key: APIKey | None,
    provider_key: LLMProviderKey,
) -> Decimal:
    """The effective per-run cap, or CapExceeded naming the first exhausted pool."""
    pools = daily_pools(
        db,
        spec_daily_cap=spec_daily_cap,
        definition=definition,
        api_key=api_key,
        provider_key=provider_key,
    )
    for pool in pools:
        if pool.remaining <= 0:
            raise CapExceeded(
                f"{pool.label}: spent ${pool.spent:.2f} of ${pool.cap:.2f} in the last 24h; "
                "try again later or raise the cap"
            )
    per_run = per_run_cap(run_cap, api_key.agent_max_run_cost_usd if api_key else None)
    return min([per_run, *(p.remaining for p in pools)])


def check_run_rate(db: DBSession, definition: AgentDefinition, spec_rate: int | None) -> None:
    """CapExceeded once a definition has started its hourly allowance of runs."""
    limit = min(settings.AGENT_MAX_RUNS_PER_HOUR, spec_rate or settings.AGENT_MAX_RUNS_PER_HOUR)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    started = (
        db.query(func.count(AgentRun.id))
        .filter(AgentRun.definition_id == definition.id, AgentRun.created_at > since)
        .scalar()
    )
    if started >= limit:
        raise CapExceeded(
            f"definition rate: {started} runs in the last hour, limit {limit} "
            "(max_runs_per_hour / AGENT_MAX_RUNS_PER_HOUR); try again later or raise it"
        )
