from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memory.agents import fire, triggers
from memory.agents.fire import fire_trigger
from memory.common import settings
from memory.common.celery_app import FIRE_TRIGGER, SEND_NOTIFICATION
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentTrigger,
    AgentTriggerMatch,
    APIKey,
    BlogPost,
)
from tests.conftest import route_item, unique_sha256

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
BATCH = {"mode": "batch", "window_sec": 60, "max_items": 10}


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", 60)


@pytest.fixture
def no_grace(monkeypatch):
    monkeypatch.setattr(triggers, "DISPATCH_GRACE", timedelta(0))


def setup(db, user, spec=SPEC, delivery=None, **fields):
    d = AgentDefinition(user_id=user.id, name="watcher", spec=spec)
    db.add(d)
    db.flush()
    config = {"filter": {"tags_any": ["ai"]}} | ({"delivery": delivery} if delivery else {})
    t = AgentTrigger(
        user_id=user.id,
        name="t",
        definition_id=d.id,
        handler={"agent": d.id},
        kind="ingest",
        config=triggers.IngestConfig.model_validate(config).model_dump(mode="json"),
        **fields,
    )
    db.add(t)
    db.commit()
    return t


def post(db, creator_id=None):
    p = BlogPost(
        sha256=unique_sha256("p"),
        modality="blog",
        title="Post",
        content="body",
        tags=["ai"],
        embed_status="STORED",
        creator_id=creator_id,
    )
    db.add(p)
    db.commit()
    return p


def claimed(db, trigger, *items) -> list[str]:
    """Pending deliveries of `items` to the trigger, as the router writes them."""
    db.add_all(
        AgentTriggerMatch(
            trigger_id=trigger.id,
            item_id=i.id,
            content_hash=triggers.content_version(i),
            changes=["new"],
        )
        for i in items
    )
    db.commit()
    return [triggers.delivery_ref(i.id, triggers.content_version(i)) for i in items]


def sent(dispatch) -> list[list]:
    """The FIRE_TRIGGER args sent since the last call."""
    fires = [c.kwargs["args"] for c in dispatch.call_args_list if c.args[0] == FIRE_TRIGGER]
    dispatch.reset_mock()
    return fires


def fire_sent(dispatch) -> list[str | None]:
    return [fire_trigger(*args) for args in sent(dispatch)]


def notifications(dispatch) -> int:
    return sum(c.args[0] == SEND_NOTIFICATION for c in dispatch.call_args_list)


def statuses(db, trigger) -> list[str]:
    db.expire_all()
    rows = db.query(AgentTriggerMatch).filter_by(trigger_id=trigger.id)
    return sorted(m.status for m in rows)


def age(db, trigger, by: timedelta, column="created_at"):
    """Move every delivery of the trigger `by` into the past."""
    for m in db.query(AgentTriggerMatch).filter_by(trigger_id=trigger.id):
        setattr(m, column, getattr(m, column) - by)
    db.commit()


def test_a_delivery_routed_but_never_sent_is_sent_by_the_sweep(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, monkeypatch
):
    trigger = setup(db_session, admin_user)
    item = post(db_session)
    with monkeypatch.context() as m:
        # A crash between the routing commit and the send.
        m.setattr(triggers, "send", lambda trigger_id, refs: None)
        route_item(db_session, item.id)

    early = triggers.sweep_deliveries()
    age(db_session, trigger, triggers.DISPATCH_GRACE, column="dispatched_at")
    triggers.sweep_deliveries()
    [run_id] = fire_sent(no_celery_dispatch)

    [delivery] = db_session.query(AgentTriggerMatch).all()
    assert early["sent"] == 0
    assert (delivery.status, delivery.run_id) == ("done", run_id)


def test_a_lost_fire_is_resent_and_both_copies_start_one_run(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, no_grace
):
    trigger = setup(db_session, admin_user)
    route_item(db_session, post(db_session).id)
    lost = sent(no_celery_dispatch)

    triggers.sweep_deliveries()
    resent = sent(no_celery_dispatch)
    runs = [fire_trigger(*args) for args in lost + resent]
    again = triggers.sweep_deliveries()

    assert resent == lost
    assert (runs[1], db_session.query(AgentRun).count()) == (None, 1)
    assert (statuses(db_session, trigger), again["sent"]) == (["done"], 0)


def test_a_delivery_still_pending_is_resent_at_doubling_intervals(
    db_session, admin_user, no_celery_dispatch
):
    trigger = setup(db_session, admin_user)
    route_item(db_session, post(db_session).id)
    sent(no_celery_dispatch)

    resent = []
    for _ in range(7):
        age(db_session, trigger, triggers.DISPATCH_GRACE, column="dispatched_at")
        triggers.sweep_deliveries()
        resent.append(len(sent(no_celery_dispatch)))

    # Sent at 0, then resent after 1, 2 and 4 grace periods.
    assert resent == [1, 0, 1, 0, 0, 0, 1]


def test_batch_members_wait_out_a_disabled_trigger_and_go_once_it_is_enabled(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user, delivery=BATCH)
    posts = [post(db_session) for _ in range(2)]
    for p in posts:
        route_item(db_session, p.id)
    trigger.enabled = False
    db_session.commit()
    age(db_session, trigger, timedelta(minutes=2))

    triggers.sweep_deliveries()
    while_disabled = sent(no_celery_dispatch)
    trigger.enabled = True
    db_session.commit()
    triggers.sweep_deliveries()
    [run_id] = fire_sent(no_celery_dispatch)

    run = db_session.get(AgentRun, run_id)
    assert while_disabled == []
    assert run is not None
    assert run.trigger_ref["item_ids"] == [p.id for p in posts]
    assert statuses(db_session, trigger) == ["done", "done"]


def test_a_batch_waits_for_its_window_or_until_it_is_full(
    db_session, admin_user, no_celery_dispatch
):
    trigger = setup(db_session, admin_user, delivery=BATCH | {"max_items": 2})
    posts = [post(db_session) for _ in range(3)]
    route_item(db_session, posts[0].id)

    alone = triggers.sweep_deliveries()
    for p in posts[1:]:
        route_item(db_session, p.id)
    full = triggers.sweep_deliveries()
    first = sent(no_celery_dispatch)
    age(db_session, trigger, timedelta(minutes=2))
    triggers.sweep_deliveries()

    refs = [[int(r.split(":")[0]) for r in args[1]] for args in first + sent(no_celery_dispatch)]
    assert (alone["sent"], full["sent"]) == (0, 1)
    assert refs == [[posts[0].id, posts[1].id], [posts[2].id]]


@pytest.mark.parametrize("enabled,status", [(True, "expired"), (False, "skipped:disabled")])
def test_deliveries_pending_too_long_end_with_a_reason(
    db_session, admin_user, no_celery_dispatch, enabled, status
):
    trigger = setup(db_session, admin_user, delivery=BATCH)
    route_item(db_session, post(db_session).id)
    trigger.enabled = enabled
    db_session.commit()
    age(db_session, trigger, triggers.DELIVERY_MAX_AGE + timedelta(minutes=1))

    swept = triggers.sweep_deliveries()

    assert (swept["expired"], statuses(db_session, trigger)) == (1, [status])
    assert sent(no_celery_dispatch) == []


def test_a_fire_that_keeps_failing_ends_the_delivery_failed(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, monkeypatch, no_grace
):
    monkeypatch.setattr(settings, "EVENT_MAX_ATTEMPTS", 2)
    trigger = setup(db_session, admin_user)

    def broken(*args):
        raise RuntimeError("render bug")

    monkeypatch.setattr(fire, "render_input", broken)
    route_item(db_session, post(db_session).id)
    [args] = sent(no_celery_dispatch)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            fire_trigger(*args)
    after_failing = fire_trigger(*args)
    triggers.sweep_deliveries()

    [delivery] = db_session.query(AgentTriggerMatch).all()
    assert (delivery.status, delivery.attempts, delivery.error) == ("failed", 2, "RuntimeError")
    assert (after_failing, sent(no_celery_dispatch)) == (None, [])


@pytest.mark.parametrize(
    "spec,earlier_cost,shed",
    [
        (SPEC | {"max_runs_per_hour": 1}, "0", "skipped:shed:rate"),
        (SPEC | {"max_cost_usd_per_day": "0.50"}, "0.50", "skipped:shed:budget"),
    ],
)
def test_over_a_cap_deliveries_are_deferred_then_shed_with_one_notification(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, no_grace, spec, earlier_cost, shed
):
    trigger = setup(db_session, admin_user, spec)
    db_session.add(
        AgentRun(
            user_id=admin_user.id,
            definition_id=trigger.definition_id,
            spec=SPEC,
            input="earlier",
            max_cost_usd=Decimal("1"),
            cost_usd=Decimal(earlier_cost),
            trigger_kind="ingest",
            status="done",
        )
    )
    db_session.commit()
    for _ in range(2):
        route_item(db_session, post(db_session).id)

    deferred = fire_sent(no_celery_dispatch)
    told = notifications(no_celery_dispatch)
    triggers.sweep_deliveries()
    too_early = sent(no_celery_dispatch)
    # An hour on: the deferred deliveries come back, still over the cap.
    age(db_session, trigger, triggers.THROTTLE_WINDOW, column="not_before")
    db_session.refresh(trigger)
    hour_ago = (datetime.now(timezone.utc) - triggers.THROTTLE_WINDOW).isoformat()
    trigger.state = trigger.state | {
        "throttled": trigger.state["throttled"] | {"since": hour_ago, "last": hour_ago}
    }
    db_session.commit()
    triggers.sweep_deliveries()
    fire_sent(no_celery_dispatch)

    db_session.refresh(trigger)
    assert (deferred, told, too_early) == ([None, None], 1, [])
    assert statuses(db_session, trigger) == [shed, shed]
    assert notifications(no_celery_dispatch) == 0
    assert trigger.enabled is True
    assert "throttled" in trigger.state


@pytest.mark.parametrize("held_back_ago,ended", [(timedelta(hours=2), True), (timedelta(minutes=5), False)])
def test_a_run_ends_the_throttling_episode_only_a_window_after_the_last_hold_back(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch, held_back_ago, ended
):
    last = (datetime.now(timezone.utc) - held_back_ago).isoformat()
    throttled = {"reason": "rate", "since": last, "last": last}
    trigger = setup(db_session, admin_user, state={"throttled": throttled})

    route_item(db_session, post(db_session).id)
    [run_id] = fire_sent(no_celery_dispatch)

    db_session.refresh(trigger)
    assert run_id is not None
    assert ("throttled" in trigger.state) is not ended


def test_a_sustained_overload_notifies_once(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user, max_per_hour=1)
    fired, told = [], 0
    for _ in range(3):
        route_item(db_session, post(db_session).id)
        route_item(db_session, post(db_session).id)
        fired += fire_sent(no_celery_dispatch)
        told += notifications(no_celery_dispatch)
        # A slot frees up, so the next item runs between the hold-backs.
        for run in db_session.query(AgentRun):
            run.created_at -= timedelta(hours=2)
        db_session.commit()

    assert [r is not None for r in fired] == [True, False] * 3
    assert told == 1


def test_unreadable_items_stay_skipped_when_a_cap_holds_back_the_rest(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    key = APIKey.create(user_id=admin_user.id, name="k", scopes=["agents", "read"])
    db_session.add(key)
    db_session.commit()
    trigger = setup(
        db_session, admin_user, SPEC | {"max_runs_per_hour": 1}, api_key_id=key.id, via_api_key=True
    )
    db_session.add(
        AgentRun(
            user_id=admin_user.id,
            definition_id=trigger.definition_id,
            spec=SPEC,
            input="earlier",
            max_cost_usd=Decimal("1"),
            trigger_kind="ingest",
            status="done",
        )
    )
    readable, hidden = post(db_session, creator_id=admin_user.id), post(db_session)
    refs = claimed(db_session, trigger, readable, hidden)

    assert fire_trigger(trigger.id, refs) is None

    db_session.expire_all()
    found = {
        m.item_id: (m.status, m.not_before is not None)
        for m in db_session.query(AgentTriggerMatch).filter_by(trigger_id=trigger.id)
    }
    assert found == {readable.id: ("pending", True), hidden.id: ("skipped:inaccessible", False)}


def test_a_delivery_whose_run_already_started_is_linked_to_it(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user)
    item = post(db_session)
    [ref] = claimed(db_session, trigger, item)
    [delivery] = db_session.query(AgentTriggerMatch).all()
    # A fire sent before deliveries were rows already started this run.
    earlier = AgentRun(
        user_id=admin_user.id,
        definition_id=trigger.definition_id,
        trigger_id=trigger.id,
        dedupe_key=triggers.dedupe_key(trigger.id, [delivery]),
        spec=SPEC,
        input="earlier",
        max_cost_usd=Decimal("1"),
        trigger_kind="ingest",
    )
    db_session.add(earlier)
    db_session.commit()

    run_id = fire_trigger(trigger.id, [ref])

    db_session.expire_all()
    assert run_id == earlier.id
    assert db_session.query(AgentRun).count() == 1
    assert (delivery.status, delivery.run_id) == ("done", earlier.id)


def test_a_backlog_over_its_max_sheds_the_oldest_and_notifies_once(
    db_session, admin_user, no_celery_dispatch
):
    trigger = setup(
        db_session, admin_user, delivery=BATCH | {"window_sec": 3600}, backlog_max=2
    )
    posts = [post(db_session) for _ in range(4)]
    for p in posts:
        route_item(db_session, p.id)
        # Oldest first, as they arrived.
        age(db_session, trigger, timedelta(seconds=1))

    first = triggers.sweep_deliveries()
    second = triggers.sweep_deliveries()

    db_session.expire_all()
    shed = db_session.query(AgentTriggerMatch).filter_by(status="skipped:shed:backlog")
    assert (first["shed"], second["shed"]) == (2, 0)
    assert sorted(m.item_id for m in shed) == [posts[0].id, posts[1].id]
    assert notifications(no_celery_dispatch) == 1


def test_a_tripped_fire_leaves_its_deliveries_pending(
    db_session, admin_user, fake_providers, admin_keys, no_celery_dispatch
):
    trigger = setup(db_session, admin_user, spec=SPEC | {"provider_key": 99999})
    route_item(db_session, post(db_session).id)

    assert fire_sent(no_celery_dispatch) == [None]

    db_session.refresh(trigger)
    assert trigger.enabled is False
    assert statuses(db_session, trigger) == ["pending"]

