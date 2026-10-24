"""Reconciling scheduled Claude session executions and sweeping dead containers."""

import asyncio
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest

from memory.claude_containers import claude_maintenance
from memory.claude_containers.orchestrator_client import OrchestratorClient, SessionInfo
from memory.common import settings
from memory.common.db.models import ScheduledTask, TaskExecution

SESSION = "u1-e1-aaaa1111aaaa1111aaaa1111aaaa1111"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def running_execution(db_session, user, task_type="claude_session", data=None, age=timedelta(minutes=10)):
    task = ScheduledTask(user_id=user.id, task_type=task_type, message="go", cron_expression="0 9 * * *")
    db_session.add(task)
    db_session.flush()
    execution = TaskExecution(
        task_id=task.id,
        scheduled_time=now() - age,
        started_at=now() - age,
        status="running",
        data={"session_id": SESSION} if data is None else data,
    )
    db_session.add(execution)
    db_session.commit()
    return execution


class FakeOrchestrator(OrchestratorClient):
    def __init__(self, socket_path, containers=(), fail_list=False):
        super().__init__(str(socket_path))
        self.containers = list(containers)
        self.fail_list = fail_list
        self.calls = []

    async def list_containers(self):
        self.calls.append("list")
        if self.fail_list:
            raise RuntimeError("orchestrator down")
        return self.containers

    async def cleanup_dead_containers(self):
        self.calls.append("cleanup")
        return ["old"]


@pytest.fixture
def socket(tmp_path):
    path = tmp_path / "orchestrator.sock"
    path.touch()
    return path


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.mark.parametrize(
    "containers, age, status, container_status, finished",
    [
        ([SessionInfo(SESSION, status="running")], timedelta(minutes=10), "running", None, False),
        ([SessionInfo(SESSION, status="created")], timedelta(minutes=10), "running", None, False),
        ([SessionInfo(SESSION, status="exited")], timedelta(minutes=10), "completed", "exited", True),
        ([SessionInfo(SESSION, status="dead")], timedelta(minutes=10), "failed", "dead", True),
        ([], timedelta(minutes=10), "failed", "gone", True),
        ([SessionInfo(SESSION, status="running")], timedelta(hours=25), "failed", "running", True),
    ],
)
def test_settle_records_how_the_container_ended(
    db_session, test_user, containers, age, status, container_status, finished
):
    execution = running_execution(db_session, test_user, age=age)

    claude_maintenance.settle_executions(
        [(execution.id, SESSION)], {c.session_id: c.status for c in containers}, now()
    )

    db_session.refresh(execution)
    assert execution.status == status
    assert (execution.data or {}).get("container_status") == container_status
    assert (execution.finished_at is not None) is finished


def test_only_running_session_executions_with_a_container_are_candidates(db_session, test_user):
    wanted = running_execution(db_session, test_user)
    running_execution(db_session, test_user, task_type="notification", data={})
    running_execution(db_session, test_user, data={})

    assert claude_maintenance.candidate_executions() == [(wanted.id, SESSION)]


@pytest.mark.asyncio
async def test_tick_settles_before_it_cleans_up(db_session, test_user, socket, redis):
    execution = running_execution(db_session, test_user)
    orchestrator = FakeOrchestrator(socket, [SessionInfo(SESSION, status="exited")])

    result = await claude_maintenance.maintenance_tick(orchestrator, redis)

    db_session.refresh(execution)
    assert execution.status == "completed"
    assert orchestrator.calls == ["list", "cleanup"]
    assert result == {"settled": 1, "removed": ["old"]}


@pytest.mark.asyncio
async def test_tick_skips_without_an_orchestrator_socket(tmp_path, redis):
    orchestrator = FakeOrchestrator(tmp_path / "missing.sock")

    assert await claude_maintenance.maintenance_tick(orchestrator, redis) is None
    assert orchestrator.calls == []


@pytest.mark.asyncio
async def test_only_one_process_runs_each_tick(db_session, socket, redis):
    first, second = FakeOrchestrator(socket), FakeOrchestrator(socket)

    await claude_maintenance.maintenance_tick(first, redis)
    await claude_maintenance.maintenance_tick(second, redis)

    assert first.calls == ["list", "cleanup"]
    assert second.calls == []


@pytest.mark.asyncio
async def test_a_failed_listing_neither_settles_nor_cleans_up(db_session, test_user, socket, redis):
    execution = running_execution(db_session, test_user)
    orchestrator = FakeOrchestrator(socket, fail_list=True)

    with pytest.raises(RuntimeError):
        await claude_maintenance.maintenance_tick(orchestrator, redis)

    db_session.refresh(execution)
    assert execution.status == "running"
    assert orchestrator.calls == ["list"]


@pytest.mark.asyncio
async def test_the_loop_survives_a_failing_tick(monkeypatch):
    ticks = []

    async def tick(client, redis):
        ticks.append(len(ticks))
        if len(ticks) == 1:
            raise RuntimeError("boom")
        return None

    monkeypatch.setattr(claude_maintenance, "maintenance_tick", tick)
    monkeypatch.setattr(settings, "CLAUDE_MAINTENANCE_INTERVAL_SEC", 0.01)
    async with claude_maintenance.maintenance_running():
        await asyncio.sleep(0.1)

    assert len(ticks) >= 2


@pytest.mark.asyncio
async def test_the_loop_is_off_when_disabled(monkeypatch):
    ticks = []

    async def tick(client, redis):
        ticks.append(1)

    monkeypatch.setattr(claude_maintenance, "maintenance_tick", tick)
    monkeypatch.setattr(settings, "CLAUDE_MAINTENANCE_INTERVAL_SEC", 0)
    async with claude_maintenance.maintenance_running():
        await asyncio.sleep(0.05)

    assert ticks == []


@pytest.mark.asyncio
async def test_the_loop_warns_when_the_orchestrator_socket_is_missing(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        claude_maintenance, "get_orchestrator_client", lambda: FakeOrchestrator(tmp_path / "missing.sock")
    )
    monkeypatch.setattr(settings, "CLAUDE_MAINTENANCE_INTERVAL_SEC", 60)
    async with claude_maintenance.maintenance_running():
        pass

    assert "missing.sock" in caplog.text
