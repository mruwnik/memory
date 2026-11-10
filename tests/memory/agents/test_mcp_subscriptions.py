# pyright: reportFunctionMemberAccess=false
from datetime import datetime, timedelta, timezone

import pytest

from memory.agents import subscriptions
from memory.api.MCP.servers import agent, subscription
from memory.common import settings
from memory.common.celery_app import FIRE_SUBSCRIPTION
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    Subscription,
    Delivery,
    APIKey,
    BlogPost,
    Event,
    MailMessage,
)
from tests.conftest import mcp_auth_context, route_item, unique_sha256

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
FILTER = {"filter": {"tags_any": ["ai"]}}
CRON = {"cron": "0 9 * * *"}
NOTIFY = {"notify": {"channel": "email", "target": "a@example.com", "message": "hi", "subject": "Daily ping"}}
ALL_SCOPES = ["agents", "agents:work", "schedule", "schedule:write", "read"]


@pytest.fixture(autouse=True)
def may_subscribe(db_session, regular_user):
    regular_user.scopes = ALL_SCOPES
    db_session.commit()


def definition(db, user, spec=SPEC, name="d"):
    d = AgentDefinition(user_id=user.id, name=name, spec=spec)
    db.add(d)
    db.commit()
    return d


def subscribed(d, name="t", **fields):
    return Subscription(
        user_id=d.user_id,
        name=name,
        definition_id=d.id,
        handler={"agent": d.id},
        kind="ingest",
        config=FILTER,
        **fields,
    )


def api_key(db, user, scopes):
    key = APIKey.create(user_id=user.id, name="k", scopes=scopes)
    db.add(key)
    db.commit()
    return key


def post(db, user):
    item = BlogPost(
        sha256=unique_sha256("p"), modality="blog", content="x", tags=["ai"], creator_id=user.id
    )
    db.add(item)
    db.commit()
    return item


upsert, list_all, fetch, delete, dry_run = (
    subscription.upsert.fn,
    subscription.list_all.fn,
    subscription.fetch.fn,
    subscription.delete.fn,
    subscription.test.fn,
)


# --- Handlers and sources --------------------------------------------------


@pytest.mark.asyncio
async def test_a_filter_subscription_on_a_definition_is_created_updated_and_deleted(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": d.id}, source=FILTER)
        updated = await upsert(
            subscription_id=created["id"],
            source={"delivery": {"mode": "batch", "window_sec": 600}, "on_edit": True},
            enabled=False,
        )
        listed = await list_all(definition_id=d.id)
        await delete(subscription_id=created["id"])

    assert (created["kind"], created["enabled"], created["handler"], created["definition_id"]) == (
        "ingest", True, {"agent": d.id}, d.id,
    )
    assert (created["config"]["template"], created["order_by"]) == (subscriptions.DEFAULT_TEMPLATE, "none")
    assert (updated["enabled"], updated["config"]["delivery"]["window_sec"], updated["config"]["on_edit"]) == (
        False, 600, True,
    )
    assert updated["config"]["filter"]["tags_any"] == ["ai"]
    assert [s["id"] for s in listed["subscriptions"]] == [created["id"]]
    assert db_session.query(Subscription).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["echo", "system:echo"])
async def test_an_agent_handler_may_be_a_code_slug(db_session, regular_user, user_session, code_agents, slug):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": slug}, source=FILTER)

    assert (created["handler"], created["definition_id"]) == ({"agent": slug}, None)


@pytest.mark.asyncio
async def test_a_queue_subscription_is_ordered_by_item_unless_told_otherwise(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.token):
        default = await upsert(name="a", handler={"queue": "reviews"}, source=FILTER)
        threaded = await upsert(name="b", handler={"queue": "chat"}, source=FILTER, order_by="thread", coalesce="latest")

    assert (default["handler"], default["order_by"], default["coalesce_mode"]) == ({"queue": "reviews"}, "item", "none")
    assert (threaded["order_by"], threaded["coalesce_mode"]) == ("thread", "latest")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first, then",
    [({"queue": "reviews"}, {"agent": "echo"}), ({"agent": "echo"}, {"queue": "reviews"})],
)
async def test_a_filter_subscriptions_handler_keeps_its_kind(
    db_session, regular_user, user_session, code_agents, first, then
):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler=first, source=FILTER)
        kept = await upsert(subscription_id=created["id"], handler=first)
        with pytest.raises(ValueError, match="handler kind can't change"):
            await upsert(subscription_id=created["id"], handler=then)

    assert kept["handler"] == first


@pytest.mark.asyncio
async def test_a_cron_moved_from_an_agent_to_a_message_drops_its_input(
    db_session, regular_user, user_session, code_agents
):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": "echo"}, source=CRON | {"input": "digest"})
        moved = await upsert(subscription_id=created["id"], handler=NOTIFY)

    assert (moved["handler"], moved["config"]) == (NOTIFY, CRON | {"input": ""})


@pytest.mark.asyncio
async def test_a_partial_filter_is_merged_into_the_stored_one(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"queue": "q"}, source=FILTER)
        updated = await upsert(subscription_id=created["id"], source={"filter": {"repos": ["a/b"]}})

    assert (updated["config"]["filter"]["tags_any"], updated["config"]["filter"]["repos"]) == (["ai"], ["a/b"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, match",
    [
        (
            {"handler": NOTIFY, "source": {"at": "2999-01-01T09:00:00Z"}, "enabled": True, "resume": True},
            "resume applies to filter sources",
        ),
        ({"name": "existing", "enabled": False, "resume": True}, "enabled=true"),
    ],
)
async def test_resume_is_refused_where_it_cannot_apply(db_session, regular_user, user_session, kwargs, match):
    with mcp_auth_context(user_session.token):
        await upsert(name="existing", handler={"queue": "q"}, source=FILTER)
        with pytest.raises(ValueError, match=match):
            await upsert(**kwargs)

    assert db_session.query(Event).filter_by(kind="timer").count() == 0
    assert db_session.query(Subscription).one().enabled is True


@pytest.mark.asyncio
async def test_a_recurring_message_is_a_cron_subscription_in_the_owners_timezone(
    db_session, regular_user, user_session
):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await upsert(handler=NOTIFY, source=CRON)

    assert (result["kind"], result["config"], result["enabled"], result["api_key_id"]) == (
        "cron", {"cron": "0 9 * * *", "input": ""}, True, None,
    )
    assert result["handler"] == NOTIFY
    assert result["name"] == f"subscription-{result['id']}"
    assert datetime.fromisoformat(result["next_at"]).astimezone(timezone.utc).strftime("%H:%M") == "03:30"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "at, fires",
    [
        ("2999-01-01T09:00:00Z", datetime(2999, 1, 1, 9, tzinfo=timezone.utc)),
        ("2999-01-01T09:00:00+01:00", datetime(2999, 1, 1, 8, tzinfo=timezone.utc)),
        ("2999-01-01T09:00:00", datetime(2999, 1, 1, 3, 30, tzinfo=timezone.utc)),
    ],
)
async def test_a_one_off_is_a_timer(db_session, regular_user, user_session, at, fires):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await upsert(handler=NOTIFY, source={"at": at})

    timer = db_session.get_one(Event, result["id"])
    assert (result["kind"], timer.kind, timer.fire_at, timer.handler["notify"]["message"]) == (
        "timer", "timer", fires, "hi",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["echo", None])
async def test_an_agent_runs_on_a_cron_with_its_input(
    db_session, regular_user, user_session, code_agents, slug
):
    d = definition(db_session, regular_user)
    agent_ref = slug or d.id

    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": agent_ref}, source=CRON | {"input": "digest"})
        updated = await upsert(subscription_id=created["id"], source={"cron": "0 9 * * 1"})

    assert (created["handler"], created["config"]) == ({"agent": agent_ref}, CRON | {"input": "digest"})
    assert updated["config"] == {"cron": "0 9 * * 1", "input": "digest"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"handler": NOTIFY}, "source is required"),
        ({"source": CRON}, "handler is required"),
        ({"handler": NOTIFY, "source": CRON | {"at": "2999-01-01T09:00:00Z"}}, "exactly one of"),
        ({"handler": NOTIFY, "source": {"input": "x"}}, "exactly one of"),
        ({"handler": NOTIFY | {"queue": "q"}, "source": CRON}, "exactly one of"),
        ({"handler": {"webhook": "x"}, "source": CRON}, "exactly one of"),
        ({"handler": NOTIFY, "source": {"cron": "*/1 * * * *"}}, "too short"),
        ({"handler": {"notify": {"message": "x"}}, "source": CRON}, "needs channel, target and message"),
        ({"handler": {"notify": NOTIFY["notify"] | {"channel": "sms"}}, "source": CRON}, "must be one of"),
        ({"handler": {"notify": NOTIFY["notify"] | {"target": "not-an-email"}}, "source": CRON}, "No email address"),
        ({"handler": NOTIFY, "source": {"at": "2000-01-01T09:00:00Z"}}, "in the future"),
        ({"handler": NOTIFY, "source": {"at": "2999-01-01T09:00:00Z"}, "enabled": False}, "can't be created paused"),
        ({"handler": NOTIFY, "source": {"at": "2999-01-01T09:00:00Z"}, "name": "x"}, "timers have no name"),
        ({"handler": NOTIFY, "source": FILTER}, "notify handlers take cron and timer sources"),
        ({"handler": {"queue": "q"}, "source": CRON}, "queue handlers take filter and raw sources"),
        ({"handler": {"queue": " "}, "source": FILTER}, "queue name"),
        ({"handler": {"queue": "slack-*"}, "source": FILTER}, "queue name"),
        ({"handler": {"queue": "jobs[1]"}, "source": FILTER}, "queue name"),
        ({"handler": {"queue": "what?"}, "source": FILTER}, "queue name"),
        ({"handler": {"agent": "nope"}, "source": FILTER}, "unknown agent 'nope'"),
        ({"handler": {"agent": 999_999}, "source": FILTER}, "unknown agent definition"),
        ({"handler": {"agent": 999_999}, "source": CRON | {"input": "x"}}, "unknown agent definition"),
        ({"handler": {"agent": "echo"}, "source": CRON}, "input is required"),
        ({"handler": NOTIFY, "source": CRON | {"input": "x"}}, "input only applies to agent handlers"),
        ({"handler": NOTIFY, "source": CRON, "max_per_hour": 3}, "apply to filter sources"),
        ({"handler": NOTIFY, "source": CRON, "order_by": "item"}, "apply to filter sources"),
        ({"handler": {"agent": "echo"}, "source": FILTER, "order_by": "item"}, "order_by applies to queue handlers"),
        ({"handler": {"queue": "q"}, "source": FILTER, "max_per_hour": -1}, "max_per_hour must be at least 0"),
        ({"handler": {"queue": "q"}, "source": FILTER, "backlog_max": -1}, "backlog_max must be at least 0"),
        (
            {"handler": {"queue": "q"}, "source": FILTER, "max_per_hour": 3601},
            r"max_per_hour can be at most 3600 \(AGENT_MAX_DELIVERIES_PER_HOUR\)",
        ),
        ({"handler": {"queue": "q"}, "source": FILTER, "name": " "}, "name must not be empty"),
        ({"handler": {"queue": "q"}, "source": FILTER, "name": "subscription-12"}, "reserved for default names"),
        ({"handler": {"queue": "q"}, "source": {"filter": {}}}, "set at least one of"),
        ({"handler": {"queue": "q"}, "source": {"filter": {"regex": "("}}}, "regex does not compile"),
        ({"handler": {"queue": "q"}, "source": FILTER | {"nope": 1}}, "Extra inputs"),
        (
            {"handler": {"queue": "q"}, "source": {"filter": {"source_types": ["blog_post"]}, "template": "{item.repo}"}},
            r"unknown field \{item.repo\}; valid fields: author,",
        ),
    ],
)
async def test_create_refuses_with_reasons(db_session, regular_user, user_session, code_agents, kwargs, match):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=match):
            await upsert(**kwargs)
    assert db_session.query(Subscription).count() == db_session.query(Event).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing, change",
    [
        (CRON | {"input": "x"}, {"source": FILTER}),
        (CRON | {"input": "x"}, {"source": {"at": "2999-01-01T09:00:00Z"}}),
        (FILTER, {"source": CRON}),
    ],
)
async def test_a_source_keeps_its_kind(db_session, regular_user, user_session, code_agents, existing, change):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": "echo"}, source=existing)
        with pytest.raises(ValueError, match="source kind can't change"):
            await upsert(subscription_id=created["id"], **change)


# --- Names, idempotence and caps ------------------------------------------


@pytest.mark.asyncio
async def test_subscribing_again_by_name_updates_in_place(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        first = await upsert(name="inbox", handler={"queue": "q"}, source=FILTER, max_per_hour=5)
        again = await upsert(name="inbox", handler={"queue": "q"}, source={"filter": {"tags_any": ["ml"]}})

    assert (again["id"], again["max_per_hour"], again["config"]["filter"]["tags_any"]) == (first["id"], 5, ["ml"])
    assert again["active_since"] == first["active_since"]
    assert db_session.query(Subscription).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"name": "renamed"}, {"enabled": False}, {"handler": {"queue": "jobs[1]"}, "max_per_hour": 3}],
)
async def test_a_subscription_on_a_queue_named_before_globs_can_still_be_edited(
    db_session, regular_user, user_session, change
):
    """Only new queue names are refused glob characters: existing ones keep working."""
    subscription = Subscription(
        user_id=regular_user.id,
        name="old",
        handler={"queue": "jobs[1]"},
        kind="ingest",
        config=subscriptions.IngestConfig.model_validate({"filter": {"tags_any": ["ai"]}}).model_dump(mode="json"),
    )
    db_session.add(subscription)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        updated = await upsert(subscription_id=subscription.id, **change)

    assert (updated["id"], updated["handler"]) == (subscription.id, {"queue": "jobs[1]"})


@pytest.mark.asyncio
async def test_a_subscription_cant_move_to_a_queue_named_with_globs(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"queue": "jobs"}, source=FILTER)
        with pytest.raises(ValueError, match="queue name"):
            await upsert(subscription_id=created["id"], handler={"queue": "jobs[1]"})


@pytest.mark.asyncio
async def test_subscribing_again_by_name_leaves_a_tripped_subscription_off(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    t = tripped(d, name="inbox")
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        again = await upsert(name="inbox", handler={"agent": d.id}, source=FILTER)

    assert (again["id"], again["enabled"], again["active_since"]) == (t.id, False, ACTIVE_SINCE.isoformat())


@pytest.mark.asyncio
async def test_a_subscription_is_renamed_by_id(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"queue": "q"}, source=FILTER)
        other = await upsert(name="other", handler={"queue": "q"}, source=FILTER)
        renamed = await upsert(subscription_id=created["id"], name="inbox")
        with pytest.raises(ValueError, match="you already have a subscription named 'inbox'"):
            await upsert(subscription_id=other["id"], name="inbox")

    assert (renamed["id"], renamed["name"]) == (created["id"], "inbox")


@pytest.mark.asyncio
async def test_a_name_taken_concurrently_is_refused_by_the_database(
    db_session, regular_user, user_session, fake_providers, monkeypatch
):
    d = definition(db_session, regular_user)
    db_session.add(subscribed(d, "taken"))
    db_session.commit()
    # As if the other subscription was committed after this call's own check.
    monkeypatch.setattr(subscription, "find", lambda db, user_id, subscription_id, name: None)
    monkeypatch.setattr(subscription, "checked_name", lambda db, user_id, name, subscription_id: name)

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="you already have a subscription named 'taken'"):
            await upsert(handler={"agent": d.id}, source=FILTER, name="taken")


@pytest.mark.asyncio
async def test_enabled_subscriptions_are_capped_per_user(
    db_session, regular_user, user_session, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_MAX_SUBSCRIPTIONS_PER_USER", 1)

    with mcp_auth_context(user_session.token):
        paused = await upsert(name="a", handler=NOTIFY, source=CRON, enabled=False)
        first = await upsert(name="b", handler={"queue": "q"}, source=FILTER)
        edited = await upsert(subscription_id=first["id"], source=FILTER)
        with pytest.raises(ValueError, match="AGENT_MAX_SUBSCRIPTIONS_PER_USER"):
            await upsert(name="c", handler={"queue": "q"}, source=FILTER)
        with pytest.raises(ValueError, match="AGENT_MAX_SUBSCRIPTIONS_PER_USER"):
            await upsert(subscription_id=paused["id"], enabled=True)

    assert (paused["enabled"], edited["enabled"]) == (False, True)


# --- Credentials and scopes -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scopes, kwargs, match",
    [
        (["agents:work"], {"handler": {"queue": "q"}, "source": FILTER}, "needs the 'read' scope"),
        (["agents:work", "read"], {"handler": {"agent": "echo"}, "source": FILTER}, "needs the 'agents' scope"),
        (["agents:work", "read"], {"handler": NOTIFY, "source": CRON}, "needs the 'schedule:write' scope"),
        (["schedule:write"], {"handler": {"queue": "q"}, "source": FILTER}, "needs the 'agents:work' scope"),
        (["schedule:write"], {"handler": {"agent": "echo"}, "source": CRON | {"input": "x"}}, "needs the 'agents' scope"),
        (["agents"], {"handler": {"agent": "echo"}, "source": CRON | {"input": "x"}}, "needs the 'schedule:write' scope"),
        (
            ["agents"],
            {"handler": {"agent": "echo"}, "source": {"at": "2999-01-01T09:00:00Z", "input": "x"}},
            "needs the 'schedule:write' scope",
        ),
    ],
)
async def test_each_handler_needs_its_scope(db_session, regular_user, code_agents, scopes, kwargs, match):
    key = api_key(db_session, regular_user, scopes)

    with mcp_auth_context(key.key, scopes=scopes):
        with pytest.raises(ValueError, match=match):
            await upsert(**kwargs)


@pytest.mark.asyncio
async def test_a_work_key_with_read_may_subscribe_a_queue(db_session, regular_user):
    key = api_key(db_session, regular_user, ["agents:work", "read"])

    with mcp_auth_context(key.key, scopes=["agents:work", "read"]):
        created = await upsert(handler={"queue": "q"}, source=FILTER)

    assert (created["api_key_id"], created["via_api_key"]) == (key.id, True)


@pytest.mark.asyncio
async def test_a_key_cannot_subscribe_tools_it_could_not_run(db_session, regular_user, fake_providers):
    d = definition(db_session, regular_user, SPEC | {"tools": ["core_search"]})
    key = api_key(db_session, regular_user, ["agents"])

    with mcp_auth_context(key.key, scopes=["agents"]):
        with pytest.raises(ValueError, match="tool 'core_search' is not visible to you"):
            await upsert(handler={"agent": d.id}, source=FILTER)


@pytest.mark.asyncio
async def test_a_subscription_keeps_the_key_that_made_it(db_session, regular_user, user_session, fake_providers):
    d = definition(db_session, regular_user)
    key = api_key(db_session, regular_user, ["agents"])

    with mcp_auth_context(key.key, scopes=["agents"]):
        created = await upsert(handler={"agent": d.id}, source=FILTER)
    with mcp_auth_context(user_session.token):
        edited = await upsert(subscription_id=created["id"], source=FILTER)

    assert (created["api_key_id"], created["via_api_key"]) == (key.id, True)
    assert (edited["api_key_id"], edited["via_api_key"]) == (key.id, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"source": {"template": "x"}},
        {"enabled": False},
        {"name": "renamed"},
    ],
)
async def test_a_key_may_change_only_what_it_set_up(db_session, regular_user, user_session, change):
    key = api_key(db_session, regular_user, ALL_SCOPES)
    with mcp_auth_context(user_session.token):
        mine = await upsert(name="mine", handler={"queue": "q"}, source=FILTER)

    with mcp_auth_context(key.key, scopes=ALL_SCOPES):
        made = await upsert(name="made", handler={"queue": "q"}, source=FILTER)
        with pytest.raises(ValueError, match="another credential"):
            await upsert(subscription_id=mine["id"], **change)
        with pytest.raises(ValueError, match="another credential"):
            await delete(subscription_id=mine["id"])

    assert (made["api_key_id"], made["via_api_key"]) == (key.id, True)


@pytest.mark.asyncio
async def test_a_key_cannot_cancel_a_timer_it_did_not_set(db_session, regular_user, user_session):
    key = api_key(db_session, regular_user, ALL_SCOPES)
    with mcp_auth_context(user_session.token):
        timer = await upsert(handler=NOTIFY, source={"at": "2999-01-01T09:00:00Z"})

    with mcp_auth_context(key.key, scopes=ALL_SCOPES):
        with pytest.raises(ValueError, match="another credential"):
            await delete(timer_id=timer["id"])


@pytest.mark.asyncio
async def test_a_subscription_whose_key_stopped_working_can_be_paused_by_its_owner(
    db_session, regular_user, user_session
):
    key = api_key(db_session, regular_user, ALL_SCOPES)
    with mcp_auth_context(key.key, scopes=ALL_SCOPES):
        made = await upsert(handler=NOTIFY, source=CRON)
    key.revoked = True
    db_session.commit()

    with mcp_auth_context(user_session.token):
        paused = await upsert(subscription_id=made["id"], enabled=False)

    assert paused["enabled"] is False


@pytest.mark.asyncio
async def test_pausing_works_after_access_is_lost(db_session, regular_user, user_session, fake_providers):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": d.id}, source=FILTER)
    d.spec = SPEC | {"tools": ["meta_get_user"]}
    regular_user.scopes = ["agents:work"]
    db_session.commit()

    with mcp_auth_context(user_session.token):
        paused = await upsert(subscription_id=created["id"], enabled=False)

    assert paused["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda t: upsert(subscription_id=t.id, source=FILTER),
        lambda t: upsert(subscription_id=t.id, enabled=False),
        lambda t: fetch(subscription_id=t.id),
        lambda t: delete(subscription_id=t.id),
    ],
)
async def test_other_users_subscriptions_are_unknown(
    db_session, admin_user, regular_user, user_session, fake_providers, call
):
    t = subscribed(definition(db_session, admin_user))
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=f"unknown subscription {t.id}"):
            await call(t)
        listed = await list_all()

    assert listed == {"subscriptions": [], "timers": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tools,warned",
    [([], []), (["web_fetch"], ["web_fetch"]), (["meta_notify_user"], ["meta_notify_user"])],
)
async def test_write_and_egress_tools_are_flagged(
    db_session, admin_user, admin_session, fake_providers, admin_keys, tools, warned
):
    d = definition(db_session, admin_user, SPEC | {"tools": tools})

    with mcp_auth_context(admin_session.token):
        created = await upsert(handler={"agent": d.id}, source=FILTER)

    assert all(w in " ".join(created["warnings"]) for w in warned)
    assert len(created["warnings"]) == len(warned[:1])


# --- Enabling, tripping and resuming --------------------------------------


TRIPPED_AT = datetime(2026, 11, 1, tzinfo=timezone.utc)
ACTIVE_SINCE = datetime(2026, 10, 15, tzinfo=timezone.utc)


def tripped(d, name="t", **state):
    return subscribed(
        d,
        name,
        enabled=False,
        active_since=ACTIVE_SINCE,
        state={"tripped": {"reason": "key revoked", "at": TRIPPED_AT.isoformat()}} | state,
    )


@pytest.mark.asyncio
async def test_re_enabling_clears_a_tripped_breaker_and_moves_active_since(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    t = tripped(
        d,
        throttled={"reason": "rate", "since": "x", "last": "x"},
        resume={"from": "x", "to": "x", "after": 0},
    )
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        enabled = await upsert(subscription_id=t.id, enabled=True)

    assert (enabled["enabled"], enabled["state"]) == (True, {})
    assert datetime.fromisoformat(enabled["active_since"]) > TRIPPED_AT


@pytest.mark.asyncio
@pytest.mark.parametrize("resume,missed", [(False, []), (True, [True])])
async def test_a_tripped_subscription_can_resume_from_when_it_tripped(
    db_session, regular_user, user_session, fake_providers, no_celery_dispatch, resume, missed
):
    d = definition(db_session, regular_user)
    t = tripped(d)
    db_session.add(t)
    db_session.commit()
    item = post(db_session, regular_user)
    # Routed while the subscription was tripped, so it never saw this item.
    route_item(db_session, item.id)

    with mcp_auth_context(user_session.token):
        enabled = await upsert(subscription_id=t.id, enabled=True, resume=resume)
    routed = subscriptions.route_events()

    # Resuming carries on as if it had never tripped; otherwise it starts from now.
    kept_active_since = datetime.fromisoformat(enabled["active_since"]) == ACTIVE_SINCE
    assert (kept_active_since, [r == t.id for r in routed]) == (resume, missed)
    assert "resume" not in db_session.get_one(Subscription, t.id).state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make, fields, match",
    [
        (lambda d: subscribed(d, enabled=False), {"enabled": True, "resume": True}, "tripped"),
        (tripped, {"source": {"template": "x"}, "resume": True}, "enabled=true"),
    ],
)
async def test_resuming_needs_a_tripped_subscription_being_enabled(
    db_session, regular_user, user_session, fake_providers, make, fields, match
):
    d = definition(db_session, regular_user)
    t = make(d)
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token), pytest.raises(ValueError, match=match):
        await upsert(subscription_id=t.id, **fields)


@pytest.mark.asyncio
async def test_an_upsert_that_does_not_enable_leaves_a_tripped_subscription_alone(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    t = tripped(d)
    db_session.add(t)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        updated = await upsert(subscription_id=t.id, source={"template": "x"})

    assert (updated["enabled"], updated["active_since"], updated["state"]) == (
        False, ACTIVE_SINCE.isoformat(), t.state,
    )


@pytest.mark.asyncio
async def test_pausing_a_cron_clears_its_next_tick_and_enabling_reschedules_it(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler=NOTIFY, source=CRON)
        subscription = db_session.get_one(Subscription, created["id"])
        subscription.state = {"tripped": {"reason": "x", "at": "2026-01-01T00:00:00+00:00"}, "failures": 3}
        db_session.commit()
        paused = await upsert(subscription_id=created["id"], enabled=False)
        resumed = await upsert(subscription_id=created["id"], enabled=True)

    assert (paused["enabled"], paused["next_at"]) == (False, None)
    assert (resumed["enabled"], resumed["next_at"] is not None, resumed["state"]) == (True, True, {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change, moves",
    [
        ({"handler": {"notify": {"message": "renamed"}}}, False),
        ({"name": "renamed"}, False),
        ({"source": {"cron": "0 10 * * *"}}, True),
        ({"source": {"cron": "0 9 * * *"}}, False),
    ],
)
async def test_next_tick_moves_only_with_the_cron(db_session, regular_user, user_session, change, moves):
    far = datetime(2999, 1, 1, 9, tzinfo=timezone.utc)
    with mcp_auth_context(user_session.token):
        created = await upsert(handler=NOTIFY, source=CRON)
        subscription = db_session.get_one(Subscription, created["id"])
        subscription.next_at = far
        db_session.commit()
        updated = await upsert(subscription_id=created["id"], **change)

    assert (datetime.fromisoformat(updated["next_at"]) != far) is moves


@pytest.mark.asyncio
async def test_a_notify_handler_update_merges_and_revalidates_the_target(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler=NOTIFY, source=CRON)
        updated = await upsert(subscription_id=created["id"], handler={"notify": {"message": "renamed"}})
        with pytest.raises(ValueError, match="not a known person, channel, or user id"):
            await upsert(subscription_id=created["id"], handler={"notify": {"channel": "slack"}})

    assert updated["handler"] == {"notify": NOTIFY["notify"] | {"message": "renamed"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", [{"discord_bot_id": 5}, {"from_address": "boss@example.com"}])
async def test_a_notify_handler_takes_only_its_message_fields(db_session, regular_user, user_session, injected):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="takes only channel, target, message and subject"):
            await upsert(handler={"notify": NOTIFY["notify"] | injected}, source=CRON)
        created = await upsert(handler=NOTIFY, source=CRON)
        stored = db_session.get_one(Subscription, created["id"])
        stored.handler = {"notify": stored.handler["notify"] | injected}
        db_session.commit()
        updated = await upsert(subscription_id=created["id"], handler={"notify": {"message": "renamed"}})

    assert updated["handler"] == {"notify": NOTIFY["notify"] | {"message": "renamed"}}


# --- Dry runs --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter, failed",
    [
        ({"tags_any": ["ai"]}, None),
        ({"tags_any": ["other"]}, "tags_any"),
        ({"source_types": ["blog_post"], "text_contains": ["nowhere"]}, "text_contains"),
        ({"source_types": ["mail_message"], "text_contains": ["nowhere"]}, "source_types"),
        ({"tags_any": ["ai"], "regex": "^y"}, "regex"),
        ({"tags_any": ["ai"], "known_people_only": True}, "known_people_only"),
    ],
)
async def test_a_dry_run_names_the_first_predicate_an_item_fails(
    db_session, regular_user, user_session, filter, failed
):
    item = post(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "q"}, source={"filter": filter})
        result = await dry_run(subscription_id=queued["id"], item_id=item.id)

    assert result == {"subscription_id": queued["id"], "item_id": item.id, "matches": failed is None, "failed": failed}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter, failed",
    [
        ({"tags_any": ["ai"]}, None),
        ({"tags_any": ["other"]}, "tags_any"),
        ({"tags_any": ["ai"], "regex": "^y"}, "regex"),
    ],
)
async def test_a_dry_run_tries_an_unsaved_filter(db_session, regular_user, user_session, filter, failed):
    item = post(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        result = await dry_run(item_id=item.id, source={"filter": filter}, handler={"queue": "q"})

    assert result == {"subscription_id": None, "item_id": item.id, "matches": failed is None, "failed": failed}
    assert db_session.query(Subscription).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("saved, failed", [(True, "posted"), (False, None)])
async def test_a_dry_run_counts_only_the_subscriptions_own_posts(db_session, regular_user, user_session, saved, failed):
    mail = MailMessage(
        sha256=unique_sha256("m"), modality="mail", content="x", tags=["ai"], creator_id=regular_user.id,
        message_id="<m1@example.com>", sender="a@example.com", recipients=["b@example.com"], subject="Hi",
    )
    db_session.add(mail)
    db_session.commit()
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "q"}, source=FILTER) if saved else None
    # A run of the saved subscription, or one of no subscription at all, posted it.
    db_session.add(AgentRun(
        user_id=regular_user.id, spec=SPEC, input="x", trigger_kind="ingest", trigger_ref={}, max_cost_usd=1,
        subscription_id=queued and queued["id"], status="done",
        posted=[{"source": "email", "external_id": "<m1@example.com>"}],
    ))
    db_session.commit()

    with mcp_auth_context(user_session.token):
        tried = {"subscription_id": queued["id"]} if queued else {"source": FILTER, "handler": {"queue": "q"}}
        result = await dry_run(item_id=mail.id, **tried)

    assert result["failed"] == failed


@pytest.mark.asyncio
async def test_a_dry_run_tries_changes_to_a_saved_filter_without_saving_them(db_session, regular_user, user_session):
    item = post(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "q"}, source=FILTER)
        changed = await dry_run(subscription_id=queued["id"], item_id=item.id, source={"filter": {"tags_any": ["other"]}})
        saved = await dry_run(subscription_id=queued["id"], item_id=item.id)

    assert (changed["failed"], saved["failed"]) == ("tags_any", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, error",
    [
        ({}, "give subscription_id, or a source and handler"),
        ({"source": FILTER}, "give subscription_id, or a source and handler"),
        ({"source": CRON, "handler": {"queue": "q"}}, "only filter subscriptions take items"),
        ({"source": FILTER, "handler": NOTIFY}, "notify handlers take cron and timer sources"),
        ({"subscription_id": 1, "handler": {"queue": "q"}}, "handler applies to a new subscription"),
        ({"source": {"filter": {"regex": "("}}, "handler": {"queue": "q"}}, "invalid filter source"),
    ],
)
async def test_a_dry_run_refuses_what_cant_take_items(db_session, regular_user, user_session, args, error):
    item = post(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=error):
            await dry_run(item_id=item.id, **args)


@pytest.mark.asyncio
async def test_a_schedule_key_cant_dry_run_filters(db_session, regular_user):
    item = post(db_session, regular_user)
    key = api_key(db_session, regular_user, ["schedule"])
    with mcp_auth_context(key.key, scopes=["schedule"]):
        with pytest.raises(ValueError, match="filter subscriptions need"):
            await dry_run(item_id=item.id, source=FILTER, handler={"queue": "q"})


@pytest.mark.asyncio
async def test_a_dry_run_fails_an_item_the_subscriptions_key_cant_read(
    db_session, regular_user, admin_user, admin_session
):
    item = post(db_session, regular_user)
    key = api_key(db_session, admin_user, ["agents:work", "read"])
    with mcp_auth_context(key.key, scopes=["agents:work", "read"]):
        queued = await upsert(handler={"queue": "q"}, source=FILTER)

    with mcp_auth_context(admin_session.token):
        result = await dry_run(subscription_id=queued["id"], item_id=item.id)

    assert (result["matches"], result["failed"]) == (False, "readable")


@pytest.mark.asyncio
async def test_a_dry_run_fails_a_subscription_whose_key_is_gone(db_session, regular_user, user_session):
    item = post(db_session, regular_user)
    key = api_key(db_session, regular_user, ["agents:work", "read"])
    with mcp_auth_context(key.key, scopes=["agents:work", "read"]):
        queued = await upsert(handler={"queue": "q"}, source=FILTER)
    db_session.delete(key)
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await dry_run(subscription_id=queued["id"], item_id=item.id)

    assert (result["matches"], result["failed"]) == (False, "credentials")


@pytest.mark.asyncio
async def test_a_dry_run_only_takes_items_the_caller_can_read(db_session, regular_user, admin_user, user_session):
    hidden = post(db_session, admin_user)
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "q"}, source=FILTER)
        with pytest.raises(ValueError, match=f"unknown item {hidden.id}"):
            await dry_run(subscription_id=queued["id"], item_id=hidden.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, error",
    [
        ({"filter": {"regex": "("}}, "invalid filter source: regex does not compile: "),
        ({"filter": {}}, "invalid filter source: set at least one of "),
        (
            {"filter": {"tags_any": ["ai"]}, "delivery": {"mode": "batch", "window_sec": 5}},
            "invalid filter source: delivery.batch.window_sec: Input should be greater than or equal to 60",
        ),
        ({"filter": {"tags_any": ["ai"]}, "input": "x"}, "invalid filter source: input only applies to cron and timer sources"),
        ({"filter": {"tags_any": ["ai"]}, "on_changes": ["edited"]}, "invalid filter source: on_changes needs on_edit"),
    ],
)
async def test_a_refused_source_is_said_plainly(db_session, regular_user, user_session, source, error):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError) as refused:
            await upsert(handler={"queue": "q"}, source=source)

    assert str(refused.value).startswith(error)
    assert "pydantic" not in str(refused.value) and "\n" not in str(refused.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [True, False])
async def test_enabling_a_tripped_subscription_sends_what_was_waiting_at_once(
    db_session, regular_user, user_session, fake_providers, no_celery_dispatch, resume
):
    d = definition(db_session, regular_user)
    item = post(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        created = await upsert(handler={"agent": d.id}, source=FILTER)
    stored = db_session.get_one(Subscription, created["id"])
    # Sent, and resent, while it was tripping: the fires dropped it.
    waiting = Delivery(
        subscription_id=stored.id, item_id=item.id, content_hash=subscriptions.content_version(item),
        changes=["new"], dispatched_at=datetime.now(timezone.utc) - timedelta(minutes=1), sends=3,
    )
    db_session.add(waiting)
    stored.enabled, stored.state = False, {"tripped": {"reason": "x", "at": datetime.now(timezone.utc).isoformat()}}
    db_session.commit()
    no_celery_dispatch.reset_mock()

    with mcp_auth_context(user_session.token):
        await upsert(subscription_id=stored.id, enabled=True, resume=resume)

    fires = [c.kwargs["args"] for c in no_celery_dispatch.call_args_list if c.args[0] == FIRE_SUBSCRIPTION]
    db_session.refresh(waiting)
    assert fires == [[stored.id, [subscriptions.delivery_ref(item.id, waiting.content_hash)]]]
    assert waiting.sends == 1


# --- Listing, history and deleting ----------------------------------------


@pytest.mark.asyncio
async def test_list_delete_and_cancel(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        cron = await upsert(handler=NOTIFY, source=CRON)
        timer = await upsert(handler=NOTIFY, source={"at": "2999-01-01T09:00:00Z"})
        listed = await list_all()
        await delete(timer_id=timer["id"])
        await delete(subscription_id=cron["id"])
        after = await list_all()
        with_fired = await list_all(include_fired=True)
        with pytest.raises(ValueError, match=f"timer {timer['id']} is already cancelled"):
            await delete(timer_id=timer["id"])

    assert ([s["id"] for s in listed["subscriptions"]], [t["id"] for t in listed["timers"]]) == (
        [cron["id"]], [timer["id"]],
    )
    assert after == {"subscriptions": [], "timers": []}
    assert [(t["id"], t["result"]) for t in with_fired["fired"]] == [(timer["id"], {"status": "skipped:cancelled"})]


@pytest.mark.asyncio
async def test_listing_narrows_to_a_definition_or_a_queue(db_session, regular_user, user_session, fake_providers):
    d, other = definition(db_session, regular_user), definition(db_session, regular_user, name="other")
    with mcp_auth_context(user_session.token):
        on_d = await upsert(name="a", handler={"agent": d.id}, source=CRON | {"input": "x"})
        queued = await upsert(name="b", handler={"queue": "reviews"}, source=FILTER)
        timer = await upsert(handler={"agent": d.id}, source={"at": "2999-01-01T09:00:00Z", "input": "x"})
        by_d = await list_all(definition_id=d.id)
        by_other = await list_all(definition_id=other.id)
        by_queue = await list_all(queue="reviews")

    assert ([s["id"] for s in by_d["subscriptions"]], [t["id"] for t in by_d["timers"]]) == ([on_d["id"]], [timer["id"]])
    assert by_other == {"subscriptions": [], "timers": []}
    assert ([s["id"] for s in by_queue["subscriptions"]], by_queue["timers"]) == ([queued["id"]], [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scopes, sees_filters",
    [
        (["schedule"], False),
        (["schedule:write"], False),
        (["agents:work"], False),
        (["agents:work", "read"], True),
        (["agents"], True),
    ],
)
async def test_filter_subscriptions_are_shown_only_to_keys_that_may_read_them(
    db_session, regular_user, user_session, scopes, sees_filters
):
    with mcp_auth_context(user_session.token):
        cron = await upsert(name="a", handler=NOTIFY, source=CRON)
        queued = await upsert(name="b", handler={"queue": "q"}, source=FILTER)
        timer = await upsert(handler=NOTIFY, source={"at": "2999-01-01T09:00:00Z"})
    key = api_key(db_session, regular_user, scopes)

    with mcp_auth_context(key.key, scopes=scopes):
        listed = await list_all()
        fetched_cron = await fetch(subscription_id=cron["id"])

    shown = [cron["id"], queued["id"]][: 1 + int(sees_filters)]
    assert [s["id"] for s in listed["subscriptions"]] == shown
    assert ([t["id"] for t in listed["timers"]], fetched_cron["subscription"]["id"]) == ([timer["id"]], cron["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [["schedule"], ["agents:work"]])
@pytest.mark.parametrize(
    "call",
    [
        lambda s: fetch(subscription_id=s["id"]),
        lambda s: upsert(subscription_id=s["id"], enabled=False),
        lambda s: upsert(name=s["name"], enabled=False),
        lambda s: delete(subscription_id=s["id"]),
    ],
)
async def test_a_key_that_may_not_read_filters_cannot_reach_one(
    db_session, regular_user, user_session, scopes, call
):
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "q"}, source=FILTER)
    key = api_key(db_session, regular_user, scopes)

    with mcp_auth_context(key.key, scopes=scopes), pytest.raises(ValueError, match="unknown subscription"):
        await call(queued)

    assert db_session.get_one(Subscription, queued["id"]).enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"subscription_id": None, "name": "renamed"},
        {"subscription_id": None, "enabled": True},
        {"subscription_id": None, "source": {"cron": "0 10 * * *"}},
    ],
)
async def test_a_stored_notify_handler_sheds_fields_callers_may_not_set(
    db_session, regular_user, user_session, change
):
    with mcp_auth_context(user_session.token):
        created = await upsert(handler=NOTIFY, source=CRON, enabled=False)
    stored = db_session.get_one(Subscription, created["id"])
    stored.handler = {"notify": stored.handler["notify"] | {"discord_bot_id": 5, "from_address": "x@example.com"}}
    db_session.commit()

    with mcp_auth_context(user_session.token):
        updated = await upsert(**change | {"subscription_id": created["id"]})

    assert updated["handler"] == NOTIFY


@pytest.mark.asyncio
async def test_a_cron_history_reads_tick_outcomes_newest_first(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        cron = await upsert(handler=NOTIFY, source=CRON)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        Event(kind="tick", subscription_id=cron["id"], owner_id=regular_user.id, fire_at=at, data={"missed": m}, result=r)
        for at, m, r in [(now - timedelta(days=1), 0, {"status": "sent"}), (now, 2, None)]
    )
    db_session.commit()

    with mcp_auth_context(user_session.token):
        fetched = await fetch(subscription_id=cron["id"])

    assert fetched["subscription"]["id"] == cron["id"]
    assert [(h["missed"], h["result"]) for h in fetched["deliveries"]] == [(2, None), (0, {"status": "sent"})]


@pytest.mark.asyncio
async def test_a_filter_history_shows_pending_deliveries_and_runs(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "reviews"}, source=FILTER)
    a, b = post(db_session, regular_user), post(db_session, regular_user)
    run = AgentRun(
        user_id=regular_user.id, queue="reviews", spec={}, input="", trigger_kind="ingest",
        trigger_ref={"subscription_id": queued["id"], "item_ids": [a.id]}, subscription_id=queued["id"],
        executor="workers", status="done", max_cost_usd=0,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add_all([
        Delivery(subscription_id=queued["id"], item_id=a.id, content_hash=b"a", status="done", run_id=run.id),
        Delivery(subscription_id=queued["id"], item_id=b.id, content_hash=b"b", changes=["new"]),
    ])
    db_session.commit()

    with mcp_auth_context(user_session.token):
        everything = await fetch(subscription_id=queued["id"])
        for_a = await fetch(subscription_id=queued["id"], item_id=a.id)

    assert sorted((d["item_id"], d["status"], d["run_id"]) for d in everything["deliveries"]) == [
        (a.id, "done", run.id), (b.id, "pending", None),
    ]
    assert [(r["id"], r["status"]) for r in everything["runs"]] == [(run.id, "done")]
    assert ([d["item_id"] for d in for_a["deliveries"]], [r["id"] for r in for_a["runs"]]) == ([a.id], [run.id])


@pytest.mark.asyncio
async def test_deleting_a_definition_takes_its_subscriptions_and_cancels_its_timers(
    db_session, regular_user, user_session, code_agents
):
    d = definition(db_session, regular_user)
    at = {"at": "2999-01-01T09:00:00Z", "input": "x"}
    with mcp_auth_context(user_session.token):
        await upsert(handler={"agent": d.id}, source=CRON | {"input": "x"})
        timer = await upsert(handler={"agent": d.id}, source=at)
        slugged = await upsert(handler={"agent": "echo"}, source=at)
        await agent.delete.fn(definition_id=d.id)

    assert db_session.query(Subscription).count() == 0
    assert db_session.get_one(Event, timer["id"]).result == {"status": "skipped:cancelled"}
    assert db_session.get_one(Event, slugged["id"]).result is None


@pytest.mark.asyncio
async def test_create_only_refuses_a_name_already_taken(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        first = await upsert(name="reviews", handler={"queue": "prs"}, source=FILTER)
        with pytest.raises(ValueError, match="already have a subscription named 'reviews'"):
            await upsert(name="reviews", handler={"queue": "other"}, source=FILTER, create_only=True)
        fresh = await upsert(name="fresh", handler={"queue": "other"}, source=FILTER, create_only=True)

    assert db_session.get_one(Subscription, first["id"]).handler == {"queue": "prs"}
    assert fresh["name"] == "fresh"


@pytest.mark.asyncio
async def test_caps_and_a_subject_are_cleared_with_their_empty_values(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "q"}, source=FILTER, max_per_hour=5, backlog_max=10)
        cron = await upsert(name="ping", handler=NOTIFY, source=CRON)
        cleared = await upsert(subscription_id=queued["id"], max_per_hour=0, backlog_max=0)
        unsubjected = await upsert(subscription_id=cron["id"], handler={"notify": {"subject": ""}})

    assert (cleared["max_per_hour"], cleared["backlog_max"]) == (None, None)
    assert unsubjected["handler"] == {"notify": {k: v for k, v in NOTIFY["notify"].items() if k != "subject"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caps,limits",
    [
        ({}, {"max_per_hour": 7, "backlog_max": None}),
        ({"max_per_hour": 3600, "backlog_max": 10}, {"max_per_hour": 3600, "backlog_max": 10}),
        ({"max_per_hour": 0, "backlog_max": 0}, {"max_per_hour": 7, "backlog_max": None}),
    ],
)
async def test_a_filter_subscription_reports_the_limits_in_effect(
    db_session, regular_user, user_session, monkeypatch, caps, limits
):
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", 7)
    with mcp_auth_context(user_session.token):
        saved = await upsert(name="limited", handler={"queue": "q"}, source=FILTER, **caps)
        listed = await list_all(queue="q")

    assert saved["limits"] == limits
    assert listed["subscriptions"][0]["limits"] == limits


@pytest.mark.asyncio
async def test_a_cron_subscription_has_no_limits(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        saved = await upsert(name="ping", handler=NOTIFY, source=CRON)

    assert "limits" not in saved


def queued_run(db, user, subscription_id, item_id, status="done"):
    run = AgentRun(
        user_id=user.id, queue="reviews", spec={}, input="", trigger_kind="ingest",
        trigger_ref={"subscription_id": subscription_id, "item_ids": [item_id]}, subscription_id=subscription_id,
        executor="workers", status=status, max_cost_usd=0,
    )
    db.add(run)
    db.flush()
    return run


@pytest.mark.asyncio
async def test_a_listing_shows_each_subscriptions_backlog_last_delivery_and_origin(
    db_session, regular_user, user_session
):
    key = api_key(db_session, regular_user, ALL_SCOPES)
    with mcp_auth_context(user_session.token):
        busy = await upsert(name="busy", handler={"queue": "reviews"}, source=FILTER)
        await upsert(name="idle", handler={"queue": "idle"}, source=FILTER)
    with mcp_auth_context(key.key, scopes=ALL_SCOPES):
        cron = await upsert(name="cron", handler=NOTIFY, source=CRON)
    a, b, c = (post(db_session, regular_user) for _ in range(3))
    run = queued_run(db_session, regular_user, busy["id"], a.id, status="failed")
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Delivery(
            subscription_id=busy["id"], item_id=a.id, content_hash=b"a", status="done", run_id=run.id,
            created_at=now - timedelta(minutes=1),
        ),
        Delivery(subscription_id=busy["id"], item_id=b.id, content_hash=b"b", created_at=now - timedelta(hours=2)),
        Delivery(subscription_id=busy["id"], item_id=c.id, content_hash=b"c", created_at=now - timedelta(hours=1)),
        Event(kind="tick", subscription_id=cron["id"], owner_id=regular_user.id, fire_at=now, result={"status": "sent"}),
    ])
    db_session.commit()

    with mcp_auth_context(user_session.token):
        listed = {s["name"]: s for s in (await list_all())["subscriptions"]}

    assert listed["busy"]["activity"] == {
        "pending": 2,
        "oldest_pending_at": (now - timedelta(hours=2)).isoformat(),
        "last": {"status": "done", "at": (now - timedelta(minutes=1)).isoformat(), "run_id": run.id, "run_status": "failed"},
    }
    assert listed["idle"]["activity"] == {"pending": 0, "oldest_pending_at": None, "last": None}
    assert listed["cron"]["activity"] == {
        "pending": 0,
        "oldest_pending_at": None,
        "last": {"status": "sent", "at": now.isoformat(), "run_id": None, "run_status": None},
    }
    assert (listed["busy"]["api_key_name"], listed["cron"]["api_key_name"]) == (None, key.name)


@pytest.mark.asyncio
@pytest.mark.parametrize("by_creator, shown", [(True, "creator"), (False, None)])
async def test_a_key_sees_only_its_own_name_on_what_it_set_up(db_session, regular_user, by_creator, shown):
    creator = APIKey.create(user_id=regular_user.id, name="creator", scopes=ALL_SCOPES)
    other = APIKey.create(user_id=regular_user.id, name="other", scopes=ALL_SCOPES)
    db_session.add_all([creator, other])
    db_session.commit()
    with mcp_auth_context(creator.key, scopes=ALL_SCOPES):
        await upsert(name="cron", handler=NOTIFY, source=CRON)

    with mcp_auth_context((creator if by_creator else other).key, scopes=ALL_SCOPES):
        [listed] = (await list_all())["subscriptions"]

    assert listed["api_key_name"] == shown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pending, expected",
    [
        (True, [("b", None), ("c", None)]),
        (False, [("a", "done")]),
        (None, [("a", "done"), ("c", None), ("b", None)]),
    ],
)
async def test_a_history_lists_pending_deliveries_apart_with_their_runs_status(
    db_session, regular_user, user_session, pending, expected
):
    with mcp_auth_context(user_session.token):
        queued = await upsert(handler={"queue": "reviews"}, source=FILTER)
    items = {name: post(db_session, regular_user) for name in "abc"}
    run = queued_run(db_session, regular_user, queued["id"], items["a"].id)
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Delivery(
            subscription_id=queued["id"], item_id=items["a"].id, content_hash=b"a", status="done", run_id=run.id, created_at=now
        ),
        Delivery(subscription_id=queued["id"], item_id=items["b"].id, content_hash=b"b", created_at=now - timedelta(hours=2)),
        Delivery(subscription_id=queued["id"], item_id=items["c"].id, content_hash=b"c", created_at=now - timedelta(hours=1)),
    ])
    db_session.commit()
    names = {i.id: name for name, i in items.items()}

    with mcp_auth_context(user_session.token):
        fetched = await fetch(subscription_id=queued["id"], pending=pending)

    assert [(names[d["item_id"]], d["run_status"]) for d in fetched["deliveries"]] == expected
