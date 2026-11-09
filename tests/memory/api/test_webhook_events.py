"""The webhook log: GET /webhook-events lists the source accounts it covers, and
GET /webhook-events/{source}/{account_id} one account's deliveries."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from memory.common.db.models import (
    AgentRun,
    APIKey,
    Subscription,
    Delivery,
    BlogPost,
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordServer,
    Event,
    GithubAccount,
    GithubRepo,
    HumanUser,
    SlackApp,
    SlackChannel,
    SlackWorkspace,
)
from memory.agents import raw_events
from memory.api.webhook_events import RAW_RUN_EVENT
from memory.common.db.models.events import record_raw_event
from tests.conftest import unique_sha256


def raw(
    db, owner_id, delivery_id, *, source="github", account_id=5, payload=None, outcome="no_item_type",
    event_type="pull_request.opened", container="555", links_items=False,
):
    return record_raw_event(
        db,
        source=source,
        event_type=event_type,
        account_id=account_id,
        owner_id=owner_id,
        delivery_id=delivery_id,
        outcome=outcome,
        container=container,
        payload={"secret": "untrusted"} if payload is None else payload,
        links_items=links_items,
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


@pytest.mark.parametrize(
    "params, shown",
    [
        ({"outcome": "stored"}, ["d2"]),
        ({"type": "issues"}, ["d3"]),
        # A prefix; an untracked channel's delivery never matches, as its type is hidden.
        ({"type": "pull_request"}, ["d2", "d1"]),
        ({"type": "pull_request", "outcome": "no_item_type"}, ["d1"]),
        ({"type": "pull_%"}, []),
    ],
)
def test_deliveries_filter_by_outcome_and_type_prefix(member_client, db_session, user, params, shown):
    ids = {
        raw(db_session, user.id, "d1", event_type="pull_request.opened"): "d1",
        raw(db_session, user.id, "d2", event_type="pull_request.closed", outcome="stored"): "d2",
        raw(db_session, user.id, "d3", event_type="issues.opened"): "d3",
        raw(db_session, user.id, "d4", event_type="pull_request.opened", outcome="not_collected"): "d4",
    }
    db_session.commit()

    listed = member_client.get("/webhook-events/github/5", params=params).json()

    assert [ids[e["id"]] for e in listed] == shown


def test_a_github_delivery_names_its_repo_and_why_its_sync_fails(member_client, db_session, user):
    account = GithubAccount(user_id=user.id, name="acct", auth_type="pat", access_token="ghp_x", active=True)
    db_session.add(account)
    db_session.flush()
    db_session.add(GithubRepo(account_id=account.id, github_id=555, owner="acme", name="api", sync_error="No access"))
    raw(db_session, user.id, "d1", account_id=account.id)
    raw(db_session, user.id, "d2", account_id=account.id, container="556")
    db_session.commit()

    listed = member_client.get(f"/webhook-events/github/{account.id}").json()

    assert [(e["container"], e["container_name"], e["container_error"]) for e in listed] == [
        ("556", None, None),
        ("555", "acme/api", "No access"),
    ]


def test_a_discord_delivery_names_only_channels_the_viewer_can_see(member_client, db_session, user):
    mine = DiscordBot(application_id=1, name="Mine", created_by_user_id=user.id)
    theirs = DiscordBot(application_id=2, name="Theirs", created_by_user_id=other_user(db_session).id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    db_session.add_all([
        DiscordChannel(id=901, name="my-dm", channel_type="dm", bot_id=mine.id),
        DiscordChannel(id=902, name="their-dm", channel_type="dm", bot_id=theirs.id),
    ])
    raw(db_session, user.id, "d1", source="discord", account_id=mine.id, container="901")
    raw(db_session, user.id, "d2", source="discord", account_id=mine.id, container="902")
    db_session.commit()

    listed = member_client.get(f"/webhook-events/discord/{mine.id}").json()

    assert [(e["container"], e["container_name"]) for e in listed] == [("902", None), ("901", "my-dm")]


def test_a_payload_from_a_channel_the_viewer_cant_read_is_withheld(client, member_client, db_session, user):
    db_session.add(SlackWorkspace(id="T1", name="w"))
    db_session.add(SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel"))
    raw(db_session, user.id, "d1", source="slack", container="C1", payload={"pinned": "secret"})
    db_session.commit()

    [as_member] = member_client.get("/webhook-events/slack/5", params={"payload": True}).json()
    user.scopes = ["*"]
    db_session.flush()
    [as_admin] = client.get("/webhook-events/slack/5", params={"payload": True}).json()

    assert (as_member["payload"], as_member["payload_withheld"]) == (None, True)
    assert (as_admin["payload"], as_admin["payload_withheld"]) == ({"pinned": "secret"}, False)


def test_the_owner_sees_payloads_from_their_own_project_less_repo(member_client, db_session, user):
    account = GithubAccount(user_id=user.id, name="acct", auth_type="pat", access_token="ghp_x", active=True)
    db_session.add(account)
    db_session.flush()
    db_session.add(GithubRepo(account_id=account.id, github_id=555, owner="acme", name="api"))
    raw(db_session, user.id, "d1", account_id=account.id, payload={"n": 1})
    db_session.commit()

    [shown] = member_client.get(f"/webhook-events/github/{account.id}", params={"payload": True}).json()

    assert (shown["payload"], shown["payload_withheld"]) == ({"n": 1}, False)


@pytest.mark.parametrize("first_bot, shown", [("mine", True), ("theirs", False)])
def test_a_discord_dm_payload_shows_only_to_the_owner_of_the_bot_it_belongs_to(
    member_client, db_session, user, first_bot, shown
):
    bots = {
        "mine": DiscordBot(application_id=1, name="Mine", created_by_user_id=user.id),
        "theirs": DiscordBot(application_id=2, name="Theirs", created_by_user_id=other_user(db_session).id),
    }
    db_session.add_all(bots.values())
    db_session.flush()
    db_session.add(DiscordChannel(id=901, name="dm", channel_type="dm", bot_id=bots[first_bot].id))
    raw(db_session, user.id, "d1", source="discord", account_id=bots["mine"].id, container="901", payload={"n": 1})
    db_session.commit()

    [event] = member_client.get(f"/webhook-events/discord/{bots['mine'].id}", params={"payload": True}).json()

    assert event["payload_withheld"] is not shown


@pytest.mark.parametrize(
    "channel, known",
    [
        (DiscordChannel(id=901, name="dm", channel_type="dm", bot_id=1), True),
        (DiscordChannel(id=901, name="dm", channel_type="dm", bot_id=2), False),
        (DiscordChannel(id=901, name="c", channel_type="text", server_id=11), True),
        (DiscordChannel(id=901, name="c", channel_type="text", server_id=12), True),
        (DiscordChannel(id=901, name="c", channel_type="text", server_id=22), False),
    ],
    ids=["own dm", "their dm", "own server", "server it is listed in", "their server"],
)
def test_a_discord_delivery_knows_only_channels_its_bots_owner_may_push_into(db_session, user, channel, known):
    """What a pushed delivery's channel is, or whether it exists, is the bot owner's to learn only there."""
    db_session.add_all([
        DiscordBot(id=1, application_id=1, name="Mine", created_by_user_id=user.id),
        DiscordBot(id=2, application_id=2, name="Theirs", created_by_user_id=other_user(db_session).id),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordServer(id=11, name="mine", bot_id=1, verified=True),
        DiscordServer(id=12, name="listed", bot_id=2, verified=True),
        DiscordServer(id=22, name="theirs", bot_id=2, verified=True),
    ])
    db_session.flush()
    db_session.add_all([DiscordBotServer(bot_id=1, server_id=12), channel])
    raw(db_session, user.id, "d1", source="discord", account_id=1, container="901", outcome="stored")
    db_session.commit()

    [event] = db_session.query(Event).filter_by(kind="raw").all()

    assert (raw_events.container_of(db_session, event) is not None) is known


def test_a_container_of_non_ascii_digits_has_no_name(client, db_session, user):
    raw(db_session, user.id, "d1", source="discord", container="²")
    db_session.commit()

    response = client.get("/webhook-events/discord/5", params={"payload": True})

    assert [e["container_name"] for e in response.json()] == [None]


def test_payload_access_looks_up_the_viewers_roles_once_a_page(member_client, db_session, user, monkeypatch):
    db_session.add(SlackWorkspace(id="T1", name="w"))
    db_session.add(SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel"))
    for n in range(3):
        raw(db_session, user.id, f"d{n}", source="slack", container="C1")
    db_session.commit()
    looked_up = []
    real = raw_events.get_user_project_roles
    monkeypatch.setattr(raw_events, "get_user_project_roles", lambda *a: looked_up.append(1) or real(*a))

    member_client.get("/webhook-events/slack/5", params={"payload": True})

    assert len(looked_up) == 1


def read_key(db, user):
    key = APIKey.create(user_id=user.id, name="read", scopes=["read"])
    db.add(key)
    db.commit()
    return {"Authorization": f"Bearer {key.key}"}


def test_an_admins_read_only_key_sees_only_their_own_accounts_and_deliveries(client, db_session, user):
    other = other_user(db_session)
    db_session.add_all([
        SlackApp(client_id="mine.app", name="Mine", created_by_user_id=user.id),
        SlackApp(client_id="their.app", name="Theirs", created_by_user_id=other.id),
    ])
    raw(db_session, other.id, "d1")
    headers = read_key(db_session, user)

    accounts = client.get("/webhook-events", headers=headers).json()
    deliveries = client.get("/webhook-events/github/5", headers=headers).json()

    assert ([a["name"] for a in accounts], deliveries) == (["Mine"], [])


def test_an_admins_read_only_key_names_only_channels_its_bots_see(client, db_session, user):
    mine = DiscordBot(application_id=1, name="Mine", created_by_user_id=user.id)
    theirs = DiscordBot(application_id=2, name="Theirs", created_by_user_id=other_user(db_session).id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    db_session.add(DiscordChannel(id=902, name="their-dm", channel_type="dm", bot_id=theirs.id))
    raw(db_session, user.id, "d1", source="discord", account_id=mine.id, container="902")
    headers = read_key(db_session, user)

    [shown] = client.get(f"/webhook-events/discord/{mine.id}", headers=headers).json()

    assert shown["container_name"] is None


@pytest.mark.parametrize("links_items", [True, False])
def test_a_delivery_says_whether_its_ingest_links_items(member_client, db_session, user, links_items):
    raw(db_session, user.id, "d1", outcome="stored", links_items=links_items)
    db_session.commit()

    [shown] = member_client.get("/webhook-events/github/5").json()

    assert shown["links_items"] is links_items
