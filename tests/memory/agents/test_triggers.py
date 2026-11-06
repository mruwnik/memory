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
)
from memory.common.celery_app import FIRE_TRIGGER, MATCH_INGEST_TRIGGERS
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    AgentTriggerMatch,
    BeeperMessage,
    BlogPost,
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    GithubItem,
    SlackChannel,
    SlackMessage,
    SlackWorkspace,
)
from memory.common.db.models.slack import SlackApp, SlackUserCredentials
from memory.common.content_processing import process_content_item
from tests.conftest import unique_sha256

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}


@pytest.fixture
def client():
    return fakeredis.FakeRedis()


def backdate(client, trigger_id):
    """Make every buffered item look as if it arrived two minutes earlier.

    Shifting keeps arrival order: equal scores sort by member, i.e. by the
    item id as a string.
    """
    key = triggers.buffer_key(trigger_id)
    buffered = client.zrange(key, 0, -1, withscores=True)
    client.zadd(key, {m: score - 120 for m, score in buffered})


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
        ({"filter": {"exclude_channel_ids": ["C1"]}}, "set at least one of"),
        ({"filter": {"source_types": ["nope"]}}, "unknown source types"),
        ({"filter": {"regex": "("}}, "regex does not compile"),
        ({"filter": {"regex": "a" * 501}}, "at most 500"),
        (
            {"filter": {"tags_any": ["x"]}, "delivery": {"mode": "batch", "window_sec": 5}},
            "greater than or equal to 60",
        ),
        ({"filter": {"tags_any": ["x"]}, "bogus": 1}, "Extra inputs"),
        ({"filter": {"tags_any": ["x"]}, "on_changes": ["commits"]}, "on_changes needs on_edit"),
        (
            {"filter": {"tags_any": ["x"]}, "on_edit": True, "on_changes": ["pushed"]},
            "Input should be",
        ),
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
        ({"repos": ["EquiStamp/Memory"]}, True),
        ({"repos": ["equistamp/other"]}, False),
        ({"mentions_me": True}, True),
        ({"tags_any": ["ai"], "mentions_me": True, "repos": ["x/y"]}, False),
    ],
)
def test_matches_every_set_criterion(f, expected):
    item = facts(repo="equistamp/memory", mentions=frozenset({"200", "u_other"}))
    assert matches(IngestFilter.model_validate(f), item, mine=frozenset({"200"})) is expected


@pytest.mark.parametrize("mine", [frozenset(), frozenset({"u_bot"})])
def test_mentions_me_needs_a_mention_of_one_of_mine(mine):
    f = IngestFilter.model_validate({"mentions_me": True})

    assert matches(f, facts(mentions=frozenset({"200"})), mine=mine) is False


@pytest.mark.parametrize(
    "item,mentions",
    [
        (
            DiscordMessage(
                type="discord_message", channel_id=1, author_id=1, content="<@200> hi <@!201>"
            ),
            {"200", "201"},
        ),
        (
            SlackMessage(type="slack_message", channel_id="C1", content="<@U1> and <@W2|bob>"),
            {"u1", "w2"},
        ),
        (SlackMessage(type="slack_message", channel_id="C1", content="<#C1> <!here>"), set()),
    ],
)
def test_message_facts_carry_the_ids_they_mention(item, mentions):
    assert item_facts(item).mentions == frozenset(mentions)


def test_github_facts_carry_the_repo():
    item = GithubItem(type="github_item", repo_path="EquiStamp/Memory", author="al")

    assert item_facts(item).repo == "equistamp/memory"


def test_beeper_facts_carry_chat_and_sender():
    item = BeeperMessage(
        type="beeper_message", chat_id=5, sender_id="@a:beeper.com", sender_name="Alice"
    )

    got = item_facts(item)

    assert (got.channel_id, got.authors) == ("5", frozenset({"@a:beeper.com", "alice"}))


@pytest.mark.parametrize(
    "f,item,expected",
    [
        (
            {"source_types": ["slack_message"], "exclude_channel_ids": ["C1"]},
            SlackMessage(type="slack_message", channel_id="C1", message_ts="1.0"),
            False,
        ),
        # Slack thread replies carry their parent channel's id.
        (
            {"source_types": ["slack_message"], "exclude_channel_ids": ["C1"]},
            SlackMessage(
                type="slack_message", channel_id="C1", message_ts="2.0", thread_ts="1.0"
            ),
            False,
        ),
        (
            {"source_types": ["discord_message"], "exclude_channel_ids": ["10"]},
            DiscordMessage(type="discord_message", channel_id=10, author_id=1),
            False,
        ),
        (
            {"source_types": ["discord_message"], "exclude_channel_ids": ["10"]},
            DiscordMessage(
                type="discord_message",
                channel_id=20,
                author_id=1,
                channel=DiscordChannel(id=20, name="t", channel_type="thread", parent_id=10),
            ),
            False,
        ),
        (
            {"source_types": ["slack_message"], "exclude_channel_ids": ["C1"]},
            SlackMessage(type="slack_message", channel_id="C2", message_ts="1.0"),
            True,
        ),
        (
            {"source_types": ["discord_message"], "exclude_channel_ids": ["10"]},
            DiscordMessage(
                type="discord_message",
                channel_id=20,
                author_id=1,
                channel=DiscordChannel(id=20, name="t", channel_type="thread", parent_id=11),
            ),
            True,
        ),
        (
            {"channel_ids": ["C1", "C2"], "exclude_channel_ids": ["C2"]},
            SlackMessage(type="slack_message", channel_id="C1", message_ts="1.0"),
            True,
        ),
        (
            {"channel_ids": ["C1", "C2"], "exclude_channel_ids": ["C2"]},
            SlackMessage(type="slack_message", channel_id="C2", message_ts="1.0"),
            False,
        ),
        (
            {"channel_ids": [" C1 "], "exclude_channel_ids": [" C2"]},
            SlackMessage(type="slack_message", channel_id="C1", message_ts="1.0"),
            True,
        ),
        (
            {"channel_ids": [" C1 ", "C2"], "exclude_channel_ids": ["C2 "]},
            SlackMessage(type="slack_message", channel_id="C2", message_ts="1.0"),
            False,
        ),
    ],
)
def test_excluded_channels_never_match(f, item, expected):
    assert matches(IngestFilter.model_validate(f), item_facts(item)) is expected


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
        FIRE_TRIGGER,
        args=[on.id, [Delivery(post.id, content_version(post).hex(), False, ("new",)).encode()]],
    )


def test_items_the_owner_cannot_read_never_match(
    db_session, regular_user, client, no_celery_dispatch
):
    d = make_definition(db_session, regular_user)
    make_trigger(db_session, d)
    post = blog_post(db_session, project_id=None)

    assert triggers.match_ingest_triggers(post.id, client) == []
    no_celery_dispatch.assert_not_called()


def discord_message(db, author_id, bot_token="tok", content="ai news", owner=None):
    bot = DiscordBot(
        id=200, application_id=200, name="ourbot", created_by_user_id=owner and owner.id
    )
    bot.token = bot_token
    db.add_all(
        [
            DiscordServer(id=1, name="s"),
            DiscordUser(id=100, username="alice"),
            DiscordUser(id=200, username="ourbot"),
            bot,
        ]
    )
    db.flush()
    db.add(DiscordChannel(id=10, server_id=1, name="general", channel_type="text"))
    db.flush()
    message = DiscordMessage(
        sha256=unique_sha256("dm"),
        modality="message",
        content=content,
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


@pytest.mark.parametrize(
    "author_id, bot_token, fires",
    [(100, "tok", True), (200, "tok", False), (200, None, True)],
)
def test_our_own_discord_bots_never_trigger(
    db_session, admin_user, client, no_celery_dispatch, author_id, bot_token, fires
):
    """Only bots that authenticate with a token are known to post as their
    application id; a token-less row's id is just what its owner typed."""
    make_trigger(db_session, make_definition(db_session, admin_user))
    message = discord_message(db_session, author_id, bot_token)

    assert bool(triggers.match_ingest_triggers(message.id, client)) is fires


@pytest.mark.parametrize("content,fires", [("<@200>", True), ("hi", False)])
def test_a_short_message_mentioning_our_bot_fires(
    db_session, admin_user, client, no_celery_dispatch, monkeypatch, content, fires
):
    queued = []
    monkeypatch.setattr("memory.common.content_processing.queue_trigger_match", queued.append)
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        filter={"source_types": ["discord_message"], "mentions_me": True},
    )
    message = discord_message(db_session, 100, content=content, owner=admin_user)
    message.embed_status = "RAW"

    process_content_item(message, db_session)

    assert message.embed_status == "SKIPPED"
    assert [triggers.match_ingest_triggers(i, client) for i in queued] == [
        [trigger.id] if fires else []
    ]


def slack_message(db, owner, author_id):
    app = SlackApp(client_id="c", name="app")
    db.add_all([app, SlackWorkspace(id="T1", name="w")])
    db.flush()
    db.add_all(
        [
            SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel"),
            SlackUserCredentials(
                slack_app_id=app.id,
                workspace_id="T1",
                user_id=owner.id,
                slack_user_id="U_OWNER",
                bot_user_id="U_BOT",
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


@pytest.mark.parametrize(
    "author_id,fires", [("U_OTHER", True), ("U_OWNER", False), ("U_BOT", False)]
)
def test_the_owners_slack_integration_and_bot_users_never_trigger(
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
    triggers.queue_trigger_match(2, ["commits"])

    no_celery_dispatch.assert_called_once_with(MATCH_INGEST_TRIGGERS, args=[2, ["commits"]])


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
        (
            [Delivery(1, "a", False, ("new",)), Delivery(2, "b", True, ("commits",))],
            {"1": ["new"], "2": ["commits"]},
        ),
        # Buffered fresh, then edited before the batch went out: the run is its first sight.
        (
            [Delivery(1, "a", False, ("new",)), Delivery(1, "b", True, ("comments", "edited"))],
            {"1": ["new"]},
        ),
    ],
)
def test_changes_by_item_merges_each_items_deliveries(deliveries, expected):
    assert triggers.changes_by_item(deliveries) == expected


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


def test_on_changes_limits_which_edits_fire(
    db_session, admin_user, client, no_celery_dispatch
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=True, on_changes=["commits"]
    )
    post = blog_post(db_session)

    new = triggers.match_ingest_triggers(post.id, client, changes=["state"])
    edit(db_session, post)
    commented = triggers.match_ingest_triggers(post.id, client, changes=["comments"])
    unknown = triggers.match_ingest_triggers(post.id, client)
    pushed = triggers.match_ingest_triggers(post.id, client, changes=["comments", "commits"])

    assert (new, commented, unknown, pushed) == ([trigger.id], [], [], [trigger.id])
    assert [
        Delivery.decode(c.kwargs["args"][1][0]).changes for c in no_celery_dispatch.call_args_list
    ] == [("new", "state"), ("comments", "commits")]


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
