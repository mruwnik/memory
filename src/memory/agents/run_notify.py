"""Tell owners that runs they asked to hear about (agent_run notify) have ended.

A sweep, not a hook on each way a run can end: server, worker, cancellation,
timeout and expiry all set a terminal status, and the sweep sees every one.
Worker-safe: no spec, enqueue or API imports.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from memory.common import settings
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentDefinition, AgentRun, User
from memory.common.db.models.agents import TERMINAL_STATUSES
from memory.common.notify import notify_user_now

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 500
NOTIFY_BATCH_SIZE = 100


def run_label(db: DBSession, run: AgentRun) -> str:
    if run.agent:
        return run.agent
    if run.definition_id and (definition := db.get(AgentDefinition, run.definition_id)):
        return definition.name
    return "inline agent"


def outcome_preview(run: AgentRun) -> str:
    """The start of the run's error, else of its text or structured result."""
    result = run.result or {}
    if run.error:
        text = run.error
    elif "text" in result:
        text = str(result["text"])
    elif "data" in result:
        text = json.dumps(result["data"])
    else:
        text = ""
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + "…"


def run_url(run: AgentRun) -> str:
    return f"{settings.SERVER_URL.rstrip('/')}/agents/runs/{run.id}"


def notification_for(db: DBSession, run: AgentRun) -> tuple[str, str]:
    """The subject and message telling the owner `run` ended."""
    subject = f"Agent run {run.status}: {run_label(db, run)}"
    fetch = f'agent_wait(run_id="{run.id}", result_only=true) returns the whole result.'
    preview = outcome_preview(run)
    message = f"{preview}\n\n{fetch}" if preview else fetch
    return subject, message


def notify_owner(db: DBSession, run: AgentRun) -> bool:
    """Queue the notification; False when the owner has no channel to notify on."""
    owner = db.get(User, run.user_id)
    if owner is None:
        return False
    subject, message = notification_for(db, run)
    return notify_user_now(db, owner, subject, message, run_url(run)) is not None


def claim_finished_runs() -> list[str]:
    """Mark up to a batch of ended runs that asked to notify as notified, and commit.

    Returns the ids claimed. Concurrent sweeps skip each other's rows.
    """
    pending = (
        select(AgentRun.id)
        .where(
            AgentRun.notify_on_finish,
            AgentRun.notified_at.is_(None),
            AgentRun.status.in_([s.value for s in TERMINAL_STATUSES]),
        )
        .order_by(AgentRun.finished_at)
        .limit(NOTIFY_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    claim = (
        update(AgentRun)
        .where(AgentRun.id.in_(pending.scalar_subquery()))
        .values(notified_at=datetime.now(timezone.utc))
        .returning(AgentRun.id)
        .execution_options(synchronize_session=False)
    )
    with make_session() as db:
        return list(db.execute(claim).scalars())


def notify_claimed_run(run_id: str) -> bool:
    """Notify the owner of one claimed run, in its own session."""
    with make_session() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            return False
        return notify_owner(db, run)


def notify_one(run_id: str) -> bool:
    """notify_claimed_run, logging a failure instead of raising it."""
    try:
        return notify_claimed_run(run_id)
    except Exception:
        logger.exception("could not notify the owner of run %s", run_id)
        return False


def notify_finished_runs() -> dict[str, int]:
    """Notify the owners of ended runs that asked for it, at most once each.

    Runs are claimed (notified_at set and committed) before anything is sent, so a
    failure while sending, or a crash, loses that run's notification rather than
    repeating it every sweep.
    """
    run_ids = claim_finished_runs()
    notified = sum(notify_one(run_id) for run_id in run_ids)
    return {"notified": notified, "unsent": len(run_ids) - notified}
