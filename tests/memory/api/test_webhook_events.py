"""The webhook log: GET /webhook-events lists the source accounts it covers, and
GET /webhook-events/{source}/{account_id} one account's deliveries."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from memory.common.db.models import (
    AgentRun,
    Subscription,
    Delivery,
    BlogPost,
    Event,
    GithubAccount,
    HumanUser,
    SlackApp,
)
from memory.api.webhook_events import RAW_RUN_EVENT
from memory.common.db.models.events import record_raw_event
from tests.conftest import unique_sha256


def raw(db, owner_id, delivery_id, *, source="github", account_id=5, payload=None, outcome="no_item_type"):
    return record_raw_event(
        db,
        source=source,
        event_type="pull_request.opened",
        account_id=account_id,
        owner_id=owner_id,
        delivery_id=delivery_id,
        outcome=outcome,
        container="555",
        payload={"secret": "untrusted"} if payload is None else payload,
    )


def other_user(db):
    other = HumanUser(name="Other", email="other@example.com", password_hash="x")
    db.add(other)
    db.flush()
    return other


def new_subscription(db, user_id, name):
    subscription = Subscription(
        user_id=user_id, name=name, handler={"queue": "q"}, kind="ingest", config={"filter": {"tags_any": ["ai"]}}
    )
    db.add(subscription)
    db.flush()
    return subscription


def stored_item(db, raw_id, creator_id=None):
    item = BlogPost(sha256=unique_sha256("p"), modality="blog", content="x", tags=["ai"], creator_id=creator_id)
    db.add(item)
    db.flush()
    event = Event(kind="item", action="added", item_id=item.id, caused_by=raw_id)
    db.add(event)
    db.flush()
    return item, event


def test_the_owner_sees_their_accounts_deliveries_newest_first_with_their_items(member_client, db_session, user):
    other = other_user(db_session)
    first, second = raw(db_session, user.id, "d1"), raw(db_session, user.id, "d2")
    raw(db_session, user.id, "d3", account_id=6)
    raw(db_session, user.id, "d4", source="slack")
    raw(db_session, other.id, "d5")
    (a, _), (b, _) = stored_item(db_session, first, user.id), stored_item(db_session, first, user.id)
    db_session.commit()

    response = member_client.get("/webhook-events/github/5")

    assert response.status_code == 200
    assert [(e["id"], e["type"], e["outcome"], e["container"], e["item_ids"], e["payload"]) for e in response.json()] == [
        (second, "pull_request.opened", "no_item_type", "555", [], None),
        (first, "pull_request.opened", "no_item_type", "555", [a.id, b.id], None),
    ]


def test_items_the_viewer_cannot_read_are_left_out(member_client, db_session, user):
    raw_id = raw(db_session, user.id, "d1", outcome="stored")
    mine, _ = stored_item(db_session, raw_id, user.id)
    # No project and someone else's: only an admin reads it.
    hidden, hidden_event = stored_item(db_session, raw_id)
    subscription = new_subscription(db_session, user.id, "mine")
    db_session.add(Delivery(subscription_id=subscription.id, item_id=hidden.id, content_hash=b"v", event_id=hidden_event.id))
    db_session.commit()

    [shown] = member_client.get("/webhook-events/github/5").json()

    assert (shown["item_ids"], shown["triggered"]) == ([mine.id], [])


@pytest.mark.parametrize("payload", [{"n": 1}, ["a", "list"], "text"])
def test_payloads_and_older_pages_are_on_request(client, db_session, user, payload):
    first = raw(db_session, user.id, "d1", payload=payload)
    second = raw(db_session, user.id, "d2")
    db_session.commit()

    response = client.get("/webhook-events/github/5", params={"payload": True, "before": second})

    assert [(e["id"], e["payload"]) for e in response.json()] == [(first, payload)]


def test_another_users_account_shows_nothing_but_to_an_admin(client, member_client, db_session, user):
    raw_id = raw(db_session, other_user(db_session).id, "d1")
    db_session.commit()

    as_member = member_client.get("/webhook-events/github/5").json()
    user.scopes = ["*"]
    db_session.flush()
    as_admin = client.get("/webhook-events/github/5").json()

    assert (as_member, [e["id"] for e in as_admin]) == ([], [raw_id])


def test_a_delivery_from_an_untracked_channel_shows_only_when_it_arrived(member_client, db_session, user):
    raw(db_session, user.id, "d1", outcome="not_collected")
    db_session.commit()

    [event] = member_client.get("/webhook-events/github/5", params={"payload": True}).json()

    assert (event["outcome"], event["type"], event["container"], event["item_ids"], event["triggered"]) == (
        "not_collected", None, None, [], [],
    )


def test_a_delivery_shows_what_the_viewers_subscriptions_made_of_it(member_client, db_session, user):
    raw_id = raw(db_session, user.id, "d1", outcome="stored")
    item, event = stored_item(db_session, raw_id, user.id)
    mine, theirs = new_subscription(db_session, user.id, "mine"), new_subscription(db_session, other_user(db_session).id, "theirs")
    db_session.add_all(
        Delivery(subscription_id=t.id, item_id=item.id, content_hash=b"v", status=status, event_id=event.id)
        for t, status in [(mine, "pending"), (theirs, "done")]
    )
    db_session.commit()

    [shown] = member_client.get("/webhook-events/github/5").json()

    assert (shown["item_ids"], shown["triggered"]) == (
        [item.id],
        [{"subscription_id": mine.id, "subscription": "mine", "item_id": item.id, "status": "pending", "run_id": None}],
    )


def test_the_accounts_listed_are_the_viewers_own_with_their_latest_delivery(member_client, db_session, user):
    other = other_user(db_session)
    github = GithubAccount(user_id=user.id, name="acct", auth_type="pat", access_token="ghp_x", active=True)
    slack = SlackApp(client_id="mine.app", name="Mine", created_by_user_id=user.id)
    db_session.add_all(
        [github, slack, GithubAccount(user_id=other.id, name="theirs", auth_type="pat", access_token="ghp_y", active=True)]
    )
    db_session.flush()
    raw(db_session, user.id, "d1", account_id=github.id)
    latest = raw(db_session, user.id, "d2", account_id=github.id)
    db_session.commit()

    listed = member_client.get("/webhook-events").json()

    assert [(a["source"], a["account_id"], a["name"], a["last_event_id"]) for a in listed] == [
        ("github", github.id, "acct", latest),
        ("slack", slack.id, "Mine", None),
    ]


def test_an_unknown_source_is_rejected(client):
    assert client.get("/webhook-events/email/5").status_code == 422


def test_a_delivery_shows_the_viewers_raw_subscriptions_runs_of_it(member_client, db_session, user):
    raw_id = raw(db_session, user.id, "d1")
    mine, theirs = (
        Subscription(user_id=u, name=n, handler={"queue": "q"}, kind="raw", config={"raw": {"source": "github"}})
        for u, n in [(user.id, "mine"), (other_user(db_session).id, "theirs")]
    )
    db_session.add_all([mine, theirs])
    db_session.flush()
    runs = [
        AgentRun(
            user_id=t.user_id,
            queue="q",
            spec={},
            input="",
            max_cost_usd=0,
            trigger_kind="raw",
            trigger_ref={"subscription_id": t.id, "event_id": raw_id},
            subscription_id=t.id,
            executor="workers",
        )
        for t in (mine, theirs)
    ]
    db_session.add_all(runs)
    db_session.commit()

    [shown] = member_client.get("/webhook-events/github/5").json()

    # A run nobody claimed yet isn't linked.
    assert shown["triggered"] == [
        {"subscription_id": mine.id, "subscription": "mine", "item_id": None, "status": "pending", "run_id": None}
    ]



@pytest.mark.parametrize("started, linked", [(True, True), (False, False)])
def test_a_filter_delivery_links_its_run_only_once_it_started(member_client, db_session, user, started, linked):
    raw_id = raw(db_session, user.id, "d1", outcome="stored")
    item, event = stored_item(db_session, raw_id, user.id)
    subscription = new_subscription(db_session, user.id, "mine")
    run = AgentRun(
        user_id=user.id, spec={}, input="", max_cost_usd=0, trigger_kind="ingest", subscription_id=subscription.id,
        executor="workers", started_at=datetime.now(timezone.utc) if started else None,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(Delivery(subscription_id=subscription.id, item_id=item.id, content_hash=b"v", status="done",
                                     event_id=event.id, run_id=run.id))
    db_session.commit()

    [shown] = member_client.get("/webhook-events/github/5").json()

    assert [t["run_id"] for t in shown["triggered"]] == [run.id if linked else None]


def test_the_raw_run_lookup_uses_its_index(db_session):
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    # The lookup's own filter, apart from the join a busier database may plan around.
    lookup = select(AgentRun.id).where(AgentRun.trigger_kind == "raw", RAW_RUN_EVENT.in_([1, 2]))
    plan = db_session.execute(
        text("EXPLAIN " + str(lookup.compile(dialect=db_session.bind.dialect, compile_kwargs={"literal_binds": True})))
    ).scalars().all()

    assert any("agent_runs_raw_event_idx" in line for line in plan)


def test_an_admin_sees_every_account_marked_by_whose_it_is(client, db_session, user):
    mine = SlackApp(client_id="mine.app", name="Mine", created_by_user_id=user.id)
    theirs = SlackApp(client_id="their.app", name="Theirs", created_by_user_id=other_user(db_session).id)
    db_session.add_all([mine, theirs])
    db_session.commit()

    listed = [(a["account_id"], a["own"]) for a in client.get("/webhook-events").json()]

    assert listed == [(mine.id, True), (theirs.id, False)]
