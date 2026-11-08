# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportOptionalOperand=false
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.agents import cron
from memory.agents.cron import start_time_run
from memory.common import settings
from memory.common.celery_app import RUN_AGENT
from memory.common.db.models import AgentDefinition, AgentRun, Subscription, APIKey, Event

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}


@pytest.fixture
def owner(db_session, regular_user):
    regular_user.scopes = ["agents"]
    db_session.commit()
    return regular_user


@pytest.fixture
def told(monkeypatch):
    notices = []
    monkeypatch.setattr(
        "memory.agents.clock.tell_owner", lambda db, owner, subject, message: notices.append(subject)
    )
    return notices


def now() -> datetime:
    return datetime.now(timezone.utc)


def definition(db, user, spec=SPEC, name="cron"):
    d = AgentDefinition(user_id=user.id, name=name, spec=spec)
    db.add(d)
    db.commit()
    return d


def api_key(db, user, scopes, revoked=False):
    key = APIKey.create(user_id=user.id, name="k", scopes=scopes)
    key.revoked = revoked
    db.add(key)
    db.commit()
    return key


def new_subscription(db, user, d, key=None):
    subscription = Subscription(
        user_id=user.id,
        name="daily",
        kind="cron",
        handler={"agent": d.id},
        definition_id=d.id,
        config={"cron": "0 9 * * *", "input": "daily digest"},
        api_key_id=key.id if key else None,
        via_api_key=key is not None,
        next_at=now() + timedelta(days=1),
    )
    db.add(subscription)
    db.commit()
    return subscription


def tick(db, subscription, fire_at=None, result=None, missed=0):
    event = Event(
        kind="tick", subscription_id=subscription.id, owner_id=subscription.user_id,
        fire_at=fire_at or now(), data={"missed": missed}, result=result,
        routed_at=now() if result else None,
    )
    db.add(event)
    db.commit()
    return event


def test_a_tick_starts_its_run_and_ends_done(db_session, owner, fake_providers, no_celery_dispatch):
    d = definition(db_session, owner)
    subscription = new_subscription(db_session, owner, d)
    earlier = tick(db_session, subscription, now() - timedelta(days=1), result={"status": "done"})
    e = tick(db_session, subscription, missed=2)

    run_id = start_time_run(e.id)

    db_session.refresh(e)
    run = db_session.get_one(AgentRun, run_id)
    assert e.result == {"status": "done", "run_id": run_id}
    assert (run.definition_id, run.input, run.trigger_kind, run.subscription_id, run.dedupe_key) == (
        d.id, "daily digest", "cron", subscription.id, f"event:{e.id}",
    )
    assert run.trigger_ref == {
        "event_id": e.id,
        "time": e.fire_at.isoformat(),
        "subscription_id": subscription.id,
        "missed": 2,
        "since": earlier.fire_at.isoformat(),
    }
    no_celery_dispatch.assert_any_call(RUN_AGENT, args=[run_id], task_id=run_id)


def test_a_timer_runs_its_definition_with_its_input(db_session, owner, fake_providers):
    d = definition(db_session, owner)
    e = Event(kind="timer", owner_id=owner.id, fire_at=now(), handler={"agent": d.id}, data={"input": "once"})
    db_session.add(e)
    db_session.commit()

    run_id = start_time_run(e.id)

    run = db_session.get_one(AgentRun, run_id)
    assert (run.input, run.trigger_kind, run.subscription_id, run.trigger_ref) == (
        "once", "timer", None, {"event_id": e.id, "time": e.fire_at.isoformat()},
    )


@pytest.mark.parametrize("slug", ["echo", "system:echo"])
def test_a_timer_runs_a_code_agent_by_slug(db_session, owner, code_agents, slug):
    e = Event(kind="timer", owner_id=owner.id, fire_at=now(), handler={"agent": slug}, data={"input": "x"})
    db_session.add(e)
    db_session.commit()

    run = db_session.get_one(AgentRun, start_time_run(e.id))

    assert (run.agent, run.definition_id, run.input) == (slug, None, "x")


def test_a_code_agents_hourly_cap_sheds_its_timers_and_tells_the_owner(
    db_session, owner, code_agents, monkeypatch, told
):
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", 1)
    timers = [
        Event(kind="timer", owner_id=owner.id, fire_at=now(), handler={"agent": "echo"}, data={"input": "x"})
        for _ in range(2)
    ]
    db_session.add_all(timers)
    db_session.commit()

    for t in timers:
        start_time_run(t.id)

    db_session.expire_all()
    assert [t.result["status"] for t in timers] == ["done", "skipped:shed:rate"]
    assert told == ["Timer failed"]


def test_a_redelivery_does_nothing(db_session, owner, fake_providers):
    subscription = new_subscription(db_session, owner, definition(db_session, owner))
    e = tick(db_session, subscription)

    first = start_time_run(e.id)
    second = start_time_run(e.id)

    assert (first is not None, second, db_session.query(AgentRun).count()) == (True, None, 1)


def test_a_resend_after_a_crash_reuses_the_run(db_session, owner, fake_providers):
    subscription = new_subscription(db_session, owner, definition(db_session, owner))
    e = tick(db_session, subscription)
    run_id = start_time_run(e.id)
    db_session.refresh(e)
    e.routed_at, e.result = None, None
    db_session.commit()

    assert start_time_run(e.id) == run_id
    assert db_session.query(AgentRun).count() == 1


def test_the_creating_api_key_bounds_the_run(db_session, owner, fake_providers):
    key = api_key(db_session, owner, ["agents", "read"])
    subscription = new_subscription(db_session, owner, definition(db_session, owner), key)

    run = db_session.get_one(AgentRun, start_time_run(tick(db_session, subscription).id))

    assert (run.api_key_id, sorted(run.scopes)) == (key.id, ["agents", "read"])


@pytest.mark.parametrize(
    "case, expected",
    [
        ("revoked key", "no longer valid"),
        ("key without agents scope", "'agents' scope"),
        ("user without agents scope", "'agents' scope"),
        ("invalid spec", "reasoning"),
    ],
)
def test_a_subscription_that_can_never_run_fails_and_trips(
    db_session, owner, fake_providers, told, case, expected
):
    setups = {
        "revoked key": lambda: (SPEC, api_key(db_session, owner, ["agents"], revoked=True)),
        "key without agents scope": lambda: (SPEC, api_key(db_session, owner, ["read"])),
        "user without agents scope": lambda: setattr(owner, "scopes", ["read"]) or (SPEC, None),
        "invalid spec": lambda: (SPEC | {"reasoning": "medium", "model": "openai/m"}, None),
    }
    spec, key = setups[case]()
    subscription = new_subscription(db_session, owner, definition(db_session, owner, spec), key)
    e = tick(db_session, subscription)

    assert start_time_run(e.id) is None

    db_session.refresh(e)
    db_session.refresh(subscription)
    assert (e.result["status"], subscription.enabled, told) == ("failed", False, ["Schedule paused: daily"])
    assert expected in e.result["error"]
    assert db_session.query(AgentRun).count() == 0


def test_a_cap_sheds_the_tick_and_tells_the_owner_once(db_session, owner, fake_providers, monkeypatch):
    d = definition(db_session, owner, SPEC | {"max_cost_usd_per_day": "0.000001"})
    db_session.add(
        AgentRun(
            user_id=owner.id, definition_id=d.id, spec=SPEC, input="earlier",
            max_cost_usd=Decimal("1"), cost_usd=Decimal("0.01"), trigger_kind="manual", status="done",
        )
    )
    db_session.commit()
    subscription = new_subscription(db_session, owner, d)
    first, second = tick(db_session, subscription), tick(db_session, subscription, now() + timedelta(seconds=1))
    told = []
    monkeypatch.setattr(
        "memory.agents.subscriptions.tell_owner", lambda db, owner, subject, message: told.append(subject)
    )

    start_time_run(first.id)
    start_time_run(second.id)

    db_session.expire_all()
    assert [e.result["status"] for e in (first, second)] == ["skipped:shed:budget"] * 2
    assert (subscription.enabled, "throttled" in subscription.state) == (True, True)
    assert told == ["Agent subscription throttled: daily"]


@pytest.mark.parametrize("hours_ago, kept", [(0, True), (3, False)])
def test_a_tick_that_runs_ends_a_throttling_episode_only_once_it_is_over(
    db_session, owner, fake_providers, hours_ago, kept
):
    subscription = new_subscription(db_session, owner, definition(db_session, owner))
    last = (now() - timedelta(hours=hours_ago)).isoformat()
    subscription.state = {"throttled": {"reason": "cap", "since": last, "last": last}}
    e = tick(db_session, subscription)

    start_time_run(e.id)

    db_session.refresh(subscription)
    assert ("throttled" in subscription.state) == kept


def test_a_disabled_subscriptions_tick_is_skipped(db_session, owner, fake_providers):
    subscription = new_subscription(db_session, owner, definition(db_session, owner))
    e = tick(db_session, subscription)
    subscription.enabled = False
    db_session.commit()

    start_time_run(e.id)

    db_session.refresh(e)
    assert e.result == {"status": "skipped:disabled"}


def test_an_unexpected_error_leaves_the_tick_for_a_resend(db_session, owner, fake_providers, monkeypatch):
    subscription = new_subscription(db_session, owner, definition(db_session, owner))
    e = tick(db_session, subscription)

    def broker_down(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(cron, "enqueue_run", broker_down)
    with pytest.raises(ConnectionError):
        start_time_run(e.id)

    db_session.refresh(e)
    db_session.refresh(subscription)
    assert (e.routed_at, subscription.enabled) == (None, True)


def late_tick(db, owner, d, at):
    return tick(db, new_subscription(db, owner, d), at)


def late_timer(db, owner, d, at):
    e = Event(kind="timer", owner_id=owner.id, fire_at=at, handler={"agent": d.id}, data={"input": "x"})
    db.add(e)
    db.commit()
    return e


@pytest.mark.parametrize("make", [late_tick, late_timer])
def test_a_start_that_waited_past_max_lateness_is_skipped(db_session, owner, fake_providers, make):
    e = make(db_session, owner, definition(db_session, owner), now() - timedelta(hours=7))

    assert start_time_run(e.id) is None

    db_session.refresh(e)
    assert (e.result["status"], db_session.query(AgentRun).count()) == ("skipped:late", 0)
