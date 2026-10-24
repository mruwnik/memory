"""Finish scheduled Claude session executions when their container ends, then
remove exited and dead containers.

Runs in the API process because only it holds the orchestrator socket.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from memory.claude_containers.orchestrator_client import OrchestratorClient, get_orchestrator_client
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledTask, TaskExecution
from memory.common.db.models.scheduled_tasks import ExecutionStatus, TaskType
from memory.common.redis_async import get_async_redis

logger = logging.getLogger(__name__)

TICK_LOCK = "claude:maintenance:tick"
# A container removed before we saw it end (a kill, or a cleanup racing this
# tick's listing) lost its exit status, so it can't count as a success.
GONE = "gone"
ENDED = {
    "exited": ExecutionStatus.COMPLETED,
    "dead": ExecutionStatus.FAILED,
    GONE: ExecutionStatus.FAILED,
}


def candidate_executions() -> list[tuple[str, str]]:
    """(execution id, session id) of scheduled sessions whose container started."""
    with make_session() as db:
        rows = (
            db.query(TaskExecution)
            .join(ScheduledTask)
            .filter(
                ScheduledTask.task_type == TaskType.CLAUDE_SESSION,
                TaskExecution.status == ExecutionStatus.RUNNING,
                TaskExecution.finished_at.is_(None),
            )
            .all()
        )
        return [(e.id, sid) for e in rows if (sid := (e.data or {}).get("session_id"))]


def settle_executions(
    candidates: list[tuple[str, str]], statuses: dict[str, str | None], now: datetime
) -> int:
    """Finish the candidates whose container ended or outlived the timeout.

    Candidates must be read before ``statuses`` is listed: a container missing
    from the listing then really is gone, not spawned after it.
    """
    timeout = timedelta(hours=settings.CLAUDE_SESSION_TIMEOUT_HOURS)
    settled = 0
    with make_session() as db:
        for execution_id, session_id in candidates:
            execution = db.get(TaskExecution, execution_id, with_for_update={"skip_locked": True})
            if execution is None or execution.status != ExecutionStatus.RUNNING:
                continue
            container_status = statuses.get(session_id, GONE) or "unknown"
            if container_status in ENDED:
                status = ENDED[container_status]
                error = f"container {container_status}" if status == ExecutionStatus.FAILED else None
            elif execution.started_at and now - execution.started_at > timeout:
                status = ExecutionStatus.FAILED
                error = f"container still {container_status} after {timeout}; left running"
            else:
                continue
            execution.status = status
            execution.error_message = error
            execution.finished_at = now
            execution.data = {**(execution.data or {}), "container_status": container_status}
            settled += 1
        db.commit()
    return settled


async def maintenance_tick(client: OrchestratorClient, redis: aioredis.Redis) -> dict | None:
    """One pass; None when skipped (no orchestrator, or another process has it)."""
    if not os.path.exists(client.socket_path):
        return None
    lock_ttl = max(1, int(settings.CLAUDE_MAINTENANCE_INTERVAL_SEC) - 1)
    if not await redis.set(TICK_LOCK, "1", nx=True, ex=lock_ttl):
        return None
    candidates = await asyncio.to_thread(candidate_executions)
    containers = await client.list_containers()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    statuses = {c.session_id: c.status for c in containers}
    settled = await asyncio.to_thread(settle_executions, candidates, statuses, now)
    # Only exited/dead containers are removed, never running ones.
    removed = await client.cleanup_dead_containers()
    return {"settled": settled, "removed": removed}


async def maintenance_loop() -> None:
    while True:
        await asyncio.sleep(settings.CLAUDE_MAINTENANCE_INTERVAL_SEC)
        try:
            await maintenance_tick(get_orchestrator_client(), get_async_redis())
        except Exception:
            logger.exception("Claude container maintenance tick failed")


@contextlib.asynccontextmanager
async def maintenance_running() -> AsyncIterator[None]:
    if settings.CLAUDE_MAINTENANCE_INTERVAL_SEC <= 0:
        yield
        return
    socket_path = get_orchestrator_client().socket_path
    if not os.path.exists(socket_path):
        logger.warning(
            "Claude maintenance is on but %s is missing: scheduled sessions settle "
            "only at CLAUDE_SESSION_TIMEOUT_HOURS until it appears",
            socket_path,
        )
    task = asyncio.create_task(maintenance_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
