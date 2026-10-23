"""Cost caps: hard global ceilings that definitions and API keys may only lower.

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
from memory.common.db.models import AgentDefinition, AgentRun, APIKey


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
) -> list[Pool]:
    """The pools a run charges: always its user's, plus its API key's and definition's.

    The user pool spans every key, so minting keys can't multiply the ceiling.
    """
    ceiling = settings.AGENT_MAX_DAILY_COST_USD
    pools = [
        Pool("user pool", ceiling, spent_last_24h(db, AgentRun.user_id == user_id))
    ]
    if api_key is not None:
        pools.append(
            Pool(
                "API key pool",
                effective_cap(ceiling, api_key.agent_max_daily_cost_usd),
                spent_last_24h(db, AgentRun.api_key_id == api_key.id),
            )
        )
    if definition is not None:
        pools.append(
            Pool(
                "definition pool",
                effective_cap(ceiling, spec_daily_cap),
                spent_last_24h(db, AgentRun.definition_id == definition.id),
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
) -> Decimal:
    """The effective per-run cap, or CapExceeded naming the first exhausted pool."""
    pools = daily_pools(
        db,
        user_id=user_id,
        spec_daily_cap=spec_daily_cap,
        definition=definition,
        api_key=api_key,
    )
    for pool in pools:
        if pool.remaining <= 0:
            raise CapExceeded(
                f"{pool.label}: spent ${pool.spent:.2f} of ${pool.cap:.2f} in the last 24h; "
                "try again later or raise the cap"
            )
    per_run = effective_cap(
        settings.AGENT_MAX_RUN_COST_USD,
        run_cap,
        api_key.agent_max_run_cost_usd if api_key else None,
    )
    return min([per_run, *(p.remaining for p in pools)])
