"""Cron trigger: a ScheduledTask of type agent_run starts a run of its definition."""

import logging
from datetime import datetime, timedelta, timezone

from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.spec import AgentSpec
from memory.agents.principal import PrincipalRevoked, standing_key
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    ScheduledTask,
    TaskExecution,
    User,
)
from memory.common.db.models.scheduled_tasks import ExecutionStatus
from memory.common.scopes import SCOPE_AGENTS

logger = logging.getLogger(__name__)

# After an outage, a tick this late is dropped rather than run long after the
# moment it was meant for; the next tick runs as normal.
MAX_LATENESS = timedelta(hours=6)


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_scheduled_run(
    db: DBSession, task: ScheduledTask, execution: TaskExecution
) -> AgentRun:
    data = task.data or {}
    definition = db.get(AgentDefinition, data.get("definition_id"))
    if definition is None or definition.user_id != task.user_id:
        raise PrincipalRevoked(f"agent definition {data.get('definition_id')} no longer exists")
    user = db.get(User, task.user_id)
    if user is None:
        raise PrincipalRevoked(f"user {task.user_id} no longer exists")
    key = standing_key(db, user, data.get("api_key_id"), SCOPE_AGENTS)
    return enqueue_run(
        db,
        user_id=task.user_id,
        spec=AgentSpec.model_validate(definition.spec),
        input=data.get("input") or "",
        trigger=TriggerInfo(
            kind="cron",
            ref={"task_id": task.id, "execution_id": execution.id},
            dedupe_key=execution.id,
        ),
        definition=definition,
        api_key=key,
    )


def finish(
    db: DBSession, execution_id: str, status: ExecutionStatus, **fields
) -> None:
    execution = db.get(TaskExecution, execution_id)
    if execution is None:
        return
    execution.status = status
    execution.finished_at = utcnow_naive()
    execution.started_at = execution.started_at or execution.finished_at
    for name, value in fields.items():
        setattr(execution, name, value)
    db.commit()


def fail(db: DBSession, execution_id: str, error: Exception) -> None:
    db.rollback()
    finish(db, execution_id, ExecutionStatus.FAILED, error_message=str(error))
    if not isinstance(error, PrincipalRevoked):
        return
    execution = db.get(TaskExecution, execution_id)
    if execution is not None:
        execution.task.enabled = False
        db.commit()


def start_cron_run(execution_id: str) -> str | None:
    """Enqueue the run for one pending agent_run execution; returns the run id.

    The execution stays pending (and row-locked) until the run exists, so a
    worker dying mid-way leaves it for stuck-pending recovery, and the run's
    dedupe key (the execution id) makes the re-dispatch reuse the same run.
    Errors starting the run are recorded on the execution, and a schedule that
    can never run again (definition, key or scope gone) is disabled.
    """
    with make_session() as db:
        execution = db.get(TaskExecution, execution_id, with_for_update=True)
        if execution is None or execution.status != ExecutionStatus.PENDING:
            return None
        started = (
            db.query(AgentRun)
            .filter(AgentRun.trigger_kind == "cron", AgentRun.dedupe_key == execution_id)
            .first()
        )
        if started is not None:
            complete(db, execution_id, started.id)
            return started.id
        late = utcnow_naive() - execution.scheduled_time
        if late > MAX_LATENESS:
            finish(
                db,
                execution_id,
                ExecutionStatus.FAILED,
                error_message=f"skipped: {late} late (limit {MAX_LATENESS})",
            )
            return None
        try:
            run = enqueue_scheduled_run(db, execution.task, execution)
        except Exception as e:
            logger.warning("cron agent run for execution %s failed to start: %s", execution_id, e)
            fail(db, execution_id, e)
            return None
        complete(db, execution_id, run.id)
        return run.id


def complete(db: DBSession, execution_id: str, run_id: str) -> None:
    finish(db, execution_id, ExecutionStatus.COMPLETED, data={"run_id": run_id})
