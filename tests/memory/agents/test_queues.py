# pyright: reportFunctionMemberAccess=false
"""Queue handlers: a subscription's deliveries wait for its owner's workers to claim
them by queue name, rendered and access-checked at claim, and reported back on the run."""

from datetime import datetime, timedelta, timezone

import pytest

from memory.agents import queues, subscriptions
from memory.agents.fire import fire_subscription
from memory.agents.work import sweep_worker_runs
from memory.api.MCP.servers import agent
from memory.common import settings
from memory.common.celery_app import FIRE_SUBSCRIPTION
from memory.common.db.models import (
    AgentDefinition,
    Subscription,
    Delivery,
    AgentRun,
    APIKey,
    DiscordChannel,
    DiscordMessage,
    SlackChannel,
    SlackMessage,
    SlackWorkspace,
)
from tests.conftest import mcp_auth_context, route_item, unique_sha256
from tests.memory.agents.test_deliveries import BATCH, age, post, statuses
from tests.memory.agents.test_subscriptions import posted_run
from tests.memory.agents.test_work import backdate, expire_lease, submit_run

QUEUE = "reviews"


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", 60)


def subscribe(db, user, queue=QUEUE, delivery=None, **fields) -> Subscription:
    config = {"filter": {"tags_any": ["ai"]}, "template": "Look: {items}"}
    config |= {"delivery": delivery} if delivery else {}
    config |= {"on_edit": True} if fields.pop("on_edit", False) else {}
    config |= {"on_remove": True} if fields.pop("on_remove", False) else {}
    t = Subscription(
        user_id=user.id,
        name=fields.pop("name", f"q-{queue}"),
        handler={"queue": queue},
        kind="ingest",
        config=subscriptions.IngestConfig.model_validate(config).model_dump(mode="json"),
        **fields,
    )
    db.add(t)
    db.commit()
    return t


def edit(db, item, content: str):
    item.content = content
    db.commit()
    route_item(db, item.id, changes=["edited"])


async def claim(token: str, **kwargs) -> dict | None:
    with mcp_auth_context(token):
        return (await agent.work_claim.fn(**kwargs))["run"]


async def submit(token: str, run: dict, result: str = "ok") -> dict:
    with mcp_auth_context(token):
        return await agent.work_submit.fn(
            run_id=run["id"], lease_token=run["lease_token"], result=result
        )


def fires(dispatch) -> list:
    return [c for c in dispatch.call_args_list if c.args[0] == FIRE_SUBSCRIPTION]


def placed_on_workers(db, user) -> Subscription:
    """A batch subscription whose definition's runs go to workers."""
    d = AgentDefinition(
        user_id=user.id,
        name="watcher",
        spec={"system_prompt": "x", "model": "anthropic/m", "placement": "runner_only"},
    )
    db.add(d)
    db.flush()
    subscription = Subscription(
        user_id=user.id,
        name="t",
        definition_id=d.id,
        handler={"agent": d.id},
        kind="ingest",
        config=subscriptions.IngestConfig.model_validate(
            {"filter": {"tags_any": ["ai"]}, "delivery": BATCH}
        ).model_dump(mode="json"),
    )
    db.add(subscription)
    db.commit()
    return subscription


def fired(db, subscription, dispatch, *items) -> str:
    """Route the items to the batch subscription and start its run; returns the run's id."""
    for item in items:
        route_item(db, item.id)
    age(db, subscription, timedelta(minutes=2))
    subscriptions.sweep_deliveries()
    [run_id] = [fire_subscription(*c.kwargs["args"]) for c in fires(dispatch)]
    dispatch.reset_mock()
    assert run_id is not None
    return run_id


def run_status(db, run_id: str) -> str:
    db.expire_all()
    run = db.get(AgentRun, run_id)
    assert run is not None
    return run.status


def narrow_key(db, user) -> APIKey:
    """A worker key that can read only what's in projects its owner is in, unlike the owner's admin session."""
    key = APIKey.create(user_id=user.id, name="narrow", scopes=["agents:work", "read"])
    db.add(key)
    db.commit()
    return key


def thread_messages(db, count: int) -> list[SlackMessage]:
    db.add(SlackWorkspace(id="T1", name="w"))
    db.add(SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel"))
    messages = [
        SlackMessage(
            modality="message",
            sha256=unique_sha256("slack"),
            message_ts=f"{i + 1}.0",
            thread_ts="1.0",
            channel_id="C1",
            workspace_id="T1",
            content=f"message {i}",
            tags=["ai"],
        )
        for i in range(count)
    ]
    db.add_all(messages)
    db.commit()
    return messages


@pytest.mark.asyncio
async def test_a_queue_delivery_is_claimed_by_its_queue_rendered_now_and_reported_on_its_run(
    db_session, regular_user, worker_session, no_celery_dispatch
):
    subscription = subscribe(db_session, regular_user)
    item = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    item.content = "rewritten after routing"
    db_session.commit()

    other_queue = await claim(worker_session.token, queue="other")
    no_queue = await claim(worker_session.token)
    claimed = await claim(worker_session.token, queue=QUEUE, worker="laptop")
    assert claimed is not None
    reported = await submit(worker_session.token, claimed, "reviewed")

    [delivery] = db_session.query(Delivery).all()
    assert (other_queue, no_queue, fires(no_celery_dispatch)) == (None, None, [])
    assert "rewritten after routing" in claimed["input"]
    assert claimed["input"].startswith("Look: ")
    assert (claimed["queue"], claimed["ref"]) == (
        QUEUE,
        {"subscription_id": subscription.id, "item_ids": [item.id], "changes": {str(item.id): ["new"]}},
    )
    assert (delivery.status, delivery.run_id) == ("done", claimed["id"])
    assert (reported["status"], reported["result"]) == ("done", {"text": "reviewed"})


@pytest.mark.asyncio
async def test_a_removed_item_reaches_its_queue_after_its_earlier_delivery_is_answered(
    db_session, regular_user, worker_session
):
    subscription = subscribe(db_session, regular_user, on_remove=True, order_by="item")
    item = post(db_session, creator_id=regular_user.id)
    item_id = item.id
    route_item(db_session, item_id)
    first = await claim(worker_session.token, queue=QUEUE)
    db_session.delete(item)
    db_session.commit()
    subscriptions.route_events()

    held = await claim(worker_session.token, queue=QUEUE)
    assert first is not None
    await submit(worker_session.token, first)
    removal = await claim(worker_session.token, queue=QUEUE)

    assert held is None
    assert removal is not None
    assert removal["ref"] == {
        "subscription_id": subscription.id,
        "item_ids": [item_id],
        "changes": {str(item_id): ["removed"]},
    }
    assert removal["input"] == f"Look: - [item #{item_id}] removed"


@pytest.mark.asyncio
async def test_a_delivery_whose_item_was_hidden_since_routing_is_skipped_at_claim(
    db_session, regular_user, admin_user, worker_session
):
    subscription = subscribe(db_session, regular_user)
    hidden = post(db_session, creator_id=regular_user.id)
    kept = post(db_session, creator_id=regular_user.id)
    route_item(db_session, hidden.id)
    route_item(db_session, kept.id)
    hidden.creator_id = admin_user.id
    db_session.commit()

    claimed = await claim(worker_session.token, queue=QUEUE)

    assert claimed is not None
    assert claimed["ref"]["item_ids"] == [kept.id]
    assert statuses(db_session, subscription) == ["done", "skipped:inaccessible"]


@pytest.mark.asyncio
async def test_a_key_without_read_cant_claim_queue_deliveries_and_they_wait(
    db_session, regular_user, no_celery_dispatch
):
    regular_user.scopes = [*regular_user.scopes, "agents:work"]
    key = APIKey.create(user_id=regular_user.id, name="work only", scopes=["agents:work"])
    db_session.add(key)
    db_session.commit()
    subscription = subscribe(db_session, regular_user)
    route_item(db_session, post(db_session, creator_id=regular_user.id).id)

    with pytest.raises(ValueError, match="read"):
        await claim(key.key, queue=QUEUE)

    assert statuses(db_session, subscription) == ["pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("order_by,held", [("item", True), ("none", False)])
async def test_order_by_holds_an_items_later_delivery_until_the_earlier_one_is_reported(
    db_session, regular_user, worker_session, order_by, held
):
    subscribe(db_session, regular_user, order_by=order_by, on_edit=True)
    item = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    edit(db_session, item, "second version")

    first = await claim(worker_session.token, queue=QUEUE)
    second = await claim(worker_session.token, queue=QUEUE)
    assert first is not None
    await submit(worker_session.token, first)
    after_report = await claim(worker_session.token, queue=QUEUE)

    assert (second is None, after_report is None) == (held, not held)
    assert "second version" in (second or after_report or {})["input"]


@pytest.mark.asyncio
async def test_coalescing_replaces_an_unclaimed_delivery_with_the_latest_and_keeps_its_changes(
    db_session, regular_user, worker_session
):
    subscription = subscribe(db_session, regular_user, coalesce_mode="latest", on_edit=True)
    item = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    edit(db_session, item, "latest version")

    claimed = await claim(worker_session.token, queue=QUEUE)
    again = await claim(worker_session.token, queue=QUEUE)

    assert claimed is not None
    assert "latest version" in claimed["input"]
    assert claimed["ref"]["changes"] == {str(item.id): ["new"]}
    assert claimed["ref"]["coalesced"] == {str(item.id): [item.id]}
    assert again is None
    assert statuses(db_session, subscription) == ["done", "skipped:coalesced"]


@pytest.mark.asyncio
async def test_a_lapsed_queue_run_goes_back_to_its_queue_rendered_afresh(
    db_session, regular_user, worker_session
):
    subscribe(db_session, regular_user)
    item = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    first = await claim(worker_session.token, queue=QUEUE)
    assert first is not None
    expire_lease(db_session, first["id"])
    sweep_worker_runs()
    item.content = "changed while lapsed"
    db_session.commit()

    generic = await claim(worker_session.token)
    again = await claim(worker_session.token, queue=QUEUE)

    assert generic is None
    assert again is not None
    assert again["id"] == first["id"]
    assert "changed while lapsed" in again["input"]


@pytest.mark.asyncio
async def test_an_agent_run_placed_on_workers_is_rendered_and_checked_at_claim(
    db_session, regular_user, admin_user, worker_session, no_celery_dispatch
):
    regular_user.scopes = [*regular_user.scopes, "agents"]
    subscription = placed_on_workers(db_session, regular_user)
    hidden = post(db_session, creator_id=regular_user.id)
    kept = post(db_session, creator_id=regular_user.id)
    run_id = fired(db_session, subscription, no_celery_dispatch, hidden, kept)
    hidden.creator_id = admin_user.id
    kept.content = "edited after the run started"
    db_session.commit()

    claimed = await claim(worker_session.token)

    assert claimed is not None
    assert claimed["id"] == run_id
    assert "edited after the run started" in claimed["input"]
    assert f"id={hidden.id}" not in claimed["input"]
    assert statuses(db_session, subscription) == ["done", "skipped:inaccessible"]


@pytest.mark.asyncio
async def test_a_queue_subscription_over_its_hourly_rate_defers_its_deliveries(
    db_session, regular_user, worker_session
):
    subscription = subscribe(db_session, regular_user, max_per_hour=1)
    for _ in range(2):
        route_item(db_session, post(db_session, creator_id=regular_user.id).id)

    first = await claim(worker_session.token, queue=QUEUE)
    second = await claim(worker_session.token, queue=QUEUE)

    deferred = db_session.query(Delivery).filter_by(status="pending").one()
    db_session.refresh(subscription)
    assert (first is not None, second) == (True, None)
    assert deferred.not_before is not None
    assert "throttled" in subscription.state


@pytest.mark.asyncio
async def test_a_queue_batch_is_claimed_together_once_its_window_closes(
    db_session, regular_user, worker_session
):
    subscription = subscribe(db_session, regular_user, delivery=BATCH)
    posts = [post(db_session, creator_id=regular_user.id) for _ in range(2)]
    for p in posts:
        route_item(db_session, p.id)

    early = await claim(worker_session.token, queue=QUEUE)
    age(db_session, subscription, timedelta(minutes=2))
    claimed = await claim(worker_session.token, queue=QUEUE)

    assert early is None
    assert claimed is not None
    assert claimed["ref"]["item_ids"] == [p.id for p in posts]
    assert statuses(db_session, subscription) == ["done", "done"]


def test_the_sweep_sheds_a_queue_backlog_but_sends_nothing(
    db_session, regular_user, no_celery_dispatch
):
    subscription = subscribe(db_session, regular_user, backlog_max=1)
    for _ in range(2):
        route_item(db_session, post(db_session, creator_id=regular_user.id).id)

    swept = subscriptions.sweep_deliveries()

    assert (swept["shed"], swept["sent"]) == (1, 0)
    assert statuses(db_session, subscription) == ["pending", "skipped:shed:backlog"]
    assert fires(no_celery_dispatch) == []


@pytest.mark.parametrize(
    "item,order_by,key",
    [
        (SlackMessage(id=1, channel_id="C1", message_ts="1.0", thread_ts=None), "thread", "slack:C1:1.0"),
        (SlackMessage(id=2, channel_id="C1", message_ts="2.0", thread_ts="1.0"), "thread", "slack:C1:1.0"),
        (DiscordMessage(id=3, channel_id=77, message_id=5), "thread", "discord:5"),
        (
            DiscordMessage(id=4, channel_id=5, message_id=6, channel=DiscordChannel(id=5, parent_id=77)),
            "thread",
            "discord:5",
        ),
        (DiscordMessage(id=3, channel_id=77, message_id=5), "item", "item:3"),
        (DiscordMessage(id=3, channel_id=77, message_id=5), "none", None),
    ],
)
def test_order_keys(item, order_by, key):
    assert subscriptions.order_key(item, order_by) == key


@pytest.mark.asyncio
async def test_a_queue_delivery_a_narrow_key_cant_read_waits_for_a_worker_that_can(
    db_session, admin_user, admin_session
):
    subscription = subscribe(db_session, admin_user)
    route_item(db_session, post(db_session).id)
    narrow = narrow_key(db_session, admin_user)

    passed_over = await claim(narrow.key, queue=QUEUE)
    left = statuses(db_session, subscription)
    taken = await claim(admin_session.token, queue=QUEUE)

    assert (passed_over, left) == (None, ["pending"])
    assert taken is not None
    assert statuses(db_session, subscription) == ["done"]


@pytest.mark.asyncio
async def test_a_worker_run_a_narrow_key_cant_read_waits_for_a_worker_that_can(
    db_session, admin_user, admin_session, no_celery_dispatch
):
    subscription = placed_on_workers(db_session, admin_user)
    run_id = fired(db_session, subscription, no_celery_dispatch, post(db_session))
    narrow = narrow_key(db_session, admin_user)

    passed_over = await claim(narrow.key)
    left_status = run_status(db_session, run_id)
    taken = await claim(admin_session.token)

    assert (passed_over, left_status) == (None, "pending")
    assert taken is not None
    assert taken["id"] == run_id
    assert statuses(db_session, subscription) == ["done"]


@pytest.mark.asyncio
async def test_a_queue_subscription_whose_config_broke_trips_and_the_queue_goes_on(
    db_session, regular_user, worker_session
):
    broken = subscribe(db_session, regular_user)
    route_item(db_session, post(db_session, creator_id=regular_user.id).id)
    broken.config = broken.config | {"filter": {}}
    db_session.commit()
    healthy = subscribe(db_session, regular_user, name="healthy")
    route_item(db_session, post(db_session, creator_id=regular_user.id).id)

    claimed = await claim(worker_session.token, queue=QUEUE)

    db_session.refresh(broken)
    assert claimed is not None
    assert claimed["ref"]["subscription_id"] == healthy.id
    assert (broken.enabled, "tripped" in broken.state) == (False, True)


@pytest.mark.asyncio
async def test_a_worker_run_whose_subscription_config_broke_fails_cleared_and_the_next_is_claimed(
    db_session, regular_user, worker_session, no_celery_dispatch
):
    regular_user.scopes = [*regular_user.scopes, "agents"]
    subscription = placed_on_workers(db_session, regular_user)
    item = post(db_session, creator_id=regular_user.id)
    broken_run = fired(db_session, subscription, no_celery_dispatch, item)
    subscription.config = subscription.config | {"filter": {}}
    db_session.commit()
    next_run = submit_run(db_session, regular_user)
    # The broken run is first in line.
    backdate(db_session, broken_run, minutes=1)

    claimed = await claim(worker_session.token)

    db_session.expire_all()
    failed = db_session.get(AgentRun, broken_run)
    assert failed is not None
    assert claimed is not None
    assert claimed["id"] == next_run
    assert (failed.status, failed.input, failed.trigger_ref) == ("failed", "", {})


@pytest.mark.asyncio
async def test_a_delivery_whose_claim_keeps_failing_fails_after_its_attempts(
    db_session, regular_user, worker_session, monkeypatch
):
    monkeypatch.setattr(settings, "EVENT_MAX_ATTEMPTS", 2)

    def broken(*args):
        raise RuntimeError("render bug")

    monkeypatch.setattr(queues, "rendered", broken)
    subscribe(db_session, regular_user)
    route_item(db_session, post(db_session, creator_id=regular_user.id).id)

    claims = []
    for _ in range(3):
        claims.append(await claim(worker_session.token, queue=QUEUE))
        # The next attempt waits for its retry.
        db_session.query(Delivery).update({"not_before": None})
        db_session.commit()

    db_session.expire_all()
    [delivery] = db_session.query(Delivery).all()
    assert claims == [None, None, None]
    assert (delivery.status, delivery.attempts, delivery.error) == ("failed", 2, "RuntimeError")


@pytest.mark.asyncio
async def test_a_failed_claim_is_retried_only_after_a_wait(
    db_session, regular_user, worker_session, monkeypatch
):
    """Workers poll every second, so a transient error would use up every attempt at once."""

    def broken(*args):
        raise RuntimeError("render bug")

    monkeypatch.setattr(queues, "rendered", broken)
    subscribe(db_session, regular_user)
    route_item(db_session, post(db_session, creator_id=regular_user.id).id)

    await claim(worker_session.token, queue=QUEUE)
    await claim(worker_session.token, queue=QUEUE)

    db_session.expire_all()
    [delivery] = db_session.query(Delivery).all()
    wait = delivery.not_before - datetime.now(timezone.utc)
    assert (delivery.status, delivery.attempts) == ("pending", 1)
    assert timedelta(seconds=30) < wait <= queues.CLAIM_RETRY


def posted_reply(db, user) -> SlackMessage:
    [reply] = thread_messages(db, 1)
    reply.creator_id = user.id
    db.commit()
    return reply


@pytest.mark.asyncio
async def test_a_claim_skips_what_the_subscription_reported_posting_after_routing(
    db_session, regular_user, worker_session
):
    """A worker's reply can be stored and routed before its report says it posted it."""
    subscription = subscribe(db_session, regular_user)
    reply = posted_reply(db_session, regular_user)
    route_item(db_session, reply.id)
    posted_run(db_session, subscription, [{"source": "slack", "external_id": "C1:1.0"}])

    claimed = await claim(worker_session.token, queue=QUEUE)

    assert claimed is None
    assert statuses(db_session, subscription) == ["skipped:posted"]


@pytest.mark.asyncio
async def test_a_lapsed_run_drops_what_the_subscription_reported_posting_since(
    db_session, regular_user, worker_session
):
    subscription = subscribe(db_session, regular_user)
    reply = posted_reply(db_session, regular_user)
    route_item(db_session, reply.id)
    first = await claim(worker_session.token, queue=QUEUE)
    assert first is not None
    expire_lease(db_session, first["id"])
    sweep_worker_runs()
    posted_run(db_session, subscription, [{"source": "slack", "external_id": "C1:1.0"}])

    again = await claim(worker_session.token, queue=QUEUE)

    assert again is None
    assert run_status(db_session, first["id"]) == "failed"
    assert [
        d.status for d in db_session.query(Delivery).filter_by(run_id=first["id"])
    ] == ["skipped:posted"]


def test_a_coalesced_delivery_keeps_the_place_and_deferral_of_the_one_it_replaced(
    db_session, regular_user
):
    subscription = subscribe(db_session, regular_user, coalesce_mode="latest", on_edit=True)
    item = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    age(db_session, subscription, timedelta(minutes=30))
    deferred_to = datetime.now(timezone.utc) + timedelta(minutes=10)
    first = db_session.query(Delivery).one()
    first.not_before = deferred_to
    db_session.commit()
    created = first.created_at

    edit(db_session, item, "newer")

    db_session.expire_all()
    replacement = db_session.query(Delivery).filter_by(status="pending").one()
    assert (replacement.created_at, replacement.not_before) == (created, deferred_to)


@pytest.mark.asyncio
async def test_a_thread_holds_its_later_message_until_the_earlier_one_is_answered(
    db_session, admin_user, admin_session
):
    subscribe(db_session, admin_user, order_by="thread")
    first, second = thread_messages(db_session, 2)
    route_item(db_session, first.id)
    route_item(db_session, second.id)

    claimed = await claim(admin_session.token, queue=QUEUE)
    held = await claim(admin_session.token, queue=QUEUE)
    assert claimed is not None
    await submit(admin_session.token, claimed)
    released = await claim(admin_session.token, queue=QUEUE)

    assert claimed["ref"]["item_ids"] == [first.id]
    assert held is None
    assert released is not None
    assert released["ref"]["item_ids"] == [second.id]


@pytest.mark.asyncio
async def test_coalescing_a_thread_folds_its_earlier_messages_into_the_latest(
    db_session, admin_user, admin_session
):
    subscribe(db_session, admin_user, order_by="thread", coalesce_mode="latest")
    first, second = thread_messages(db_session, 2)
    route_item(db_session, first.id)
    route_item(db_session, second.id)

    claimed = await claim(admin_session.token, queue=QUEUE)

    assert claimed is not None
    assert claimed["ref"]["item_ids"] == [second.id]
    assert claimed["ref"]["coalesced"] == {str(second.id): [first.id]}


@pytest.mark.asyncio
@pytest.mark.parametrize("ended", ["failed", "expired", "skipped:shed:rate", "skipped:inaccessible"])
async def test_an_earlier_delivery_that_ended_without_a_run_releases_the_next(
    db_session, regular_user, worker_session, ended
):
    subscription = subscribe(db_session, regular_user, order_by="item", on_edit=True)
    item = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    db_session.query(Delivery).filter_by(subscription_id=subscription.id).one().status = ended
    db_session.commit()
    edit(db_session, item, "second version")

    claimed = await claim(worker_session.token, queue=QUEUE)

    assert claimed is not None
    assert "second version" in claimed["input"]


@pytest.mark.asyncio
async def test_a_coalescing_batch_claims_an_item_once_at_its_latest(
    db_session, regular_user, worker_session
):
    subscription = subscribe(
        db_session, regular_user, delivery=BATCH, coalesce_mode="latest", on_edit=True
    )
    item = post(db_session, creator_id=regular_user.id)
    other = post(db_session, creator_id=regular_user.id)
    route_item(db_session, item.id)
    route_item(db_session, other.id)
    edit(db_session, item, "latest version")
    age(db_session, subscription, timedelta(minutes=2))

    claimed = await claim(worker_session.token, queue=QUEUE)

    assert claimed is not None
    assert claimed["ref"]["item_ids"] == [item.id, other.id]
    assert "latest version" in claimed["input"]
    assert statuses(db_session, subscription) == ["done", "done", "skipped:coalesced"]


@pytest.mark.asyncio
async def test_work_queues_shows_each_queues_backlog(db_session, regular_user, worker_session):
    subscription = subscribe(db_session, regular_user)
    subscribe(db_session, regular_user, queue="idle", enabled=False)
    for _ in range(2):
        route_item(db_session, post(db_session, creator_id=regular_user.id).id)
    age(db_session, subscription, timedelta(minutes=5))

    with mcp_auth_context(worker_session.token):
        seen = (await agent.work_queues.fn())["queues"]

    assert [(q["queue"], q["pending"]) for q in seen] == [("idle", 0), (QUEUE, 2)]
    assert seen[0]["oldest_age_sec"] is None
    assert 300 <= seen[1]["oldest_age_sec"] < 400


def test_queue_health_counts_every_owners_queues_by_id(db_session, regular_user, admin_user):
    reviews = subscribe(db_session, regular_user)
    also_reviews = subscribe(db_session, regular_user, name="second")
    theirs = subscribe(db_session, admin_user, enabled=False)
    for _ in range(2):
        route_item(db_session, post(db_session, creator_id=regular_user.id).id)
    age(db_session, reviews, timedelta(minutes=5))

    health = queues.queue_health(db_session)

    # Queue names are the user's own words; operators get ids.
    assert [(q["owner_id"], q["subscription_ids"], q["pending"]) for q in health] == [
        (regular_user.id, [reviews.id, also_reviews.id], 4),
        (admin_user.id, [theirs.id], 0),
    ]
    assert [set(q) for q in health] == [{"owner_id", "subscription_ids", "pending", "oldest_age_sec"}] * 2
    assert 300 <= health[0]["oldest_age_sec"] < 400
    assert health[1]["oldest_age_sec"] is None


def removal(db, subscription, item_id: int, earlier: str | None):
    """A removal delivery of `item_id`, after an `earlier` delivery in that state."""
    earlier_rows = [] if earlier is None else [(b"v1", earlier, [])]
    for version, status, changes in [*earlier_rows, (subscriptions.REMOVED_VERSION, "pending", ["removed"])]:
        db.add(Delivery(subscription_id=subscription.id, item_id=item_id, content_hash=version, status=status,
                                 changes=changes))
    db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gone, earlier, delivered",
    [
        (True, "done", True),
        # Only a delivered item's removal is news, and only a gone item's.
        (True, None, False),
        (True, "skipped:inaccessible", False),
        (False, "done", False),
    ],
)
async def test_a_removal_is_delivered_only_for_a_gone_item_it_was_delivered(
    db_session, regular_user, admin_user, worker_session, gone, earlier, delivered
):
    subscription = subscribe(db_session, regular_user, on_remove=True)
    items = [post(db_session, creator_id=admin_user.id), post(db_session, creator_id=admin_user.id)]
    item_id = items[0].id
    db_session.delete(items[int(not gone)])
    db_session.commit()
    removal(db_session, subscription, item_id, earlier)

    claimed = await claim(worker_session.token, queue=QUEUE)

    assert (claimed is not None) is delivered


@pytest.mark.asyncio
async def test_coalescing_a_thread_never_folds_a_removal(db_session, admin_user, admin_session):
    subscription = subscribe(db_session, admin_user, order_by="thread", coalesce_mode="latest", on_remove=True)
    first, second = thread_messages(db_session, 2)
    first_id = first.id
    route_item(db_session, first_id)
    first_claim = await claim(admin_session.token, queue=QUEUE)
    assert first_claim is not None
    db_session.delete(first)
    db_session.commit()
    subscriptions.route_events()

    route_item(db_session, second.id)

    db_session.expire_all()
    rows = db_session.query(Delivery).filter_by(subscription_id=subscription.id)
    assert sorted((m.changes, m.status) for m in rows) == [(["new"], "done"), (["new"], "pending"), (["removed"], "pending")]
