"""Start the agent run of a due tick or timer (memory.agents.clock); agents queue."""

import logging

from sqlalchemy import func, select

from memory.agents.caps import CapExceeded, RateExceeded
from memory.agents.clock import (
    CRON,
    SENT,
    SKIPPED_LATE,
    Notices,
    Target,
    bound_key,
    fail,
    finish,
    late_details,
    target_of,
    too_late,
    utcnow,
)
from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.prebuilt import code_agent
from memory.agents.principal import standing_key
from memory.agents.spec import AgentSpec
from memory.agents.subscriptions import (
    DONE,
    SHED_BUDGET,
    SHED_RATE,
    SKIPPED_DISABLED,
    lock_subscription,
    tell_throttled,
    throttled,
    end_episode,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentDefinition, AgentRun, Event
from memory.common.db.models.agents import iso
from memory.common.db.models.events import TICK, TIMER
from memory.common.pydantic_errors import plain
from memory.common.scopes import SCOPE_AGENTS

logger = logging.getLogger(__name__)


def time_ref(db: DBSession, event: Event) -> dict:
    """A tick's ref: its time, how many ticks it stands for beyond itself, and
    since when (the last tick that was delivered)."""
    ref = {"event_id": event.id, "time": iso(event.fire_at)}
    if event.kind == TIMER:
        return ref
    since = db.scalar(
        select(func.max(Event.fire_at)).where(
            Event.kind == TICK,
            Event.subscription_id == event.subscription_id,
            Event.fire_at < event.fire_at,
            Event.result["status"].astext.in_([DONE, SENT]),
        )
    )
    missed = (event.data or {}).get("missed", 0)
    return ref | {"subscription_id": event.subscription_id, "missed": missed, "since": iso(since)}


def start(db: DBSession, event: Event, target: Target) -> AgentRun:
    agent = target.handler["agent"]
    slug = agent if isinstance(agent, str) else None
    definition = None if slug else db.get(AgentDefinition, agent)
    if slug is not None:
        spec = code_agent(db, target.owner.id, slug)
    elif definition is None or definition.user_id != target.owner.id:
        raise ValueError("its agent definition no longer exists")
    else:
        spec = AgentSpec.model_validate(definition.spec)
    bound_key(db, target)
    key = standing_key(db, target.owner, target.api_key_id, SCOPE_AGENTS)
    # Ended in the run's own transaction, which enqueue_run commits.
    finish(event, DONE)
    subscription = lock_subscription(db, event.subscription_id) if event.subscription_id else None
    if subscription is not None:
        end_episode(subscription)
    run = enqueue_run(
        db,
        user_id=target.owner.id,
        spec=spec,
        input=target.input,
        trigger=TriggerInfo(
            kind=CRON if event.kind == TICK else TIMER,
            ref=time_ref(db, event),
            dedupe_key=f"event:{event.id}",
            subscription_id=event.subscription_id,
        ),
        definition=definition,
        agent=slug,
        api_key=key,
    )
    event.result = {"status": DONE, "run_id": run.id, **late_details(event, utcnow())}
    db.commit()
    return run


def start_time_run(event_id: int) -> str | None:
    """Enqueue the run of a tick's or timer's agent handler and end the event;
    returns the run id. A redelivery finds the event ended and does nothing, and
    the run's dedupe key makes a resend after a crash reuse the run.

    A tick or timer that waited past MAX_LATENESS is skipped; a cap that stops
    the run sheds it, telling a timer's owner, and a tick's once per throttling
    episode; a definition, key or scope that's gone fails it, and trips a subscription.
    """
    with make_session() as db:
        event = db.get(Event, event_id, with_for_update=True)
        if event is None or event.routed_at is not None:
            return None
        target = target_of(db, event)
        if target is None:
            finish(event, SKIPPED_DISABLED)
            db.commit()
            return None
        if too_late(event, target, utcnow()):
            finish(event, SKIPPED_LATE, **late_details(event, utcnow()))
            db.commit()
            return None
        try:
            return start(db, event, target).id
        except CapExceeded as e:
            db.rollback()
            event = db.get_one(Event, event_id, with_for_update=True)
            reason = str(e)
            finish(event, SHED_RATE if isinstance(e, RateExceeded) else SHED_BUDGET, error=reason)
            subscription = lock_subscription(db, event.subscription_id) if event.subscription_id else None
            episode = subscription if subscription is not None and throttled(subscription, reason) else None
            shed_timers = [(target.owner.id, event.id, reason)] if event.kind == TIMER else []
            db.commit()
            Notices(failed_timers=shed_timers).tell(db)
            if episode:
                tell_throttled(db, episode, reason)
        # An invalid spec and revoked credentials are ValueErrors.
        except ValueError as e:
            db.rollback()
            event = db.get_one(Event, event_id, with_for_update=True)
            notices = Notices()
            fail(db, event, target, plain(e), notices)
            db.commit()
            notices.tell(db)
        return None
