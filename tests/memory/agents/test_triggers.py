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
    Delivery,
    IngestConfig,
    IngestFilter,
    ItemFacts,
    content_version,
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


def backdate(client, trigger_id):
    """Make every buffered item look as if it arrived two minutes ago."""
    key = triggers.buffer_key(trigger_id)
    client.zadd(key, {m: time.time() - 120 for m in client.zrange(key, 0, -1)})


def delivered_ids(call):
    return [Delivery.decode(d).item_id for d in call.kwargs["args"][1]]


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
    "template,edited,expected",
    [
        ("Triage these:\n{items}\nThanks", set(), "Triage these:\n- [blog_post #7] T: body text\nThanks"),
        ("Triage", set(), "Triage\n\n- [blog_post #7] T: body text"),
        ("{items}", {7}, "- [blog_post #7, edited] T: body text"),
    ],
)
def test_render_input(template, edited, expected):
    post = BlogPost(id=7, type="blog_post", title="T", content="body   text")

    assert render_input(template, [post], max_chars=1000, edited=edited) == expected


def test_render_input_caps_titles():
    post = BlogPost(id=7, type="blog_post", title="T" * 10_000, content="body")

    rendered = render_input("{items}", [post], max_chars=100_000)

    assert rendered == f"- [blog_post #7] {'T' * triggers.ITEM_TITLE_CHARS}: body"


def test_render_input_is_clipped_to_the_run_input_limit():
    post = BlogPost(id=7, type="blog_post", title="T", content="body " * 200)

    rendered = render_input("{items}" * 50, [post], max_chars=2000)

    assert len(rendered) == 2000
    assert rendered.endswith("\n[input truncated]")


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
    no_celery_dispatch.assert_called_once_with(
        FIRE_TRIGGER, args=[on.id, [Delivery(post.id, content_version(post).hex(), False).encode()]]
    )


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
    backdate(client, trigger.id)
    due = triggers.sweep_trigger_buffers(client)

    [fired] = no_celery_dispatch.call_args_list
    assert (early, due) == ({}, {trigger.id: 1})
    assert fired.args == (FIRE_TRIGGER,)
    assert fired.kwargs["args"][0] == trigger.id
    assert delivered_ids(fired) == [p.id for p in posts]
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
    assert [delivered_ids(c) for c in no_celery_dispatch.call_args_list] == [
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


def test_dedupe_keys_ignore_order_and_change_with_the_claimed_version():
    a = Delivery(1, "01" * 32, False)
    b = Delivery(5, "05" * 32, False)
    b_edited = Delivery(5, "06" * 32, True)

    assert triggers.dedupe_key(3, [b, a]) == triggers.dedupe_key(3, [a, b])
    assert triggers.dedupe_key(3, [b]) == f"3:5:{'05' * 8}"
    assert triggers.dedupe_key(3, [b]) != triggers.dedupe_key(3, [b_edited])


@pytest.mark.parametrize(
    "deliveries,expected",
    [
        ([Delivery(1, "a", False), Delivery(2, "b", True)], {2}),
        # Buffered fresh, then edited before the batch went out: the run is its first sight.
        ([Delivery(1, "a", False), Delivery(1, "b", True)], set()),
    ],
)
def test_edited_ids_label_by_what_this_delivery_holds(deliveries, expected):
    assert triggers.edited_ids(deliveries) == expected


def test_content_versions_follow_what_an_agent_sees():
    post = BlogPost(title="T", content="body")
    retitled = BlogPost(title="T2", content="body")
    slack = SlackMessage(content="hi <@U1>", resolved_content="hi @al")
    # Slack edits replace content but leave resolved_content as it was.
    slack_edited = SlackMessage(content="bye <@U1>", resolved_content="hi @al")

    assert content_version(post) == content_version(BlogPost(title="T", content="body"))
    assert content_version(post) != content_version(retitled)
    assert content_version(slack) != content_version(slack_edited)


@pytest.mark.parametrize("on_edit", [False, True])
@pytest.mark.parametrize(
    "delivery", [{"mode": "per_item"}, {"mode": "batch", "window_sec": 60, "max_items": 10}]
)
def test_reprocessing_a_matched_item_matches_nothing(
    db_session, admin_user, client, no_celery_dispatch, delivery, on_edit
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        delivery=delivery,
        on_edit=on_edit,
    )
    post = blog_post(db_session)

    first = triggers.match_ingest_triggers(post.id, client)
    again = triggers.match_ingest_triggers(post.id, client)

    assert (first, again) == ([trigger.id], [])
    assert no_celery_dispatch.call_count + client.zcard(triggers.buffer_key(trigger.id)) == 1


def edit(db, item):
    """Change the content only: not every ingester updates sha256 on an edit."""
    item.content = f"{item.content}, edited"
    db.commit()


@pytest.mark.parametrize("on_edit,runs", [(False, 1), (True, 2)])
def test_an_edit_fires_again_only_with_on_edit(
    db_session, admin_user, client, no_celery_dispatch, on_edit, runs
):
    make_trigger(db_session, make_definition(db_session, admin_user), on_edit=on_edit)
    post = blog_post(db_session)

    triggers.match_ingest_triggers(post.id, client)
    edit(db_session, post)
    triggers.match_ingest_triggers(post.id, client)
    triggers.match_ingest_triggers(post.id, client)

    assert no_celery_dispatch.call_count == runs
    assert db_session.query(AgentTriggerMatch).count() == runs


def test_a_batch_buffers_each_version_of_an_item_edited_while_buffered(
    db_session, admin_user, client, no_celery_dispatch
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        delivery={"mode": "batch", "window_sec": 60, "max_items": 10},
        on_edit=True,
    )
    post = blog_post(db_session)

    first = triggers.match_ingest_triggers(post.id, client)
    edit(db_session, post)
    edited = triggers.match_ingest_triggers(post.id, client)

    assert (first, edited) == ([trigger.id], [trigger.id])
    assert [
        Delivery.decode(m.decode()).edited
        for m in client.zrange(triggers.buffer_key(trigger.id), 0, -1)
    ] == [False, True]


@pytest.mark.parametrize("on_edit", [False, True])
def test_items_stored_before_the_trigger_was_active_never_match(
    db_session, admin_user, client, no_celery_dispatch, on_edit
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=on_edit
    )
    old = blog_post(db_session, inserted_at=trigger.active_since - timedelta(minutes=1))

    before = triggers.match_ingest_triggers(old.id, client)
    edit(db_session, old)

    assert (before, triggers.match_ingest_triggers(old.id, client)) == ([], [])


def test_an_edit_to_an_item_matched_long_ago_still_fires(
    db_session, admin_user, client, no_celery_dispatch
):
    long_ago = datetime.now(timezone.utc) - timedelta(days=60)
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=True
    )
    trigger.active_since = long_ago
    post = blog_post(db_session, inserted_at=long_ago)
    db_session.add(
        AgentTriggerMatch(
            trigger_id=trigger.id,
            item_id=post.id,
            content_hash=content_version(post),
            created_at=long_ago,
        )
    )
    db_session.commit()

    unchanged = triggers.match_ingest_triggers(post.id, client)
    edit(db_session, post)

    assert (unchanged, triggers.match_ingest_triggers(post.id, client)) == ([], [trigger.id])


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


@pytest.mark.parametrize(
    "on_edit,kept",
    [
        (False, {b"fresh", b"racing new"}),
        # An old claim goes once a newer one is itself old: a claim that young may
        # still be unclaimed by a failed delivery, leaving its item no claims at all.
        (True, {b"fresh", b"stale newest", b"racing old", b"racing new"}),
    ],
)
def test_old_claims_are_purged_but_on_edit_keeps_each_items_newest(
    db_session, admin_user, client, on_edit, kept
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=on_edit
    )
    fresh, stale, racing = blog_post(db_session), blog_post(db_session), blog_post(db_session)
    now = datetime.now(timezone.utc)
    claims = [
        (fresh, b"fresh", now),
        (stale, b"stale oldest", now - timedelta(days=40)),
        (stale, b"stale newest", now - timedelta(days=31)),
        (racing, b"racing old", now - timedelta(days=40)),
        (racing, b"racing new", now),
    ]
    db_session.add_all(
        AgentTriggerMatch(
            trigger_id=trigger.id, item_id=item.id, content_hash=version, created_at=at
        )
        for item, version, at in claims
    )
    db_session.commit()

    purged = triggers.purge_matches(db_session, timedelta(days=30))

    assert purged == len(claims) - len(kept)
    assert {bytes(m.content_hash) for m in db_session.query(AgentTriggerMatch)} == kept


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
