from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.agents import cron
from memory.agents.cron import start_cron_run
from memory.common import settings
from memory.common.celery_app import RUN_AGENT
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    APIKey,
    ScheduledTask,
    TaskExecution,
)

SPEC = {"system_prompt": "x", "model": "fake-rich/m", "reasoning": "high"}


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_RUN_COST_USD", Decimal("1.00"))
    monkeypatch.setattr(settings, "AGENT_MAX_DAILY_COST_USD", Decimal("10.00"))


@pytest.fixture
def owner(db_session, regular_user):
    regular_user.scopes = ["agents"]
    db_session.commit()
    return regular_user


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def definition(db, user, spec=SPEC):
    d = AgentDefinition(user_id=user.id, name="cron", spec=spec)
    db.add(d)
    db.commit()
    return d


def api_key(db, user, scopes, revoked=False):
    key = APIKey.create(user_id=user.id, name="k", scopes=scopes)
    key.revoked = revoked
    db.add(key)
    db.commit()
    return key


def execution(db, user, definition_id, status="pending", scheduled=None, **data):
    task = ScheduledTask(
        user_id=user.id,
        task_type="agent_run",
        cron_expression="0 9 * * *",
        next_scheduled_time=now() + timedelta(hours=1),
        data={"definition_id": definition_id, "input": "daily digest"} | data,
    )
    db.add(task)
    db.flush()
    e = TaskExecution(
        task_id=task.id, scheduled_time=scheduled or now(), status=status
    )
    db.add(e)
    db.commit()
    return e


def test_cron_run_is_enqueued_and_recorded(
    db_session, owner, fake_providers, no_celery_dispatch
):
    d = definition(db_session, owner)
    e = execution(db_session, owner, d.id)

    start_cron_run(e.id)

    db_session.refresh(e)
    run = db_session.query(AgentRun).one()
    assert (e.status, e.data, e.error_message) == ("completed", {"run_id": run.id}, None)
    assert (run.definition_id, run.input, run.trigger_kind, run.dedupe_key) == (
        d.id,
        "daily digest",
        "cron",
        e.id,
    )
    assert run.trigger_ref == {"task_id": e.task_id, "execution_id": e.id}
    no_celery_dispatch.assert_called_once_with(RUN_AGENT, args=[run.id], task_id=run.id)


def test_a_crash_after_enqueue_leaves_it_pending_and_the_redispatch_reuses_the_run(
    db_session, owner, fake_providers, monkeypatch
):
    d = definition(db_session, owner)
    e = execution(db_session, owner, d.id)

    def worker_dies(*args):
        raise RuntimeError("worker died")

    with monkeypatch.context() as m:
        m.setattr(cron, "complete", worker_dies)
        with pytest.raises(RuntimeError):
            start_cron_run(e.id)
    db_session.refresh(e)
    pending = e.status

    run_id = start_cron_run(e.id)

    db_session.refresh(e)
    assert (pending, e.status) == ("pending", "completed")
    assert [r.id for r in db_session.query(AgentRun)] == [run_id]


def test_an_unexpected_enqueue_error_fails_the_execution_only(
    db_session, owner, fake_providers, monkeypatch
):
    d = definition(db_session, owner)
    e = execution(db_session, owner, d.id)

    def broker_down(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(cron, "enqueue_run", broker_down)

    start_cron_run(e.id)

    db_session.refresh(e)
    assert (e.status, e.error_message, e.task.enabled) == (
        "failed",
        "broker unreachable",
        True,
    )


def test_a_finished_execution_is_left_alone(db_session, owner, fake_providers):
    d = definition(db_session, owner)
    e = execution(db_session, owner, d.id, status="completed")

    start_cron_run(e.id)

    assert db_session.query(AgentRun).count() == 0


def test_a_long_overdue_execution_is_skipped(db_session, owner, fake_providers):
    d = definition(db_session, owner)
    e = execution(db_session, owner, d.id, scheduled=now() - timedelta(hours=7))

    start_cron_run(e.id)

    db_session.refresh(e)
    assert e.status == "failed"
    assert "late" in (e.error_message or "")
    assert db_session.query(AgentRun).count() == 0


def test_the_creating_api_key_bounds_the_run(db_session, owner, fake_providers):
    d = definition(db_session, owner)
    key = api_key(db_session, owner, ["agents", "read"])
    e = execution(db_session, owner, d.id, api_key_id=key.id)

    start_cron_run(e.id)

    run = db_session.query(AgentRun).one()
    assert (run.api_key_id, sorted(run.scopes)) == (key.id, ["agents", "read"])


@pytest.mark.parametrize(
    "case,expected",
    [
        ("foreign definition", "no longer exists"),
        ("revoked key", "no longer valid"),
        ("key without agents scope", "'agents' scope"),
        ("user without agents scope", "'agents' scope"),
    ],
)
def test_a_schedule_that_can_never_run_is_failed_and_disabled(
    db_session, owner, admin_user, fake_providers, case, expected
):
    theirs = definition(db_session, admin_user)
    mine = AgentDefinition(user_id=owner.id, name="mine", spec=SPEC)
    db_session.add(mine)
    db_session.commit()

    def unscoped_user():
        owner.scopes = ["read"]
        return execution(db_session, owner, mine.id)

    setups = {
        "foreign definition": lambda: execution(db_session, owner, theirs.id),
        "revoked key": lambda: execution(
            db_session,
            owner,
            mine.id,
            api_key_id=api_key(db_session, owner, ["agents"], revoked=True).id,
        ),
        "key without agents scope": lambda: execution(
            db_session, owner, mine.id, api_key_id=api_key(db_session, owner, ["read"]).id
        ),
        "user without agents scope": unscoped_user,
    }
    e = setups[case]()

    start_cron_run(e.id)

    db_session.refresh(e)
    assert (e.status, e.task.enabled) == ("failed", False)
    assert expected in (e.error_message or "")
    assert db_session.query(AgentRun).count() == 0


@pytest.mark.parametrize(
    "spec,expected",
    [
        (SPEC | {"reasoning": "medium", "model": "fake-poor/m"}, "reasoning"),
        (SPEC | {"max_cost_usd_per_day": "0.000001"}, "definition pool"),
    ],
)
def test_refused_runs_fail_the_execution_but_keep_the_schedule(
    db_session, owner, fake_providers, spec, expected
):
    d = definition(db_session, owner, spec)
    db_session.add(
        AgentRun(
            user_id=owner.id,
            definition_id=d.id,
            spec=SPEC,
            input="earlier",
            max_cost_usd=Decimal("1"),
            cost_usd=Decimal("0.01"),
            trigger_kind="manual",
            status="done",
        )
    )
    db_session.commit()
    e = execution(db_session, owner, d.id)

    start_cron_run(e.id)

    db_session.refresh(e)
    assert (e.status, e.task.enabled) == ("failed", True)
    assert expected in (e.error_message or "")
    assert db_session.query(AgentRun).filter(AgentRun.trigger_kind == "cron").count() == 0


def test_a_late_recovery_of_a_started_run_completes_it(
    db_session, owner, fake_providers, monkeypatch
):
    d = definition(db_session, owner)
    e = execution(db_session, owner, d.id)
    def worker_dies(*args):
        raise RuntimeError("worker died")

    with monkeypatch.context() as m:
        m.setattr(cron, "complete", worker_dies)
        with pytest.raises(RuntimeError):
            start_cron_run(e.id)
    e.scheduled_time = now() - timedelta(hours=7)
    db_session.commit()

    run_id = start_cron_run(e.id)

    db_session.refresh(e)
    assert (e.status, e.data) == ("completed", {"run_id": run_id})
