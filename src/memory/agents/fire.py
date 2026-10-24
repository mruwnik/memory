"""Start a run for matched ingest-trigger items, tripping the trigger's breaker on caps."""

import logging
from datetime import datetime, timezone

from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.principal import PrincipalRevoked, standing_key
from memory.agents.spec import MAX_TEXT_CHARS, AgentSpec
from memory.agents.triggers import (
    INGEST,
    Delivery,
    IngestConfig,
    accessible_ids,
    dedupe_key,
    edited_ids,
    render_input,
)
from memory.common.notify import notify_user_now
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentTrigger, SourceItem, User
from memory.common.scopes import SCOPE_AGENTS

logger = logging.getLogger(__name__)


def trip(db: DBSession, trigger_id: int, owner: User, reason: str) -> bool:
    """Disable the trigger and tell its owner, rather than silently dropping runs.

    Only the first of several concurrent fires to hit a cap trips it (and notifies);
    returns whether this call did.
    """
    trigger = db.get(AgentTrigger, trigger_id, with_for_update=True, populate_existing=True)
    if trigger is None or not trigger.enabled:
        db.commit()
        return False
    trigger.enabled = False
    trigger.state = (trigger.state or {}) | {
        "tripped": {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}
    }
    db.commit()
    name = trigger.definition.name
    logger.warning("agent trigger %s (%s) tripped: %s", trigger.id, name, reason)
    try:
        notify_user_now(
            db,
            owner,
            subject=f"Agent trigger paused: {name}",
            message=(
                f"Trigger {trigger.id} of agent '{name}' was disabled: {reason}\n"
                "Re-enable it on the agent's page once that's resolved."
            ),
        )
    except Exception:
        logger.exception("could not notify user %s about trigger %s", owner.id, trigger.id)
    return True


def fire_trigger(trigger_id: int, deliveries: list[str]) -> str | None:
    """Enqueue one run for these encoded Deliveries; returns its id, or None if nothing ran."""
    claimed = [Delivery.decode(d) for d in deliveries]
    with make_session() as db:
        trigger = db.get(AgentTrigger, trigger_id, with_for_update=True)
        if trigger is None or not trigger.enabled or trigger.kind != INGEST:
            return None
        definition = trigger.definition
        owner = db.get(User, definition.user_id)
        if owner is None:
            return None
        allowed = accessible_ids(db, owner, [d.item_id for d in claimed])
        if not allowed:
            return None
        delivered = [d for d in claimed if d.item_id in allowed]
        items = db.query(SourceItem).filter(SourceItem.id.in_(allowed)).order_by(SourceItem.id).all()
        config = IngestConfig.model_validate(trigger.config)
        try:
            if trigger.via_api_key and trigger.api_key_id is None:
                raise PrincipalRevoked("the API key that set up this trigger was deleted")
            key = standing_key(db, owner, trigger.api_key_id, SCOPE_AGENTS)
            run = enqueue_run(
                db,
                user_id=owner.id,
                spec=AgentSpec.model_validate(definition.spec),
                input=render_input(
                    config.template, items, MAX_TEXT_CHARS, edited_ids(delivered)
                ),
                trigger=TriggerInfo(
                    kind=INGEST,
                    ref={"trigger_id": trigger.id, "item_ids": allowed},
                    dedupe_key=dedupe_key(trigger.id, delivered),
                ),
                definition=definition,
                api_key=key,
            )
        # Caps, an invalid spec and revoked credentials (all ValueErrors) trip the
        # breaker: a visible stop beats silently dropping every later match.
        except ValueError as e:
            db.rollback()
            trip(db, trigger_id, owner, str(e))
            return None
        return run.id
