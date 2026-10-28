"""Stopping a run and the runs it started."""

from datetime import datetime

from memory.common.celery_app import app
from memory.common.db.connection import DBSession
from memory.common.db.models import AgentRun
from memory.common.db.models.agents import TERMINAL_STATUSES, RunStatus


def cancel_tree(db: DBSession, run: AgentRun, now: datetime) -> None:
    """Cancel `run`, if still live, and every live run it started, at any depth.

    Pending runs end at once; running ones stop at their next cancellation check.
    """
    if run.status == RunStatus.PENDING:
        # The run id is the Celery task id.
        app.control.revoke(run.id)
        # Also flagged, so a worker that already claimed it stops at its first check.
        run.cancel_requested_at = now
        run.status = RunStatus.CANCELLED.value
        run.error = "cancelled before it started"
        run.finished_at = now
    elif run.status not in TERMINAL_STATUSES:
        run.cancel_requested_at = now
    children = (
        db.query(AgentRun).filter(AgentRun.parent_run_id == run.id).with_for_update().all()
    )
    for child in children:
        cancel_tree(db, child, now)
