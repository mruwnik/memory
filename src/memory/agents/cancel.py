"""Stopping a run and the runs it started."""

from datetime import datetime

from memory.common.celery_app import app
from memory.common.db.connection import DBSession
from memory.common.db.models import AgentRun
from memory.common.db.models.agents import EXECUTOR_WORKERS, TERMINAL_STATUSES, RunStatus


def cancel_tree(
    db: DBSession, run: AgentRun, now: datetime, *, spare_worker_only: bool = False
) -> None:
    """Cancel `run`, if still live, and every live run it started, at any depth.
    With `spare_worker_only`, for a run that ended rather than was cancelled, children
    placed only on workers are left to finish: they spend nothing of the owner's.

    Pending runs end at once, as do worker-held ones (nothing of ours is executing
    them; the worker learns of it when it renews its lease); running ones stop at their
    next cancellation check.
    """
    if run.executor == EXECUTOR_WORKERS and run.status not in TERMINAL_STATUSES:
        run.cancel_requested_at = run.finished_at = now
        run.status = RunStatus.CANCELLED.value
        # A lapsed run is pending again, but its worker may still be executing it.
        run.error = (
            "cancelled while a worker held it"
            if run.worker_user_id
            else "cancelled before it started"
        )
    elif run.status == RunStatus.PENDING:
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
        # The spec's placement is read raw: this module must not import memory.agents.spec.
        if spare_worker_only and child.spec.get("placement") == "runner_only":
            continue
        cancel_tree(db, child, now, spare_worker_only=spare_worker_only)
