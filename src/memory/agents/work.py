"""Runs placed on workers: lapsed leases, placement fallbacks, and runs nobody claimed."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import cast

from sqlalchemy import CursorResult, func, update

from memory.agents.enqueue import dispatch, fall_back_to_server
from memory.common import settings
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentRun
from memory.common.db.models.agents import (
    EXECUTOR_SERVER,
    EXECUTOR_WORKERS,
    MAX_RETENTION_DAYS,
    RunStatus,
)

logger = logging.getLogger(__name__)

HELD_OUT = "held by workers for AGENT_WORK_HOLD_MAX_SEC without a result"


def release_expired_leases(db: DBSession) -> int:
    """Put runs whose lease ran out back up for claiming. Fails them instead once their
    leases have lapsed AGENT_WORK_MAX_LAPSES times. Fails any run workers have held,
    lapsed or not, AGENT_WORK_HOLD_MAX_SEC past its first claim. The caller commits."""
    now = datetime.now(timezone.utc)
    held_out = (
        AgentRun.status.in_([RunStatus.PENDING.value, RunStatus.RUNNING.value]),
        AgentRun.executor == EXECUTOR_WORKERS,
        AgentRun.started_at
        + func.make_interval(0, 0, 0, 0, 0, 0, settings.AGENT_WORK_HOLD_MAX_SEC)
        <= now,
    )
    expired = (
        AgentRun.status == RunStatus.RUNNING.value,
        AgentRun.executor == EXECUTOR_WORKERS,
        AgentRun.lease_expires_at < now,
    )
    exhausted = AgentRun.lease_lapses + 1 >= settings.AGENT_WORK_MAX_LAPSES
    failed = {"status": RunStatus.FAILED.value, "finished_at": now}
    statements = (
        update(AgentRun).where(*held_out).values(**failed, error=HELD_OUT),
        update(AgentRun)
        .where(*expired, exhausted)
        .values(
            **failed,
            lease_lapses=AgentRun.lease_lapses + 1,
            error=f"lease lapsed {settings.AGENT_WORK_MAX_LAPSES} times without a result",
        ),
        update(AgentRun)
        .where(*expired, ~exhausted)
        .values(status=RunStatus.PENDING.value, lease_lapses=AgentRun.lease_lapses + 1),
    )
    return sum(cast(CursorResult, db.execute(s)).rowcount for s in statements)


def due_to_move(db: DBSession, placement: str, executor: str) -> list[AgentRun]:
    """Pending `placement` runs nobody ever claimed, still on their first side
    fallback_after minutes after they were placed there. A claimed run whose lease
    lapsed stays put: its worker may still be executing it."""
    minutes = AgentRun.spec["fallback_after"].as_integer()
    return (
        db.query(AgentRun)
        .filter(
            AgentRun.status == RunStatus.PENDING.value,
            AgentRun.executor == executor,
            AgentRun.worker_user_id.is_(None),
            AgentRun.spec["placement"].astext == placement,
            AgentRun.spec["fallback_after"].astext.is_not(None),
            AgentRun.placed_at + func.make_interval(0, 0, 0, 0, 0, minutes)
            < func.now(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )


def unclaimed_past_retention(db: DBSession) -> list[AgentRun]:
    retention = func.least(
        func.coalesce(
            AgentRun.spec["retention_days"].as_integer(),
            settings.AGENT_RUN_RETENTION_DAYS,
        ),
        MAX_RETENTION_DAYS,
    )
    return (
        db.query(AgentRun)
        .filter(
            AgentRun.status == RunStatus.PENDING.value,
            AgentRun.executor == EXECUTOR_WORKERS,
            AgentRun.created_at + func.make_interval(0, 0, 0, retention) < func.now(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )


def move_to_workers(run: AgentRun) -> None:
    """Workers spend their own credentials, so the run drops its server budget and
    looks as it would had it been placed on workers from the start."""
    run.executor, run.placed_at = EXECUTOR_WORKERS, datetime.now(timezone.utc)
    run.max_cost_usd, run.provider_key_id = Decimal(0), None


def sweep_worker_runs() -> dict[str, int]:
    with make_session() as db:
        lapsed = release_expired_leases(db)
        db.commit()

    with make_session() as db:
        to_workers = due_to_move(db, "server_first", EXECUTOR_SERVER)
        for run in to_workers:
            move_to_workers(run)
        db.commit()

    with make_session() as db:
        to_server = due_to_move(db, "runner_first", EXECUTOR_WORKERS)
        for run in to_server:
            fall_back_to_server(db, run)
        moved = [run.id for run in to_server if run.executor == EXECUTOR_SERVER]
        db.commit()
    for run_id in moved:
        dispatch(run_id)

    with make_session() as db:
        expired = unclaimed_past_retention(db)
        now = datetime.now(timezone.utc)
        for run in expired:
            run.status, run.finished_at = RunStatus.FAILED.value, now
            run.error = "no worker claimed it within its retention"
        db.commit()

    counts = {
        "lapsed": lapsed,
        "to_workers": len(to_workers),
        "to_server": len(to_server),
        "expired": len(expired),
    }
    if any(counts.values()):
        logger.info("swept worker runs: %s", counts)
    return counts
