"""What subscriptions have been doing, for their listing."""

from typing import Any, cast

from sqlalchemy import func, select

from memory.agents.subscriptions import DONE, EXPIRED, PENDING, RAW_WAITING, SHED_BACKLOG, SHED_RATE
from memory.common.db.connection import DBSession
from memory.common.db.models import AgentRun, Subscription, Delivery, APIKey, Event
from memory.common.db.models.agents import RunStatus, iso
from memory.common.db.models.events import RAW, TICK


def run_outcome(run: AgentRun) -> str:
    """A raw delivery's status as a filter delivery's reads: done once claimed, or
    why it ended unclaimed."""
    if run.error in (SHED_RATE, SHED_BACKLOG, EXPIRED):
        return cast(str, run.error)
    return DONE if run.started_at is not None else run.status


def last_deliveries(db: DBSession, ids: list[int]) -> dict[int, dict[str, Any]]:
    """Each subscription's latest ended delivery, or latest delivered tick."""
    newest_match = (
        select(Delivery, AgentRun.status)
        .outerjoin(AgentRun, AgentRun.id == Delivery.run_id)
        .where(Delivery.subscription_id.in_(ids), Delivery.status != PENDING)
        .distinct(Delivery.subscription_id)
        .order_by(Delivery.subscription_id, Delivery.created_at.desc())
    )
    last = {
        m.subscription_id: {"status": m.status, "at": iso(m.created_at), "run_id": m.run_id, "run_status": status}
        for m, status in db.execute(newest_match).tuples()
    }
    newest_tick = (
        select(Event, AgentRun.status)
        .outerjoin(AgentRun, AgentRun.id == Event.result["run_id"].astext)
        .where(Event.kind == TICK, Event.subscription_id.in_(ids), Event.result.is_not(None))
        .distinct(Event.subscription_id)
        .order_by(Event.subscription_id, Event.fire_at.desc())
    )
    # A raw delivery is its run: the latest one that ended.
    newest_raw = (
        select(AgentRun)
        .where(
            AgentRun.subscription_id.in_(ids),
            AgentRun.trigger_kind == RAW,
            AgentRun.status.not_in([RunStatus.PENDING.value, RunStatus.RUNNING.value]),
        )
        .distinct(AgentRun.subscription_id)
        .order_by(AgentRun.subscription_id, AgentRun.created_at.desc())
    )
    for run in db.scalars(newest_raw):
        last[cast(int, run.subscription_id)] = {
            "status": run_outcome(run),
            "at": iso(run.created_at),
            "run_id": run.id,
            "run_status": run.status,
        }
    for tick, status in db.execute(newest_tick).tuples():
        result = tick.result or {}
        last[cast(int, tick.subscription_id)] = {
            "status": result.get("status"),
            "at": iso(tick.fire_at),
            "run_id": result.get("run_id"),
            "run_status": status,
        }
    return last


def subscription_activity(
    db: DBSession, subscriptions: list[Subscription], caller_key: APIKey | None
) -> dict[int, dict[str, Any]]:
    """By subscription id: `api_key_name`, the key it was set up through, and
    `activity`: its `pending` deliveries, the oldest's time and its `last` delivery.
    A key learns only its own name; a login session, every key's."""
    ids = [t.id for t in subscriptions]
    key_ids = [
        t.api_key_id
        for t in subscriptions
        if t.api_key_id is not None and (caller_key is None or t.api_key_id == caller_key.id)
    ]
    key_names = dict(db.execute(select(APIKey.id, APIKey.name).where(APIKey.id.in_(key_ids))).tuples().all())
    waiting = (
        select(Delivery.subscription_id, func.count(), func.min(Delivery.created_at))
        .where(Delivery.subscription_id.in_(ids), Delivery.status == PENDING)
        .group_by(Delivery.subscription_id)
    )
    raw_waiting = (
        select(AgentRun.subscription_id, func.count(), func.min(AgentRun.created_at))
        .where(AgentRun.subscription_id.in_(ids), *RAW_WAITING)
        .group_by(AgentRun.subscription_id)
    )
    # A subscription has deliveries of one kind only, filter or raw.
    backlogs = {
        cast(int, tid): (n, oldest)
        for query in (waiting, raw_waiting)
        for tid, n, oldest in db.execute(query).tuples()
    }
    last = last_deliveries(db, ids)
    return {
        t.id: {
            "api_key_name": key_names.get(t.api_key_id) if t.api_key_id is not None else None,
            "activity": {
                "pending": backlogs.get(t.id, (0, None))[0],
                "oldest_pending_at": iso(backlogs.get(t.id, (0, None))[1]),
                "last": last.get(t.id),
            },
        }
        for t in subscriptions
    }
