from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from celery.exceptions import SoftTimeLimitExceeded

from memory.agents import triggers
from memory.common import embedding, settings
from memory.common.celery_app import FIRE_TRIGGER
from memory.common.content_processing import process_content_item
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    BlogPost,
    Event,
)
from memory.workers.tasks.maintenance import purge_agent_runs
from tests.conftest import item_events, unique_sha256


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
    client = fakeredis.FakeRedis()
    add_events(db_session, stored_post(db_session))

    first = triggers.route_events(client)
    again = triggers.route_events(client)

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

    assert triggers.route_events(fakeredis.FakeRedis()) == [trigger.id] * 3
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

    triggers.route_events(fakeredis.FakeRedis())

    assert pending(db_session) == [second.id]


@pytest.mark.transactional_db
def test_an_event_the_router_fails_on_is_retried_then_given_up(
    db_session, admin_user, monkeypatch, no_celery_dispatch
):
    monkeypatch.setattr(settings, "EVENT_MAX_ATTEMPTS", 2)
    trigger = make_trigger(db_session, admin_user)
    client = fakeredis.FakeRedis()
    broken, healthy = stored_post(db_session), stored_post(db_session)
    add_events(db_session, broken, healthy)
    fail_for(monkeypatch, broken)

    first = triggers.route_events(client)
    left = pending(db_session)
    second = triggers.route_events(client)

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
        triggers.route_events(fakeredis.FakeRedis())

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

    assert triggers.route_events(fakeredis.FakeRedis()) == [healthy.id]
    assert [e.attempts for e in db_session.query(Event)] == [0]


@pytest.mark.transactional_db
def test_a_failed_delivery_puts_its_event_back_for_that_trigger_alone(
    db_session, admin_user, no_celery_dispatch
):
    down = make_trigger(db_session, admin_user)
    up = make_trigger(db_session, admin_user, name="up")
    client = fakeredis.FakeRedis()
    post = stored_post(db_session)
    add_events(db_session, post)

    def broker_down_for(name, args):
        if args[0] == down.id:
            raise ConnectionError("broker down")

    no_celery_dispatch.side_effect = broker_down_for
    first = triggers.route_events(client)
    left = pending(db_session)
    no_celery_dispatch.side_effect = None

    assert (first, left) == ([up.id], [post.id])
    assert (triggers.route_events(client), pending(db_session)) == ([down.id], [])


@pytest.mark.transactional_db
def test_removed_events_are_routed_without_deliveries(db_session, admin_user, no_celery_dispatch):
    make_trigger(db_session, admin_user)
    db_session.delete(stored_post(db_session))
    db_session.commit()

    assert triggers.route_events(fakeredis.FakeRedis()) == []
    assert pending(db_session) == []
    no_celery_dispatch.assert_not_called()


@pytest.mark.transactional_db
def test_without_triggers_pending_events_are_just_marked_routed(db_session, no_celery_dispatch):
    add_events(db_session, stored_post(db_session), stored_post(db_session))

    assert triggers.route_events(fakeredis.FakeRedis()) == []
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
