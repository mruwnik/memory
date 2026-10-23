import threading
import time
from datetime import timedelta
from datetime import datetime, timezone
from typing import Any

import fakeredis
import pytest
from pydantic import ValidationError

from memory.agents import triggers
from memory.agents.triggers import (
    IngestConfig,
    IngestFilter,
    ItemFacts,
    item_facts,
    matches,
    regex_search,
    render_input,
)
from memory.common.celery_app import FIRE_TRIGGER, MATCH_INGEST_TRIGGERS
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    AgentTriggerMatch,
    BlogPost,
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    SlackChannel,
    SlackMessage,
    SlackWorkspace,
)
from memory.common.db.models.slack import SlackApp, SlackUserCredentials
from tests.conftest import unique_sha256

SPEC = {"system_prompt": "x", "model": "fake-rich/m", "reasoning": "high"}


@pytest.fixture
def client():
    return fakeredis.FakeRedis()


def facts(**overrides):
    base: dict[str, Any] = dict(
        source_type="blog_post",
        tags=frozenset({"ai"}),
        channel_id=None,
        authors=frozenset({"alice"}),
        text="A post about Agent Runs",
    )
    return ItemFacts(**(base | overrides))


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"filter": {}}, "set at least one of"),
        ({"filter": {"source_types": ["nope"]}}, "unknown source types"),
        ({"filter": {"regex": "("}}, "regex does not compile"),
        ({"filter": {"regex": "a" * 501}}, "at most 500"),
        (
            {"filter": {"tags_any": ["x"]}, "delivery": {"mode": "batch", "window_sec": 5}},
            "greater than or equal to 60",
        ),
        ({"filter": {"tags_any": ["x"]}, "bogus": 1}, "Extra inputs"),
    ],
)
def test_config_validation_names_the_problem(config, expected):
    with pytest.raises(ValidationError, match=expected):
        IngestConfig.model_validate(config)


@pytest.mark.parametrize(
    "f,expected",
    [
        ({"source_types": ["blog_post"]}, True),
        ({"source_types": ["mail_message"]}, False),
        ({"tags_any": ["ai", "x"]}, True),
        ({"tags_any": ["x"]}, False),
        ({"channel_ids": ["10"]}, False),
        ({"text_contains": ["agent runs"]}, True),
        ({"text_contains": ["nothing"]}, False),
        ({"regex": r"Agent\s+Runs"}, True),
        ({"regex": r"^Runs"}, False),
        ({"tags_any": ["ai"], "exclude_authors": ["ALICE"]}, False),
        ({"tags_any": ["ai"], "text_contains": ["nothing"]}, False),
    ],
)
def test_matches_every_set_criterion(f, expected):
    assert matches(IngestFilter.model_validate(f), facts()) is expected


def test_regexes_refuse_to_run_off_the_main_thread():
    errors = []
    worker = threading.Thread(
        target=lambda: errors.append(pytest.raises(RuntimeError, regex_search, "a", "a"))
    )
    worker.start()
    worker.join()

    assert "main thread" in str(errors[0].value)


def test_a_runaway_regex_times_out_as_no_match():
    started = time.monotonic()

    assert regex_search(r"(a+)+$", "a" * 40 + "!") is False
    assert time.monotonic() - started < 2


def test_discord_facts_carry_channel_and_author_identities():
    message = DiscordMessage(
        type="discord_message",
        channel_id=10,
        author_id=100,
        author=DiscordUser(id=100, username="Alice", display_name="Al"),
        content="hi",
        tags=["t"],
    )

    got = item_facts(message)

    assert (got.source_type, got.channel_id, got.authors, got.text) == (
        "discord_message",
        "10",
        frozenset({"100", "alice", "al"}),
        "hi",
    )


@pytest.mark.parametrize(
    "template,expected",
    [
        ("Triage these:\n{items}\nThanks", "Triage these:\n- [blog_post #7] T: body text\nThanks"),
        ("Triage", "Triage\n\n- [blog_post #7] T: body text"),
    ],
)
def test_render_input(template, expected):
    post = BlogPost(id=7, type="blog_post", title="T", content="body   text")

    assert render_input(template, [post]) == expected


def make_definition(db, user):
    d = AgentDefinition(user_id=user.id, name="t", spec=SPEC)
    db.add(d)
    db.commit()
    return d


def make_trigger(db, definition, enabled=True, **config):
    t = AgentTrigger(
        definition_id=definition.id,
        kind="ingest",
        enabled=enabled,
        config=IngestConfig.model_validate(
            {"filter": {"tags_any": ["ai"]}} | config
        ).model_dump(mode="json"),
    )
    db.add(t)
    db.commit()
    return t


def blog_post(db, tags=("ai",), project_id=None, inserted_at=None):
    post = BlogPost(
        **({"inserted_at": inserted_at} if inserted_at else {}),
        sha256=unique_sha256("post"),
        modality="blog",
        title="Post",
        content="about agents",
        tags=list(tags),
        embed_status="STORED",
        project_id=project_id,
    )
    db.add(post)
    db.commit()
    return post


def test_per_item_triggers_fire_at_once(
    db_session, admin_user, client, no_celery_dispatch
):
    d = make_definition(db_session, admin_user)
    on = make_trigger(db_session, d)
    make_trigger(db_session, d, enabled=False)
    make_trigger(db_session, d, filter={"tags_any": ["other"]})
    post = blog_post(db_session)

    matched = triggers.match_ingest_triggers(post.id, client)

    assert matched == [on.id]
    no_celery_dispatch.assert_called_once_with(FIRE_TRIGGER, args=[on.id, [post.id]])


def test_items_the_owner_cannot_read_never_match(
    db_session, regular_user, client, no_celery_dispatch
):
    d = make_definition(db_session, regular_user)
    make_trigger(db_session, d)
    post = blog_post(db_session, project_id=None)

    assert triggers.match_ingest_triggers(post.id, client) == []
    no_celery_dispatch.assert_not_called()


def discord_message(db, author_id):
    db.add_all(
        [
            DiscordServer(id=1, name="s"),
            DiscordUser(id=100, username="alice"),
            DiscordUser(id=200, username="ourbot"),
            DiscordBot(id=200, name="ourbot"),
        ]
    )
    db.flush()
    db.add(DiscordChannel(id=10, server_id=1, name="general", channel_type="text"))
    db.flush()
    message = DiscordMessage(
        sha256=unique_sha256("dm"),
        modality="message",
        content="ai news",
        message_id=1,
        channel_id=10,
        server_id=1,
        author_id=author_id,
        sent_at=datetime.now(timezone.utc),
        embed_status="STORED",
        tags=["ai"],
    )
    db.add(message)
    db.commit()
    return message


@pytest.mark.parametrize("author_id,fires", [(100, True), (200, False)])
def test_our_own_discord_bots_never_trigger(
    db_session, admin_user, client, no_celery_dispatch, author_id, fires
):
    make_trigger(db_session, make_definition(db_session, admin_user))
    message = discord_message(db_session, author_id)

    assert bool(triggers.match_ingest_triggers(message.id, client)) is fires


def slack_message(db, owner, author_id):
    app = SlackApp(client_id="c", name="app")
    db.add_all([app, SlackWorkspace(id="T1", name="w")])
    db.flush()
    db.add_all(
        [
            SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel"),
            SlackUserCredentials(
                slack_app_id=app.id, workspace_id="T1", user_id=owner.id, slack_user_id="U_OWNER"
            ),
        ]
    )
    db.flush()
    message = SlackMessage(
        sha256=unique_sha256("sm"),
        modality="message",
        content="ai news",
        message_ts="1.0",
        channel_id="C1",
        workspace_id="T1",
        author_id=author_id,
        embed_status="STORED",
        tags=["ai"],
    )
    db.add(message)
    db.commit()
    return message


@pytest.mark.parametrize("author_id,fires", [("U_OTHER", True), ("U_OWNER", False)])
def test_the_owners_slack_integration_user_never_triggers(
    db_session, admin_user, client, no_celery_dispatch, author_id, fires
):
    make_trigger(db_session, make_definition(db_session, admin_user))
    message = slack_message(db_session, admin_user, author_id)

    assert bool(triggers.match_ingest_triggers(message.id, client)) is fires


def test_batch_triggers_buffer_until_the_window_closes(
    db_session, admin_user, client, no_celery_dispatch
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        delivery={"mode": "batch", "window_sec": 60, "max_items": 10},
    )
    posts = [blog_post(db_session) for _ in range(3)]
    for post in posts:
        triggers.match_ingest_triggers(post.id, client)

    early = triggers.sweep_trigger_buffers(client)
    client.zadd(triggers.buffer_key(trigger.id), {str(posts[0].id): time.time() - 120})
    due = triggers.sweep_trigger_buffers(client)

    assert (early, due) == ({}, {trigger.id: 1})
    no_celery_dispatch.assert_called_once_with(
        FIRE_TRIGGER, args=[trigger.id, sorted(p.id for p in posts)]
    )
    assert client.zcard(triggers.buffer_key(trigger.id)) == 0


def test_a_full_batch_fires_before_its_window(
    db_session, admin_user, client, no_celery_dispatch
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        delivery={"mode": "batch", "window_sec": 3600, "max_items": 2},
    )
    posts = [blog_post(db_session) for _ in range(5)]
    for post in posts:
        triggers.match_ingest_triggers(post.id, client)

    fired = triggers.sweep_trigger_buffers(client)

    assert fired == {trigger.id: 2}
    assert [c.kwargs["args"][1] for c in no_celery_dispatch.call_args_list] == [
        [posts[0].id, posts[1].id],
        [posts[2].id, posts[3].id],
    ]
    assert client.zcard(triggers.buffer_key(trigger.id)) == 1


def test_matching_is_queued_only_when_an_ingest_trigger_exists(
    db_session, admin_user, no_celery_dispatch
):
    triggers.queue_trigger_match(1)
    make_trigger(db_session, make_definition(db_session, admin_user))
    triggers.queue_trigger_match(2)

    no_celery_dispatch.assert_called_once_with(MATCH_INGEST_TRIGGERS, args=[2])


def test_queueing_a_match_never_raises(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(triggers, "make_session", broken)

    triggers.queue_trigger_match(1)


def test_batch_dedupe_key_ignores_order():
    assert triggers.dedupe_key(3, [5, 1]) == triggers.dedupe_key(3, [1, 5])
    assert triggers.dedupe_key(3, [5]) == "3:5"


@pytest.mark.parametrize(
    "delivery", [{"mode": "per_item"}, {"mode": "batch", "window_sec": 60, "max_items": 10}]
)
def test_reprocessing_a_matched_item_matches_nothing(
    db_session, admin_user, client, no_celery_dispatch, delivery
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), delivery=delivery
    )
    post = blog_post(db_session)

    first = triggers.match_ingest_triggers(post.id, client)
    again = triggers.match_ingest_triggers(post.id, client)

    assert (first, again) == ([trigger.id], [])
    assert no_celery_dispatch.call_count + client.zcard(triggers.buffer_key(trigger.id)) == 1


def test_items_stored_before_the_trigger_was_active_never_match(
    db_session, admin_user, client, no_celery_dispatch
):
    trigger = make_trigger(db_session, make_definition(db_session, admin_user))
    old = blog_post(db_session, inserted_at=trigger.active_since - timedelta(minutes=1))

    assert triggers.match_ingest_triggers(old.id, client) == []


def test_a_delivery_failure_unclaims_and_spares_the_other_triggers(
    db_session, admin_user, client, no_celery_dispatch
):
    d = make_definition(db_session, admin_user)
    broken = make_trigger(db_session, d)
    healthy = make_trigger(db_session, d)
    post = blog_post(db_session)

    def broker_down_for_the_first(name, args):
        if args[0] == broken.id:
            raise ConnectionError("broker down")

    no_celery_dispatch.side_effect = broker_down_for_the_first

    assert triggers.match_ingest_triggers(post.id, client) == [healthy.id]
    assert [
        m.trigger_id for m in db_session.query(AgentTriggerMatch).filter_by(item_id=post.id)
    ] == [healthy.id]


def test_old_claims_are_purged(db_session, admin_user, client):
    trigger = make_trigger(db_session, make_definition(db_session, admin_user))
    fresh, stale = blog_post(db_session), blog_post(db_session)
    db_session.add_all(
        [
            AgentTriggerMatch(trigger_id=trigger.id, item_id=fresh.id),
            AgentTriggerMatch(
                trigger_id=trigger.id,
                item_id=stale.id,
                created_at=datetime.now(timezone.utc) - timedelta(days=31),
            ),
        ]
    )
    db_session.commit()

    assert triggers.purge_matches(db_session, timedelta(days=30)) == 1
    assert [m.item_id for m in db_session.query(AgentTriggerMatch)] == [fresh.id]


def test_items_older_than_claim_retention_never_match(
    db_session, admin_user, client, no_celery_dispatch
):
    trigger = make_trigger(db_session, make_definition(db_session, admin_user))
    trigger.active_since = datetime.now(timezone.utc) - timedelta(days=60)
    db_session.commit()
    old = blog_post(
        db_session,
        inserted_at=datetime.now(timezone.utc) - triggers.MATCH_RETENTION - timedelta(hours=1),
    )

    assert triggers.match_ingest_triggers(old.id, client) == []
