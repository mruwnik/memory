from datetime import datetime, timedelta, timezone

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text

from memory.agents import triggers
from memory.common import embedding, settings
from memory.common.celery_app import FIRE_TRIGGER
from memory.common.content_processing import process_content_item
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    AgentTriggerMatch,
    BlogPost,
    Event,
)
from memory.workers.tasks.maintenance import purge_agent_runs
from tests.conftest import item_events, route_item, unique_sha256


def new_post(**fields):
    return BlogPost(
        sha256=unique_sha256("post"),
        modality="blog",
        title="Post",
        content="about agents",
        tags=["ai"],
        embed_status="RAW",
        **fields,
    )


def stored_post(db):
    post = new_post()
    db.add(post)
    db.commit()
    return post


def make_trigger(db, user, name="t"):
    definition = AgentDefinition(user_id=user.id, name=name, spec={"system_prompt": "x"})
    db.add(definition)
    db.flush()
    trigger = AgentTrigger(
        user_id=user.id,
        name=name,
        definition_id=definition.id,
        handler={"agent": definition.id},
        kind="ingest",
        config=triggers.IngestConfig.model_validate(
            {"filter": {"tags_any": ["ai"]}}
        ).model_dump(mode="json"),
    )
    db.add(trigger)
    db.commit()
    return trigger


def pending(db):
    db.expire_all()
    return [e.item_id for e in db.query(Event).filter(Event.routed_at.is_(None)).order_by(Event.id)]


@pytest.fixture
def events_at_embed(db_session, monkeypatch):
    """The events each embedding call could see, embedding nothing."""
    seen = []

    def embed(item, before_embed):
        before_embed()
        seen.append(item_events(db_session))
        return []

    monkeypatch.setattr(embedding, "embed_source_item", embed)
    return seen


@pytest.mark.parametrize("changes", [None, ["commits"]])
def test_storing_an_item_writes_its_event_before_embedding(db_session, events_at_embed, changes):
    post = new_post()

    process_content_item(post, db_session, changes=changes)

    assert events_at_embed == [[(post.id, "added", changes)]]
    assert item_events(db_session) == [(post.id, "added", changes)]


def test_reprocessing_a_stored_item_writes_a_changed_event(db_session, events_at_embed):
    post = stored_post(db_session)

    process_content_item(post, db_session, changes=["state"])

    assert item_events(db_session) == [(post.id, "changed", ["state"])]


def test_an_unchanged_item_writes_no_event(db_session, events_at_embed):
    process_content_item(new_post(), db_session, changes=[])

    assert item_events(db_session) == []


def test_deleting_an_item_writes_a_removed_event_that_outlives_it(db_session):
    post = stored_post(db_session)
    post_id = post.id

    db_session.delete(post)
    db_session.commit()

    assert db_session.get(BlogPost, post_id) is None
    assert item_events(db_session) == [(post_id, "removed", None)]


def add_events(db, *items, action="added"):
    db.add_all(Event(kind="item", action=action, item_id=i.id) for i in items)
    db.commit()


def fail_for(monkeypatch, item, error: BaseException = RuntimeError("bad item")):
    """Make matching raise for one item."""
    real_facts = triggers.item_facts

    def facts(i):
        if i.id == item.id:
            raise error
        return real_facts(i)

    monkeypatch.setattr(triggers, "item_facts", facts)


def test_a_new_item_stays_added_across_a_savepoint(db_session, events_at_embed):
    post = new_post()
    db_session.add(post)
    db_session.flush()
    with db_session.begin_nested():
        pass

    process_content_item(post, db_session)

    assert item_events(db_session) == [(post.id, "added", None)]


@pytest.mark.transactional_db
def test_the_router_routes_each_pending_event_once(db_session, admin_user, no_celery_dispatch):
    trigger = make_trigger(db_session, admin_user)
    add_events(db_session, stored_post(db_session))

    first = triggers.route_events()
    again = triggers.route_events()

    assert (first, again) == ([trigger.id], [])
    assert pending(db_session) == []
    assert [c.args[0] for c in no_celery_dispatch.call_args_list] == [FIRE_TRIGGER]


@pytest.mark.transactional_db
def test_the_router_works_through_several_batches(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(triggers, "ROUTE_BATCH_SIZE", 1)
    trigger = make_trigger(db_session, admin_user)
    add_events(db_session, *(stored_post(db_session) for _ in range(3)))

    assert triggers.route_events() == [trigger.id] * 3
    assert pending(db_session) == []


@pytest.mark.transactional_db
def test_a_run_past_its_time_budget_leaves_the_rest_to_the_next(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(triggers, "ROUTE_BATCH_SIZE", 1)
    monkeypatch.setattr(triggers, "ROUTE_RUN_SECONDS", 0)
    make_trigger(db_session, admin_user)
    first, second = stored_post(db_session), stored_post(db_session)
    add_events(db_session, first, second)

    triggers.route_events()

    assert pending(db_session) == [second.id]


@pytest.mark.transactional_db
def test_an_event_the_router_fails_on_is_retried_then_given_up(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(settings, "EVENT_MAX_ATTEMPTS", 2)
    trigger = make_trigger(db_session, admin_user)
    broken, healthy = stored_post(db_session), stored_post(db_session)
    add_events(db_session, broken, healthy)
    fail_for(monkeypatch, broken)

    first = triggers.route_events()
    left = pending(db_session)
    second = triggers.route_events()

    [failed] = db_session.query(Event).filter_by(item_id=broken.id).all()
    assert (first, left, second, pending(db_session)) == ([trigger.id], [broken.id], [], [])
    assert (failed.attempts, failed.error) == (2, "RuntimeError")


@pytest.mark.transactional_db
def test_a_soft_time_limit_stops_the_router_without_counting_an_attempt(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    make_trigger(db_session, admin_user)
    post = stored_post(db_session)
    add_events(db_session, post)
    fail_for(monkeypatch, post, SoftTimeLimitExceeded())

    with pytest.raises(SoftTimeLimitExceeded):
        triggers.route_events()

    db_session.expire_all()
    assert [(e.attempts, e.routed_at) for e in db_session.query(Event)] == [(0, None)]


@pytest.mark.transactional_db
def test_an_invalid_trigger_is_skipped_without_failing_events(
    db_session, admin_user, no_celery_dispatch
):
    broken = make_trigger(db_session, admin_user)
    broken.config = {"filter": {"no_such_filter": True}}
    db_session.commit()
    healthy = make_trigger(db_session, admin_user, name="healthy")
    add_events(db_session, stored_post(db_session))

    assert triggers.route_events() == [healthy.id]
    assert [e.attempts for e in db_session.query(Event)] == [0]


@pytest.mark.transactional_db
def test_a_failed_send_leaves_the_delivery_for_the_sweep(
    db_session, admin_user, no_celery_dispatch, monkeypatch
):
    monkeypatch.setattr(triggers, "DISPATCH_GRACE", timedelta(0))
    down = make_trigger(db_session, admin_user)
    up = make_trigger(db_session, admin_user, name="up")
    add_events(db_session, stored_post(db_session))

    def broker_down_for(name, args):
        if args[0] == down.id:
            raise ConnectionError("broker down")

    no_celery_dispatch.side_effect = broker_down_for
    routed = triggers.route_events()
    no_celery_dispatch.side_effect = None
    no_celery_dispatch.reset_mock()
    triggers.sweep_deliveries()

    assert (sorted(routed), pending(db_session)) == (sorted([down.id, up.id]), [])
    assert sorted(c.kwargs["args"][0] for c in no_celery_dispatch.call_args_list) == sorted(
        [down.id, up.id]
    )


@pytest.mark.parametrize(
    "inserted_ago,changes,backfill,on_changes,fires",
    [
        # Stored before the trigger, so only news about it counts.
        (timedelta(days=2), None, False, [], False),
        (timedelta(days=2), ["commits"], False, [], True),
        (timedelta(days=2), ["comments"], False, ["commits"], False),
        (timedelta(days=2), ["comments", "commits"], False, ["commits"], True),
        (timedelta(days=2), ["commits"], True, [], False),
        # Housekeeping isn't news.
        (timedelta(days=2), ["edited"], False, [], False),
        (timedelta(days=2), ["labels", "assignees", "draft", "checks"], False, [], False),
        (timedelta(days=2), ["labels"], False, ["labels"], False),
        (timedelta(days=2), ["checks_complete"], False, [], True),
        (timedelta(0), None, True, [], False),
        # Past claim retention, an unseen item never matches.
        (timedelta(days=31), ["commits"], False, [], False),
    ],
)
def test_an_item_older_than_the_trigger_matches_once_its_ingester_reports_changes(
    db_session, admin_user, no_celery_dispatch, inserted_ago, changes, backfill, on_changes, fires
):
    trigger = make_trigger(db_session, admin_user)
    trigger.active_since = datetime.now(timezone.utc) - timedelta(days=1)
    trigger.config = trigger.config | {"on_edit": bool(on_changes), "on_changes": on_changes}
    post = stored_post(db_session)
    post.inserted_at = datetime.now(timezone.utc) - inserted_ago
    db_session.commit()

    routed = route_item(db_session, post.id, changes, backfill=backfill)

    assert routed == ([trigger.id] if fires else [])


def test_an_event_from_before_the_trigger_never_matches_it(db_session, admin_user, no_celery_dispatch):
    trigger = make_trigger(db_session, admin_user)
    post = stored_post(db_session)

    routed = route_item(db_session, post.id, created_at=trigger.active_since - timedelta(seconds=1))

    assert routed == []


@pytest.mark.transactional_db
def test_removed_events_are_routed_without_deliveries(db_session, admin_user, no_celery_dispatch):
    make_trigger(db_session, admin_user)
    db_session.delete(stored_post(db_session))
    db_session.commit()

    assert triggers.route_events() == []
    assert pending(db_session) == []
    no_celery_dispatch.assert_not_called()


@pytest.mark.transactional_db
def test_without_triggers_pending_events_are_just_marked_routed(db_session, no_celery_dispatch):
    add_events(db_session, stored_post(db_session), stored_post(db_session))

    assert triggers.route_events() == []
    assert pending(db_session) == []


def test_old_routed_events_are_purged_and_pending_ones_kept(db_session):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=settings.EVENT_RETENTION_DAYS + 1)
    db_session.add_all(
        [
            Event(kind="item", action="added", item_id=1, routed_at=old),
            Event(kind="item", action="added", item_id=2, routed_at=now),
            Event(kind="item", action="added", item_id=3, created_at=old),
        ]
    )
    db_session.commit()

    purge_agent_runs()

    assert [e[0] for e in item_events(db_session)] == [2, 3]


def tripped_trigger(db, user, name, tripped_at):
    trigger = make_trigger(db, user, name=name)
    trigger.active_since = tripped_at - timedelta(days=1)
    trigger.enabled = False
    trigger.state = {"tripped": {"reason": "key revoked", "at": tripped_at.isoformat()}}
    db.commit()
    return trigger


def resume(db, trigger):
    triggers.resume_from_trip(db, trigger)
    trigger.enabled = True
    trigger.state = {k: v for k, v in trigger.state.items() if k != "tripped"}
    db.commit()


def routed_event(db, item, created_at, routed_at):
    db.add(Event(kind="item", action="added", item_id=item.id, created_at=created_at, routed_at=routed_at))
    db.commit()


def test_a_resumed_trigger_gets_what_it_missed_once_and_alone(
    db_session, admin_user, no_celery_dispatch
):
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    resumed = tripped_trigger(db_session, admin_user, "resumed", tripped_at)
    other = make_trigger(db_session, admin_user, name="other")
    seen, missed, waiting = (stored_post(db_session) for _ in range(3))
    # Routed before the trip, when the trigger saw it (it never claimed it, so a
    # replay of it would show).
    routed_event(db_session, seen, tripped_at - timedelta(hours=2), tripped_at - timedelta(minutes=30))
    # Written before the trip, routed while tripped: the router was behind.
    routed_event(db_session, missed, tripped_at - timedelta(minutes=1), tripped_at + timedelta(minutes=1))
    db_session.add(
        AgentTriggerMatch(trigger_id=other.id, item_id=missed.id, content_hash=b"v", status="done")
    )
    add_events(db_session, waiting)
    resume(db_session, resumed)

    routed = triggers.route_events()
    again = triggers.route_events()

    db_session.expire_all()
    claims = {
        t.id: sorted(m.item_id for m in db_session.query(AgentTriggerMatch).filter_by(trigger_id=t.id))
        for t in (resumed, other)
    }
    assert sorted(routed) == sorted([resumed.id, other.id, resumed.id])
    assert again == []
    assert claims == {resumed.id: sorted([missed.id, waiting.id]), other.id: sorted([missed.id, waiting.id])}
    assert triggers.RESUME not in db_session.get(AgentTrigger, resumed.id).state


def test_a_failed_replay_is_retried_and_counted(db_session, admin_user, no_celery_dispatch, monkeypatch):
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    resumed = tripped_trigger(db_session, admin_user, "resumed", tripped_at)
    missed = stored_post(db_session)
    routed_event(db_session, missed, tripped_at, tripped_at + timedelta(minutes=1))
    resume(db_session, resumed)

    with monkeypatch.context() as m:
        fail_for(m, missed)
        failing = triggers.route_events()
    retried = triggers.route_events()

    [event] = db_session.query(Event).all()
    db_session.refresh(event)
    assert (failing, retried, event.attempts) == ([], [resumed.id], 1)


def test_replay_gets_its_share_of_a_busy_run(db_session, admin_user, no_celery_dispatch, monkeypatch):
    monkeypatch.setattr(triggers, "ROUTE_BATCH_SIZE", 1)
    monkeypatch.setattr(triggers, "ROUTE_RUN_SECONDS", 0)
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    resumed = tripped_trigger(db_session, admin_user, "resumed", tripped_at)
    missed = stored_post(db_session)
    routed_event(db_session, missed, tripped_at, tripped_at + timedelta(minutes=1))
    resume(db_session, resumed)
    add_events(db_session, *(stored_post(db_session) for _ in range(3)))

    triggers.route_events()

    claimed = db_session.query(AgentTriggerMatch).filter_by(item_id=missed.id).count()
    assert (claimed, len(pending(db_session))) == (1, 2)


@pytest.mark.transactional_db
def test_locking_a_trigger_does_not_wait_for_a_claim_being_written(db_session, admin_user):
    trigger = make_trigger(db_session, admin_user)
    post = stored_post(db_session)

    with make_session() as router, make_session() as sweep:
        # The insert holds a key-share lock on its trigger until it commits.
        router.add(AgentTriggerMatch(trigger_id=trigger.id, item_id=post.id, content_hash=b"v"))
        router.flush()
        sweep.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked = triggers.lock_trigger(sweep, trigger.id)
        sweep.rollback()
        router.rollback()

    assert locked is not None


def test_resuming_again_extends_the_replay_under_way(db_session, admin_user):
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=2)
    trigger = tripped_trigger(db_session, admin_user, "resumed", tripped_at)
    triggers.resume_from_trip(db_session, trigger)
    first = trigger.state[triggers.RESUME] | {"after": 7}
    trigger.state = trigger.state | {
        triggers.RESUME: first,
        "tripped": {"reason": "again", "at": datetime.now(timezone.utc).isoformat()},
    }

    triggers.resume_from_trip(db_session, trigger)

    again = trigger.state[triggers.RESUME]
    assert (again["from"], again["after"]) == (first["from"], 7)
    assert again["to"] >= first["to"]
