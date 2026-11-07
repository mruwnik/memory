"""What subscriptions have been doing, for their listing."""

from typing import Any, cast

from sqlalchemy import func, select

from memory.agents.triggers import PENDING
from memory.common.db.connection import DBSession
from memory.common.db.models import AgentRun, AgentTrigger, AgentTriggerMatch, APIKey, Event
from memory.common.db.models.agents import iso
from memory.common.db.models.events import TICK


def last_deliveries(db: DBSession, ids: list[int]) -> dict[int, dict[str, Any]]:
    """Each subscription's latest ended delivery, or latest delivered tick."""
    newest_match = (
        select(AgentTriggerMatch, AgentRun.status)
        .outerjoin(AgentRun, AgentRun.id == AgentTriggerMatch.run_id)
        .where(AgentTriggerMatch.trigger_id.in_(ids), AgentTriggerMatch.status != PENDING)
        .distinct(AgentTriggerMatch.trigger_id)
        .order_by(AgentTriggerMatch.trigger_id, AgentTriggerMatch.created_at.desc())
    )
    last = {
        m.trigger_id: {"status": m.status, "at": iso(m.created_at), "run_id": m.run_id, "run_status": status}
        for m, status in db.execute(newest_match).tuples()
    }
    newest_tick = (
        select(Event, AgentRun.status)
        .outerjoin(AgentRun, AgentRun.id == Event.result["run_id"].astext)
        .where(Event.kind == TICK, Event.trigger_id.in_(ids), Event.result.is_not(None))
        .distinct(Event.trigger_id)
        .order_by(Event.trigger_id, Event.fire_at.desc())
    )
    for tick, status in db.execute(newest_tick).tuples():
        result = tick.result or {}
        last[cast(int, tick.trigger_id)] = {
            "status": result.get("status"),
            "at": iso(tick.fire_at),
            "run_id": result.get("run_id"),
            "run_status": status,
        }
    return last


def subscription_activity(
    db: DBSession, subscriptions: list[AgentTrigger], caller_key: APIKey | None
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
        select(AgentTriggerMatch.trigger_id, func.count(), func.min(AgentTriggerMatch.created_at))
        .where(AgentTriggerMatch.trigger_id.in_(ids), AgentTriggerMatch.status == PENDING)
        .group_by(AgentTriggerMatch.trigger_id)
    )
    backlogs = {tid: (n, oldest) for tid, n, oldest in db.execute(waiting).tuples()}
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
