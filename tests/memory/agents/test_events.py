from datetime import datetime, timedelta, timezone
from functools import reduce

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text

from memory.agents import subscriptions
from memory.common import embedding, settings
from memory.common.celery_app import FIRE_SUBSCRIPTION
from memory.common.content_processing import process_content_item, safe_task_execution
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentDefinition,
    Subscription,
    Delivery,
    BlogPost,
    Event,
)
from memory.common.db.models.events import MAX_RAW_PAYLOAD_BYTES, record_raw_event
from memory.workers.tasks.maintenance import purge_agent_runs
from tests.conftest import item_events, raw_events, route_item, unique_sha256


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


def make_subscription(db, user, name="t"):
    definition = AgentDefinition(user_id=user.id, name=name, spec={"system_prompt": "x"})
    db.add(definition)
    db.flush()
    subscription = Subscription(
        user_id=user.id,
        name=name,
        definition_id=definition.id,
        handler={"agent": definition.id},
        kind="ingest",
        config=subscriptions.IngestConfig.model_validate(
            {"filter": {"tags_any": ["ai"]}}
        ).model_dump(mode="json"),
    )
    db.add(subscription)
    db.commit()
    return subscription


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


def raw_event(db, delivery_id="d-1", outcome="no_item_type", payload=None, source="github", account_id=1) -> int | None:
    return record_raw_event(
        db,
        source=source,
        event_type="push",
        account_id=account_id,
        owner_id=None,
        delivery_id=delivery_id,
        outcome=outcome,
        payload=payload,
    )


def test_a_task_given_caused_by_links_the_item_events_it_writes(db_session, events_at_embed):
    raw_id = raw_event(db_session)
    gone = stored_post(db_session)
    # The link outlives its raw event, as for a job replayed after the purge.
    db_session.query(Event).filter(Event.id == raw_id).delete()
    db_session.commit()

    @safe_task_execution
    def store(post):
        process_content_item(post, db_session)

    @safe_task_execution
    def ingest(post):
        store(post)
        db_session.delete(gone)
        db_session.commit()

    ingest(new_post(), caused_by=raw_id)
    store(new_post())

    db_session.expire_all()
    linked = db_session.query(Event.action, Event.caused_by).filter(Event.kind == "item").order_by(Event.id)
    assert linked.all() == [("added", raw_id), ("removed", raw_id), ("added", None)]


def test_a_delivery_is_recorded_once_per_source_account(db_session):
    first = raw_event(db_session)
    again = raw_event(db_session)
    other_account = raw_event(db_session, account_id=2)
    other_source = raw_event(db_session, source="slack")
    db_session.commit()

    assert again is None
    assert [e.id for e in raw_events(db_session)] == [first, other_account, other_source]


@pytest.mark.parametrize(
    "outcome, payload, kept, with_payload",
    [
        ("no_item_type", {"text": "a\x00b\ud800", "list": ["\x00"]}, {"text": "ab", "list": [""]}, 1),
        ("no_item_type", {"n": [float("nan"), float("inf"), 1.5]}, {"n": [None, None, 1.5]}, 1),
        ("no_item_type", {"text": "x" * (MAX_RAW_PAYLOAD_BYTES + 1)}, {"truncated": True, "bytes": MAX_RAW_PAYLOAD_BYTES + 13}, 1),
        ("no_item_type", {"deep": reduce(lambda inner, _: [inner], range(5000), [])}, {"truncated": True}, 1),
        ("stored", {"text": "in the item"}, None, 0),
        ("not_collected", {"text": "secret"}, None, 0),
        ("rejected", {"text": "bad"}, None, 0),
    ],
)
def test_only_deliveries_that_store_nothing_keep_their_payload(db_session, outcome, payload, kept, with_payload):
    raw_event(db_session, outcome=outcome, payload=payload)
    db_session.commit()

    assert [e.payload for e in raw_events(db_session)] == [kept]
    assert db_session.query(Event).filter(Event.payload.is_not(None)).count() == with_payload


@pytest.mark.parametrize("outcome, container", [("not_collected", None), ("stored", "C1"), ("rejected", "C1")])
def test_a_delivery_not_collected_never_names_its_channel(db_session, outcome, container):
    record_raw_event(
        db_session, source="slack", event_type="message", account_id=1, owner_id=None,
        delivery_id="d-1", outcome=outcome, container="C1",
    )
    db_session.commit()

    assert [e.container for e in raw_events(db_session)] == [container]


def test_a_delivery_naming_itself_in_text_postgres_cannot_hold_is_recorded_without_it(db_session):
    record_raw_event(
        db_session, source="slack", event_type="mess\ud800age", account_id=1, owner_id=None,
        delivery_id="d-\ud800", outcome="stored", container="C\ud8001",
    )
    db_session.commit()

    assert [(e.type, e.delivery_id, e.container) for e in raw_events(db_session)] == [("message", "d-", "C1")]


@pytest.mark.transactional_db
def test_raw_events_are_routed_without_deliveries(db_session, admin_user, no_celery_dispatch):
    make_subscription(db_session, admin_user)
    raw_event(db_session, payload={"a": 1})
    db_session.commit()

    assert subscriptions.route_events() == []
    assert [e.routed_at is not None for e in raw_events(db_session)] == [True]


def add_events(db, *items, action="added"):
    db.add_all(Event(kind="item", action=action, item_id=i.id) for i in items)
    db.commit()


def fail_for(monkeypatch, item, error: BaseException = RuntimeError("bad item")):
    """Make matching raise for one item."""
    real_facts = subscriptions.item_facts

    def facts(i, **kwargs):
        if i.id == item.id:
            raise error
        return real_facts(i, **kwargs)

    monkeypatch.setattr(subscriptions, "item_facts", facts)


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
    subscription = make_subscription(db_session, admin_user)
    add_events(db_session, stored_post(db_session))

    first = subscriptions.route_events()
    again = subscriptions.route_events()

    assert (first, again) == ([subscription.id], [])
    assert pending(db_session) == []
    assert [c.args[0] for c in no_celery_dispatch.call_args_list] == [FIRE_SUBSCRIPTION]


@pytest.mark.transactional_db
def test_the_router_works_through_several_batches(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(subscriptions, "ROUTE_BATCH_SIZE", 1)
    subscription = make_subscription(db_session, admin_user)
    add_events(db_session, *(stored_post(db_session) for _ in range(3)))

    assert subscriptions.route_events() == [subscription.id] * 3
    assert pending(db_session) == []


@pytest.mark.transactional_db
def test_a_run_past_its_time_budget_leaves_the_rest_to_the_next(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(subscriptions, "ROUTE_BATCH_SIZE", 1)
    monkeypatch.setattr(subscriptions, "ROUTE_RUN_SECONDS", 0)
    make_subscription(db_session, admin_user)
    first, second = stored_post(db_session), stored_post(db_session)
    add_events(db_session, first, second)

    subscriptions.route_events()

    assert pending(db_session) == [second.id]


@pytest.mark.transactional_db
def test_an_event_the_router_fails_on_is_retried_then_given_up(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(settings, "EVENT_MAX_ATTEMPTS", 2)
    subscription = make_subscription(db_session, admin_user)
    broken, healthy = stored_post(db_session), stored_post(db_session)
    add_events(db_session, broken, healthy)
    fail_for(monkeypatch, broken)

    first = subscriptions.route_events()
    left = pending(db_session)
    second = subscriptions.route_events()

    [failed] = db_session.query(Event).filter_by(item_id=broken.id).all()
    assert (first, left, second, pending(db_session)) == ([subscription.id], [broken.id], [], [])
    assert (failed.attempts, failed.error) == (2, "RuntimeError")


@pytest.mark.transactional_db
def test_a_soft_time_limit_stops_the_router_without_counting_an_attempt(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    make_subscription(db_session, admin_user)
    post = stored_post(db_session)
    add_events(db_session, post)
    fail_for(monkeypatch, post, SoftTimeLimitExceeded())

    with pytest.raises(SoftTimeLimitExceeded):
        subscriptions.route_events()

    db_session.expire_all()
    assert [(e.attempts, e.routed_at) for e in db_session.query(Event)] == [(0, None)]


@pytest.mark.transactional_db
def test_an_invalid_subscription_is_skipped_without_failing_events(
    db_session, admin_user, no_celery_dispatch
):
    broken = make_subscription(db_session, admin_user)
    broken.config = {"filter": {"no_such_filter": True}}
    db_session.commit()
    healthy = make_subscription(db_session, admin_user, name="healthy")
    add_events(db_session, stored_post(db_session))

    assert subscriptions.route_events() == [healthy.id]
    assert [e.attempts for e in db_session.query(Event)] == [0]


@pytest.mark.transactional_db
def test_a_failed_send_leaves_the_delivery_for_the_sweep(
    db_session, admin_user, no_celery_dispatch, monkeypatch
):
    monkeypatch.setattr(subscriptions, "DISPATCH_GRACE", timedelta(0))
    down = make_subscription(db_session, admin_user)
    up = make_subscription(db_session, admin_user, name="up")
    add_events(db_session, stored_post(db_session))

    def broker_down_for(name, args):
        if args[0] == down.id:
            raise ConnectionError("broker down")

    no_celery_dispatch.side_effect = broker_down_for
    routed = subscriptions.route_events()
    no_celery_dispatch.side_effect = None
    no_celery_dispatch.reset_mock()
    subscriptions.sweep_deliveries()

    assert (sorted(routed), pending(db_session)) == (sorted([down.id, up.id]), [])
    assert sorted(c.kwargs["args"][0] for c in no_celery_dispatch.call_args_list) == sorted(
        [down.id, up.id]
    )


@pytest.mark.parametrize(
    "inserted_ago,changes,backfill,on_changes,fires",
    [
        # Stored before the subscription, so only news about it counts.
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
def test_an_item_older_than_the_subscription_matches_once_its_ingester_reports_changes(
    db_session, admin_user, no_celery_dispatch, inserted_ago, changes, backfill, on_changes, fires
):
    subscription = make_subscription(db_session, admin_user)
    subscription.active_since = datetime.now(timezone.utc) - timedelta(days=1)
    subscription.config = subscription.config | {"on_edit": bool(on_changes), "on_changes": on_changes}
    post = stored_post(db_session)
    post.inserted_at = datetime.now(timezone.utc) - inserted_ago
    db_session.commit()

    routed = route_item(db_session, post.id, changes, backfill=backfill)

    assert routed == ([subscription.id] if fires else [])


def test_an_event_from_before_the_subscription_never_matches_it(db_session, admin_user, no_celery_dispatch):
    subscription = make_subscription(db_session, admin_user)
    post = stored_post(db_session)

    routed = route_item(db_session, post.id, created_at=subscription.active_since - timedelta(seconds=1))

    assert routed == []


@pytest.mark.transactional_db
def test_removed_events_are_routed_without_deliveries(db_session, admin_user, no_celery_dispatch):
    make_subscription(db_session, admin_user)
    db_session.delete(stored_post(db_session))
    db_session.commit()

    assert subscriptions.route_events() == []
    assert pending(db_session) == []
    no_celery_dispatch.assert_not_called()


@pytest.mark.transactional_db
@pytest.mark.parametrize(
    "on_remove, earlier, removals",
    [(True, "done", 1), (False, "done", 0), (True, "pending", 0), (True, "skipped:inaccessible", 0)],
)
def test_a_removal_reaches_on_remove_subscriptions_that_were_delivered_the_item(
    db_session, admin_user, no_celery_dispatch, on_remove, earlier, removals
):
    subscription = make_subscription(db_session, admin_user)
    subscription.config = subscription.config | {"on_remove": on_remove}
    post = stored_post(db_session)
    post_id = post.id
    route_item(db_session, post_id)
    db_session.query(Delivery).update({"status": earlier})
    db_session.delete(post)
    db_session.commit()

    subscriptions.route_events()

    db_session.expire_all()
    removed = db_session.query(Delivery).filter(Delivery.changes == ["removed"])
    assert [(m.item_id, m.status, m.edited) for m in removed] == [(post_id, "pending", True)] * removals


@pytest.mark.transactional_db
def test_without_subscriptions_pending_events_are_just_marked_routed(db_session, no_celery_dispatch):
    add_events(db_session, stored_post(db_session), stored_post(db_session))

    assert subscriptions.route_events() == []
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


def tripped_subscription(db, user, name, tripped_at):
    subscription = make_subscription(db, user, name=name)
    subscription.active_since = tripped_at - timedelta(days=1)
    subscription.enabled = False
    subscription.state = {"tripped": {"reason": "key revoked", "at": tripped_at.isoformat()}}
    db.commit()
    return subscription


def resume(db, subscription):
    subscriptions.resume_from_trip(db, subscription)
    subscription.enabled = True
    subscription.state = {k: v for k, v in subscription.state.items() if k != "tripped"}
    db.commit()


def routed_event(db, item, created_at, routed_at):
    db.add(Event(kind="item", action="added", item_id=item.id, created_at=created_at, routed_at=routed_at))
    db.commit()


def test_a_resumed_subscription_gets_what_it_missed_once_and_alone(
    db_session, admin_user, no_celery_dispatch
):
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    resumed = tripped_subscription(db_session, admin_user, "resumed", tripped_at)
    other = make_subscription(db_session, admin_user, name="other")
    seen, missed, waiting = (stored_post(db_session) for _ in range(3))
    # Routed before the trip, when the subscription saw it (it never claimed it, so a
    # replay of it would show).
    routed_event(db_session, seen, tripped_at - timedelta(hours=2), tripped_at - timedelta(minutes=30))
    # Written before the trip, routed while tripped: the router was behind.
    routed_event(db_session, missed, tripped_at - timedelta(minutes=1), tripped_at + timedelta(minutes=1))
    db_session.add(
        Delivery(subscription_id=other.id, item_id=missed.id, content_hash=b"v", status="done")
    )
    add_events(db_session, waiting)
    resume(db_session, resumed)

    routed = subscriptions.route_events()
    again = subscriptions.route_events()

    db_session.expire_all()
    claims = {
        t.id: sorted(m.item_id for m in db_session.query(Delivery).filter_by(subscription_id=t.id))
        for t in (resumed, other)
    }
    assert sorted(routed) == sorted([resumed.id, other.id, resumed.id])
    assert again == []
    assert claims == {resumed.id: sorted([missed.id, waiting.id]), other.id: sorted([missed.id, waiting.id])}
    assert subscriptions.RESUME not in db_session.get(Subscription, resumed.id).state


def test_a_failed_replay_is_retried_and_counted(db_session, admin_user, no_celery_dispatch, monkeypatch):
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    resumed = tripped_subscription(db_session, admin_user, "resumed", tripped_at)
    missed = stored_post(db_session)
    routed_event(db_session, missed, tripped_at, tripped_at + timedelta(minutes=1))
    resume(db_session, resumed)

    with monkeypatch.context() as m:
        fail_for(m, missed)
        failing = subscriptions.route_events()
    retried = subscriptions.route_events()

    [event] = db_session.query(Event).all()
    db_session.refresh(event)
    assert (failing, retried, event.attempts) == ([], [resumed.id], 1)


def test_replay_gets_its_share_of_a_busy_run(db_session, admin_user, no_celery_dispatch, monkeypatch):
    monkeypatch.setattr(subscriptions, "ROUTE_BATCH_SIZE", 1)
    monkeypatch.setattr(subscriptions, "ROUTE_RUN_SECONDS", 0)
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    resumed = tripped_subscription(db_session, admin_user, "resumed", tripped_at)
    missed = stored_post(db_session)
    routed_event(db_session, missed, tripped_at, tripped_at + timedelta(minutes=1))
    resume(db_session, resumed)
    add_events(db_session, *(stored_post(db_session) for _ in range(3)))

    subscriptions.route_events()

    claimed = db_session.query(Delivery).filter_by(item_id=missed.id).count()
    assert (claimed, len(pending(db_session))) == (1, 2)


@pytest.mark.transactional_db
def test_locking_a_subscription_does_not_wait_for_a_claim_being_written(db_session, admin_user):
    subscription = make_subscription(db_session, admin_user)
    post = stored_post(db_session)

    with make_session() as router, make_session() as sweep:
        # The insert holds a key-share lock on its subscription until it commits.
        router.add(Delivery(subscription_id=subscription.id, item_id=post.id, content_hash=b"v"))
        router.flush()
        sweep.execute(text("SET LOCAL lock_timeout = '2s'"))
        locked = subscriptions.lock_subscription(sweep, subscription.id)
        sweep.rollback()
        router.rollback()

    assert locked is not None


def test_resuming_again_extends_the_replay_under_way(db_session, admin_user):
    tripped_at = datetime.now(timezone.utc) - timedelta(hours=2)
    subscription = tripped_subscription(db_session, admin_user, "resumed", tripped_at)
    subscriptions.resume_from_trip(db_session, subscription)
    first = subscription.state[subscriptions.RESUME] | {"after": 7}
    subscription.state = subscription.state | {
        subscriptions.RESUME: first,
        "tripped": {"reason": "again", "at": datetime.now(timezone.utc).isoformat()},
    }

    subscriptions.resume_from_trip(db_session, subscription)

    again = subscription.state[subscriptions.RESUME]
    assert (again["from"], again["after"]) == (first["from"], 7)
    assert again["to"] >= first["to"]


def test_raw_payloads_expire_but_their_events_are_kept(db_session):
    raw_event(db_session, delivery_id="old", payload={"a": 1})
    raw_event(db_session, delivery_id="new", payload={"a": 2})
    db_session.query(Event).filter(Event.delivery_id == "old").update(
        {"created_at": datetime.now(timezone.utc) - timedelta(days=settings.EVENT_RAW_PAYLOAD_DAYS + 1)}
    )
    db_session.commit()

    assert purge_agent_runs()["purged_raw_payloads"] == 1
    assert [(e.delivery_id, e.payload) for e in raw_events(db_session)] == [("old", None), ("new", {"a": 2})]
