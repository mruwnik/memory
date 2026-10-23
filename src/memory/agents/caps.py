"""Cost caps.

Runs on the server's provider keys are held to hard global ceilings that
definitions and API keys may only lower. Runs on a user's own provider key
skip those ceilings: only the caps the user set (the agent's, the API key's,
the provider key's) and default per-run and daily caps apply.

Concurrent runs that start against the same remaining amount can overshoot a
daily pool by at most (concurrency - 1) x the per-run cap; that's accepted
rather than locking.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func

from memory.common import settings
from memory.common.db.connection import DBSession
from memory.common.db.models import AgentDefinition, AgentRun, APIKey, LLMProviderKey

SERVER = "server"
USER = "user"


class CapExceeded(ValueError):
    pass


def ceiling_error(
    field: str, value: Decimal | None, ceiling: Decimal, setting: str
) -> str | None:
    if value is None or value <= ceiling:
        return None
    return (
        f"{field} {value} exceeds the {setting} ceiling of {ceiling}; "
        "lower it or raise the ceiling in settings"
    )


def effective_cap(ceiling: Decimal, *values: Decimal | None) -> Decimal:
    """Strictest wins; unset values don't participate."""
    return min([ceiling, *(v for v in values if v is not None)])


def per_run_cap(on_server_key: bool, *values: Decimal | None) -> Decimal:
    """The strictest of `values` (the agent's cap first), within the pool's per-run limit.

    On the server's key that limit is AGENT_MAX_RUN_COST_USD; on a user's key it is
    the agent's own cap, or AGENT_DEFAULT_USER_KEY_RUN_COST_USD when it sets none.
    """
    if on_server_key:
        return effective_cap(settings.AGENT_MAX_RUN_COST_USD, *values)
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


def daily_pools(
    db: DBSession,
    *,
    user_id: int,
    spec_daily_cap: Decimal | None,
    definition: AgentDefinition | None,
    api_key: APIKey | None,
    provider_key: LLMProviderKey | None,
) -> list[Pool]:
    """The pools a run charges.

    On the server's key: the user pool, AGENT_MAX_DAILY_COST_USD over everything the
    user spent on the server's keys, so minting API keys or definitions can't multiply
    it. Either way: each daily cap the user set (definition, API key, provider key),
    counting all spend through it. A provider key with no daily cap, on an agent with
    none, gets AGENT_DEFAULT_USER_KEY_DAILY_COST_USD.
    """
    pools = []
    if provider_key is None:
        pools.append(
            Pool(
                "user pool",
                settings.AGENT_MAX_DAILY_COST_USD,
                spent_last_24h(
                    db, AgentRun.user_id == user_id, AgentRun.key_source == SERVER
                ),
            )
        )
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
                spent_last_24h(db, AgentRun.definition_id == definition.id),
            )
        )
    if provider_key is None:
        return pools
    key_cap = provider_key.max_cost_usd_per_day
    if key_cap is None and (definition is None or spec_daily_cap is None):
        key_cap = settings.AGENT_DEFAULT_USER_KEY_DAILY_COST_USD
    if key_cap is not None:
        pools.append(
            Pool(
                "provider key pool",
                key_cap,
                spent_last_24h(db, AgentRun.provider_key_id == provider_key.id),
            )
        )
    return pools


def resolve_run_cap(
    db: DBSession,
    *,
    user_id: int,
    run_cap: Decimal | None,
    spec_daily_cap: Decimal | None,
    definition: AgentDefinition | None,
    api_key: APIKey | None,
    provider_key: LLMProviderKey | None,
) -> Decimal:
    """The effective per-run cap, or CapExceeded naming the first exhausted pool.

    `provider_key` None means the run is on the server's key.
    """
    pools = daily_pools(
        db,
        user_id=user_id,
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
    per_run = per_run_cap(
        provider_key is None,
        run_cap,
        api_key.agent_max_run_cost_usd if api_key else None,
    )
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
