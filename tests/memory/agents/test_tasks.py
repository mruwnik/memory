import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.common import settings
from memory.common.celery_app import AGENTS_ROOT, app
from memory.common.db.models import AgentRun, AgentRunStep
from memory.workers.tasks import agents as agent_tasks
from memory.workers.tasks import maintenance
from memory.workers.tasks.maintenance import purge_agent_runs, sweep_agent_runs


def test_agent_tasks_route_to_the_agents_queue():
    assert app.conf.task_routes[f"{AGENTS_ROOT}.*"] == {
        "queue": f"{settings.CELERY_QUEUE_PREFIX}-agents"
    }


def test_worker_tasks_do_not_import_the_api_package_at_load_time():
    # Every worker autodiscovers the task modules; only agents-worker has the API's deps.
    api_only = ["memory.agents.runner", "memory.agents.enqueue", "fastmcp", "fastapi", "jsonschema"]
    code = (
        "import sys, memory.workers.tasks; "
        f"print(any(m in sys.modules for m in {api_only!r}))"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    assert out.stdout.strip() == "False"


def test_run_agent_executes_the_run(monkeypatch):
    seen = []

    async def fake_execute(run_id):
        seen.append(run_id)

    monkeypatch.setattr("memory.agents.runner.execute_run", fake_execute)

    agent_tasks.run_agent("run-1")

    assert seen == ["run-1"]


def run(db, user, **overrides):
    r = AgentRun(
        user_id=user.id,
        spec=overrides.pop("spec", {}),
        input="x",
        max_cost_usd=Decimal("1"),
        trigger_kind="manual",
        **overrides,
    )
    db.add(r)
    db.commit()
    return r


def ago(**delta) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**delta)


@pytest.mark.parametrize(
    "overrides,expected_status,expected_error",
    [
        # Without a timeout in the spec, the longest allowed (30 min) plus 10 min.
        ({"status": "running", "started_at": timedelta(hours=3)}, "failed", "worker lost"),
        ({"status": "running", "started_at": timedelta(minutes=15)}, "running", ""),
        # A run's own timeout_sec (60s) plus 10 min.
        (
            {
                "status": "running",
                "started_at": timedelta(minutes=15),
                "spec": {"timeout_sec": 60},
            },
            "failed",
            "worker lost",
        ),
        ({"status": "running", "started_at": timedelta(seconds=30)}, "running", ""),
        (
            {"status": "pending", "created_at": timedelta(hours=1)},
            "failed",
            "queue is backed up",
        ),
        # A busy queue can hold runs for the longest run's duration.
        ({"status": "pending", "created_at": timedelta(minutes=30)}, "pending", ""),
        ({"status": "pending"}, "pending", ""),
    ],
)
def test_sweep_fails_only_stuck_runs(
    db_session, regular_user, overrides, expected_status, expected_error
):
    now = datetime.now(timezone.utc)
    overrides = {key: now - value if isinstance(value, timedelta) else value for key, value in overrides.items()}
    r = run(db_session, regular_user, **overrides)
    child = run(db_session, regular_user, status="running", started_at=now, parent_run_id=r.id, depth=1)

    sweep_agent_runs()

    db_session.expire_all()
    swept = db_session.get(AgentRun, r.id)
    assert swept is not None
    assert swept.status == expected_status
    assert expected_error in (swept.error or "")
    # A swept run's children are told to stop, as when a run ends normally.
    assert (db_session.get(AgentRun, child.id).cancel_requested_at is not None) is (expected_status == "failed")


@pytest.mark.parametrize(
    "age_days,starred,spec,purged",
    [
        (15, False, {}, True),
        (15, True, {}, False),
        (10, False, {}, False),
        # An agent's own retention_days beats the default either way.
        (10, False, {"retention_days": 7}, True),
        (15, False, {"retention_days": 30}, False),
        # A snapshot from before the spec bounded it is held to 3650 days, not an SQL error.
        (15, False, {"retention_days": 10**9}, False),
    ],
)
def test_purge_deletes_unstarred_runs_past_their_retention(
    db_session, regular_user, age_days, starred, spec, purged
):
    r = run(
        db_session,
        regular_user,
        status="done",
        starred=starred,
        spec=spec,
        finished_at=ago(days=age_days),
    )
    db_session.add(AgentRunStep(run_id=r.id, seq=0, kind="user", content=[]))
    db_session.commit()
    run_id = r.id

    purge_agent_runs()

    db_session.expire_all()
    assert (db_session.get(AgentRun, run_id) is None) is purged
    assert (db_session.query(AgentRunStep).filter_by(run_id=run_id).count() == 0) is purged


def test_purge_keeps_the_runs_a_starred_run_started(db_session, regular_user):
    starred = run(db_session, regular_user, status="done", starred=True, finished_at=ago(days=30))
    child = run(db_session, regular_user, status="done", finished_at=ago(days=30), parent_run_id=starred.id, depth=1)
    grandchild = run(db_session, regular_user, status="done", finished_at=ago(days=30), parent_run_id=child.id, depth=2)
    run(db_session, regular_user, status="done", finished_at=ago(days=30))
    kept = {starred.id, child.id, grandchild.id}

    purge_agent_runs()

    db_session.expire_all()
    assert {r.id for r in db_session.query(AgentRun)} == kept


def test_purge_keeps_unfinished_runs(db_session, regular_user):
    r = run(db_session, regular_user, status="running", created_at=ago(days=30))

    purge_agent_runs()

    db_session.expire_all()
    assert db_session.get(AgentRun, r.id) is not None


def test_purge_works_through_every_batch(db_session, regular_user, monkeypatch):
    monkeypatch.setattr(maintenance, "PURGE_BATCH_SIZE", 1)
    for _ in range(3):
        run(db_session, regular_user, status="done", finished_at=ago(days=15))

    purge_agent_runs()

    assert db_session.query(AgentRun).count() == 0
