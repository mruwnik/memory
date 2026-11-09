import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from memory.agents import run_notify
from memory.agents.run_notify import PREVIEW_CHARS, notify_finished_runs, outcome_preview, run_label
from memory.common import settings
from memory.common.celery_app import NOTIFY_FINISHED_RUNS, SEND_NOTIFICATION, app, build_beat_schedule
from memory.common.db.models import AgentDefinition, AgentRun
from memory.workers.tasks import maintenance

SPEC = {"system_prompt": "x", "model": "anthropic/m"}
OWNER_ADDRESS = "owner@example.com"


@pytest.fixture
def owner(regular_user, add_sending_email):
    """A user with an email account notifications can be sent from."""
    add_sending_email(regular_user, OWNER_ADDRESS)
    return regular_user


def add_run(db, user, status="done", notify=True, **fields) -> AgentRun:
    run = AgentRun(
        user_id=user.id,
        spec=SPEC,
        input="x",
        max_cost_usd=Decimal("1"),
        trigger_kind="manual",
        status=status,
        notify_on_finish=notify,
        finished_at=datetime.now(timezone.utc),
        **fields,
    )
    db.add(run)
    db.commit()
    return run


def notified_at(db, run_id: str) -> datetime | None:
    db.expire_all()
    found = db.get(AgentRun, run_id)
    assert found is not None
    return found.notified_at


def sent_notifications(dispatch) -> list[list]:
    return [c.kwargs["args"] for c in dispatch.call_args_list if c.args[0] == SEND_NOTIFICATION]


@pytest.mark.parametrize("status", ["done", "failed", "budget_exceeded", "cancelled"])
def test_each_ended_run_is_notified_once(db_session, owner, no_celery_dispatch, status):
    runs = [add_run(db_session, owner, status=status) for _ in range(2)]

    first = notify_finished_runs()
    second = notify_finished_runs()

    assert first == {"notified": 2, "unsent": 0}
    assert second == {"notified": 0, "unsent": 0}
    assert len(sent_notifications(no_celery_dispatch)) == 2
    assert all(notified_at(db_session, r.id) is not None for r in runs)


@pytest.mark.parametrize(
    "status,notify",
    [("running", True), ("pending", True), ("done", False), ("failed", False)],
)
def test_unfinished_and_opted_out_runs_are_not_notified(
    db_session, regular_user, no_celery_dispatch, status, notify
):
    run = add_run(db_session, regular_user, status=status, notify=notify)

    assert notify_finished_runs() == {"notified": 0, "unsent": 0}
    assert sent_notifications(no_celery_dispatch) == []
    assert notified_at(db_session, run.id) is None


def test_the_notification_names_the_run_and_how_to_fetch_it(
    db_session, owner, no_celery_dispatch, monkeypatch
):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example/")
    run = add_run(db_session, owner, agent="fact-check", result={"text": "it's true"})

    notify_finished_runs()

    [args] = sent_notifications(no_celery_dispatch)
    channel, address, message, user_id, subject, _ = args
    assert (channel, address, user_id) == ("email", OWNER_ADDRESS, owner.id)
    assert subject == "Agent run done: fact-check"
    assert message == (
        "**Agent run done: fact-check**\n\n"
        "it's true\n\n"
        f'agent_wait(run_id="{run.id}", result_only=true) returns the whole result.\n\n'
        f"[View details](https://memory.example/agents/runs/{run.id})"
    )


def test_an_owner_with_no_channel_counts_as_unsent(db_session, regular_user, no_celery_dispatch):
    run = add_run(db_session, regular_user)

    assert notify_finished_runs() == {"notified": 0, "unsent": 1}
    assert sent_notifications(no_celery_dispatch) == []
    # Not retried every sweep.
    assert notified_at(db_session, run.id) is not None


def test_an_owner_with_no_channel_is_logged_by_id(db_session, regular_user, no_celery_dispatch, caplog):
    run = add_run(db_session, regular_user, agent="secret-agent", result={"text": "private words"})

    with caplog.at_level(logging.INFO, logger=run_notify.__name__):
        notify_finished_runs()

    [record] = [r for r in caplog.records if r.name == run_notify.__name__]
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        f"run {run.id} ended but its owner, user {regular_user.id}, has no channel to notify on"
    )


def test_a_failure_to_notify_one_run_does_not_stop_the_others(
    db_session, owner, no_celery_dispatch, monkeypatch
):
    broken = add_run(db_session, owner, agent="broken")
    fine = add_run(db_session, owner, agent="fine")
    real_notification_for = run_notify.notification_for

    def notification_for(db, run):
        assert run.agent != "broken", "boom"
        return real_notification_for(db, run)

    monkeypatch.setattr(run_notify, "notification_for", notification_for)

    assert notify_finished_runs() == {"notified": 1, "unsent": 1}
    assert notified_at(db_session, broken.id) is not None
    assert notified_at(db_session, fine.id) is not None


@pytest.mark.transactional_db
def test_a_database_error_notifying_one_run_does_not_resend_the_others(
    db_session, owner, no_celery_dispatch, monkeypatch
):
    broken = add_run(db_session, owner, agent="broken")
    fine = add_run(db_session, owner, agent="fine")
    real_notification_for = run_notify.notification_for

    def notification_for(db, run):
        db.execute(text("SELECT 1 / CASE WHEN :agent = 'broken' THEN 0 ELSE 1 END"), {"agent": run.agent})
        return real_notification_for(db, run)

    monkeypatch.setattr(run_notify, "notification_for", notification_for)

    first = notify_finished_runs()
    second = notify_finished_runs()

    assert first == {"notified": 1, "unsent": 1}
    assert second == {"notified": 0, "unsent": 0}
    [args] = sent_notifications(no_celery_dispatch)
    assert args[4] == "Agent run done: fine"
    assert notified_at(db_session, broken.id) is not None
    assert notified_at(db_session, fine.id) is not None


@pytest.mark.parametrize(
    "result,error,expected",
    [
        ({"text": "short"}, None, "short"),
        ({"text": "a" * PREVIEW_CHARS}, None, "a" * PREVIEW_CHARS),
        ({"text": "a" * (PREVIEW_CHARS + 1)}, None, "a" * PREVIEW_CHARS + "…"),
        ({"data": {"verdict": "true"}}, None, '{"verdict": "true"}'),
        ({"text": "partial"}, "timed out", "timed out"),
        (None, "e" * (PREVIEW_CHARS + 10), "e" * PREVIEW_CHARS + "…"),
        (None, None, ""),
        ({}, None, ""),
    ],
)
def test_outcome_preview(result, error, expected):
    assert outcome_preview(AgentRun(result=result, error=error)) == expected


def test_a_run_with_nothing_to_preview_still_says_how_to_fetch_it(db_session, regular_user):
    run = add_run(db_session, regular_user, status="cancelled")

    subject, message = run_notify.notification_for(db_session, run)

    assert subject == "Agent run cancelled: inline agent"
    assert message == f'agent_wait(run_id="{run.id}", result_only=true) returns the whole result.'


def test_run_label_names_the_agent(db_session, regular_user):
    definition = AgentDefinition(user_id=regular_user.id, name="digest", spec=SPEC)
    db_session.add(definition)
    db_session.commit()
    labels = [
        run_label(db_session, add_run(db_session, regular_user, agent="fact-check")),
        run_label(db_session, add_run(db_session, regular_user, definition_id=definition.id)),
        run_label(db_session, add_run(db_session, regular_user)),
    ]

    assert labels == ["fact-check", "digest", "inline agent"]


def test_the_task_runs_the_sweep(db_session, owner, no_celery_dispatch):
    add_run(db_session, owner)

    assert maintenance.notify_finished_runs() == {"notified": 1, "unsent": 0}


def test_the_sweep_runs_on_the_scheduler_every_30_seconds():
    entry = build_beat_schedule()["notify-finished-runs"]

    assert (entry["task"], entry["schedule"]) == (NOTIFY_FINISHED_RUNS, 30)
    assert app.conf.task_routes[NOTIFY_FINISHED_RUNS] == {
        "queue": f"{settings.CELERY_QUEUE_PREFIX}-scheduler"
    }


def test_run_notify_stays_off_the_agents_image():
    # Scheduler workers import it; they lack what spec, enqueue and the API need.
    api_only = ["memory.agents.spec", "memory.agents.enqueue", "memory.agents.runner", "memory.api"]
    code = f"import sys, memory.agents.run_notify; print([m for m in {api_only!r} if m in sys.modules])"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env)

    assert out.stdout.strip() == "[]"
