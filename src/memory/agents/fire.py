"""Start a run for an ingest trigger's pending deliveries. Caps defer and then shed
deliveries; broken credentials or an invalid definition trip the trigger's breaker."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, tuple_

from memory.agents.caps import CapExceeded, RateExceeded
from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.prebuilt import code_agent
from memory.agents.spec import MAX_TEXT_CHARS, AgentSpec
from memory.agents.trigger_input import render_input
from memory.agents.triggers import (
    DONE,
    FAILED,
    INGEST,
    PENDING,
    SHED_BUDGET,
    SHED_RATE,
    SKIPPED_INACCESSIBLE,
    SKIPPED_POSTED,
    THROTTLE_WINDOW,
    THROTTLED,
    IngestConfig,
    accessible_ids,
    changes_by_item,
    coalesced_ref,
    dedupe_key,
    delivery_ref,
    episode_over,
    lock_trigger,
    parse_ref,
    posted_among,
    tell_owner,
    tell_throttled,
    throttled,
    trigger_key,
)
from memory.common import settings
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    AgentTriggerMatch,
    SourceItem,
    User,
)

logger = logging.getLogger(__name__)


def trip(db: DBSession, trigger_id: int, owner: User, reason: str) -> bool:
    """Disable the trigger and tell its owner, rather than failing every later delivery.

    Only the first of several concurrent fires to trip it notifies; returns whether
    this call did.
    """
    trigger = lock_trigger(db, trigger_id)
    if trigger is None or not trigger.enabled:
        db.commit()
        return False
    trigger.enabled = False
    trigger.state = (trigger.state or {}) | {
        "tripped": {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}
    }
    db.commit()
    name = trigger.definition.name if trigger.definition else trigger.name
    logger.warning("agent trigger %s (%s) tripped: %s", trigger.id, name, reason)
    tell_owner(
        db,
        owner,
        subject=f"Agent trigger paused: {name}",
        message=(
            f"Trigger {trigger.id} ('{name}') was disabled: {reason}\n"
            "Re-enable it once that's resolved. Enabling it with "
            "resume also delivers what it missed while paused."
        ),
    )
    return True


def due_deliveries(db: DBSession, trigger_id: int, refs: list[str]) -> list[AgentTriggerMatch]:
    """The deliveries among `refs` still pending and not deferred, locked."""
    return list(
        db.scalars(
            select(AgentTriggerMatch)
            .where(
                AgentTriggerMatch.trigger_id == trigger_id,
                tuple_(AgentTriggerMatch.item_id, AgentTriggerMatch.content_hash).in_(
                    [parse_ref(r) for r in refs]
                ),
                AgentTriggerMatch.status == PENDING,
                or_(
                    AgentTriggerMatch.not_before.is_(None),
                    AgentTriggerMatch.not_before <= datetime.now(timezone.utc),
                ),
            )
            .order_by(AgentTriggerMatch.item_id)
            .with_for_update()
        )
    )


def handler_agent(
    db: DBSession, trigger: AgentTrigger, owner: User
) -> tuple[AgentSpec, AgentDefinition | None, str | None]:
    """The spec an agent handler runs, with its definition or code slug."""
    agent = trigger.handler["agent"]
    if isinstance(agent, str):
        return code_agent(db, owner.id, agent), None, agent
    definition = trigger.definition
    if definition is None:
        raise ValueError("its agent definition no longer exists")
    return AgentSpec.model_validate(definition.spec), definition, None


def start_run(
    db: DBSession,
    trigger: AgentTrigger,
    owner: User,
    deliveries: list[AgentTriggerMatch],
) -> str | None:
    """Enqueue the run for the deliveries the trigger can still read, marking them
    done in the run's transaction. The unreadable ones, and those a run of the
    trigger reported posting since they were routed, are skipped first, in their
    own, so a cap that stops the run can't hold them back too."""
    config = IngestConfig.model_validate(trigger.config)
    key = trigger_key(db, trigger, owner)
    allowed = set(accessible_ids(db, owner, key, [d.item_id for d in deliveries]))
    candidates = db.query(SourceItem).filter(SourceItem.id.in_(allowed)).all()
    posted = posted_among(db, trigger.id, candidates)
    skipped = {d.item_id: SKIPPED_INACCESSIBLE for d in deliveries if d.item_id not in allowed}
    skipped |= {item_id: SKIPPED_POSTED for item_id in posted}
    readable = [
        delivery_ref(d.item_id, d.content_hash) for d in deliveries if d.item_id not in skipped
    ]
    if skipped:
        for d in deliveries:
            d.status = skipped.get(d.item_id, d.status)
        db.commit()
        handler = trigger.handler
        relocked = lock_trigger(db, trigger.id)
        # Disabled, deleted or moved meanwhile: what's left waits for it, or goes with it.
        if relocked is None or not relocked.enabled or relocked.handler != handler:
            return None
    delivered = due_deliveries(db, trigger.id, readable) if readable else []
    if not delivered:
        return None
    for d in delivered:
        d.status = DONE
    if THROTTLED in trigger.state and episode_over(trigger.state, datetime.now(timezone.utc)):
        trigger.state = {k: v for k, v in trigger.state.items() if k != THROTTLED}
    item_ids = sorted(d.item_id for d in delivered)
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).order_by(SourceItem.id).all()
    changes = changes_by_item(delivered)
    spec, definition, slug = handler_agent(db, trigger, owner)
    run = enqueue_run(
        db,
        user_id=owner.id,
        spec=spec,
        input=render_input(config.template, items, MAX_TEXT_CHARS, changes),
        trigger=TriggerInfo(
            kind=INGEST,
            ref={"trigger_id": trigger.id, "item_ids": item_ids, "changes": changes}
            | coalesced_ref(delivered),
            dedupe_key=dedupe_key(trigger.id, delivered),
            trigger_id=trigger.id,
        ),
        definition=definition,
        agent=slug,
        api_key=key,
        deliveries=delivered,
    )
    # A run its dedupe key already started is linked here instead.
    for d in delivered:
        d.run_id = run.id
    db.commit()
    return run.id


def hold_back(db: DBSession, trigger_id: int, refs: list[str], cap: CapExceeded) -> None:
    """Defer the deliveries a cap stopped by THROTTLE_WINDOW, and shed those it
    stops a second time."""
    trigger = lock_trigger(db, trigger_id)
    if trigger is None:
        return
    shed_as = SHED_RATE if isinstance(cap, RateExceeded) else SHED_BUDGET
    retry_at = datetime.now(timezone.utc) + THROTTLE_WINDOW
    for d in due_deliveries(db, trigger_id, refs):
        if d.not_before is None:
            d.not_before, d.dispatched_at, d.sends = retry_at, None, 0
        else:
            d.status = shed_as
    reason = str(cap)
    episode = throttled(trigger, reason)
    db.commit()
    if episode:
        tell_throttled(db, trigger, reason)


def count_failure(
    db: DBSession,
    trigger_id: int,
    refs: list[str],
    error: Exception,
    retry_after: timedelta | None = None,
) -> None:
    """Count a failed fire against its deliveries; after EVENT_MAX_ATTEMPTS they
    fail. With `retry_after`, the rest wait that long for their next attempt."""
    lock_trigger(db, trigger_id)
    retry_at = None if retry_after is None else datetime.now(timezone.utc) + retry_after
    for d in due_deliveries(db, trigger_id, refs):
        d.attempts += 1
        if d.attempts >= settings.EVENT_MAX_ATTEMPTS:
            d.status, d.error = FAILED, type(error).__name__
        elif retry_at:
            d.not_before = retry_at
    db.commit()


def fire_trigger(trigger_id: int, refs: list[str]) -> str | None:
    """Enqueue one run for these deliveries (triggers.delivery_ref) if they are still
    due; returns its id, or None if nothing ran.

    The run's trigger ref is {"trigger_id", "item_ids": [readable ids], "changes":
    {"<item id>": [Change tags delivered for it]}}, plus triggers.coalesced_ref.
    """
    with make_session() as db:
        trigger = lock_trigger(db, trigger_id)
        # A disabled trigger's deliveries wait for it to be enabled again, or expire.
        if trigger is None or not trigger.enabled or trigger.kind != INGEST:
            return None
        owner = db.get(User, trigger.user_id)
        deliveries = due_deliveries(db, trigger_id, refs)
        if "agent" not in trigger.handler or owner is None or not deliveries:
            return None
        try:
            return start_run(db, trigger, owner, deliveries)
        except CapExceeded as e:
            db.rollback()
            hold_back(db, trigger_id, refs, e)
        # An invalid spec or config and revoked credentials (all ValueErrors) trip the
        # breaker: a visible stop beats failing every later delivery.
        except ValueError as e:
            db.rollback()
            trip(db, trigger_id, owner, str(e))
        except Exception as e:
            db.rollback()
            count_failure(db, trigger_id, refs, e)
            raise
        return None
