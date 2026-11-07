import itertools
import threading
import time
from datetime import timedelta
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from memory.agents import triggers
from memory.agents.triggers import (
    IngestConfig,
    IngestFilter,
    ItemFacts,
    content_version,
    item_facts,
    matches,
    regex_search,
)
from memory.common.celery_app import FIRE_TRIGGER
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
    GithubIssue,
    GithubPR,
    GithubPRData,
    SlackChannel,
    SlackMessage,
    SlackWorkspace,
)
from memory.common.db.models.slack import SlackApp, SlackUserCredentials
from memory.common.content_processing import process_content_item
from tests.conftest import route_item, unique_sha256

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
TRIGGER_NAMES = (f"t{i}" for i in itertools.count())


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
    "change",
    ["labels", "assignees", "draft", "checks_complete", "title", "due", "priority", "state"],
)
def test_on_changes_can_name_what_syncs_report(change):
    config = IngestConfig.model_validate(
        {"filter": {"tags_any": ["x"]}, "on_edit": True, "on_changes": [change]}
    )

    assert config.refires([change]) is True


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
    item = GithubPR(kind="pr", repo_path="EquiStamp/Memory", author="al")

    assert item_facts(item).repo == "equistamp/memory"


@pytest.mark.parametrize(
    "source_types,item,expected",
    [
        (["github_pr"], GithubPR(kind="pr", repo_path="o/r"), True),
        (["github_pr"], GithubIssue(kind="issue", repo_path="o/r"), False),
        (["github_issue"], GithubIssue(kind="issue", repo_path="o/r"), True),
        (["github_issue"], GithubPR(kind="pr", repo_path="o/r"), False),
    ],
)
def test_source_types_tell_prs_from_issues(source_types, item, expected):
    f = IngestFilter.model_validate({"source_types": source_types})

    assert matches(f, item_facts(item)) is expected


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
        user_id=definition.user_id,
        name=next(TRIGGER_NAMES),
        definition_id=definition.id,
        handler={"agent": definition.id},
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
    db_session, admin_user, no_celery_dispatch
):
    d = make_definition(db_session, admin_user)
    on = make_trigger(db_session, d)
    make_trigger(db_session, d, enabled=False)
    make_trigger(db_session, d, filter={"tags_any": ["other"]})
    post = blog_post(db_session)

    matched = route_item(db_session, post.id)

    assert matched == [on.id]
    no_celery_dispatch.assert_called_once_with(
        FIRE_TRIGGER,
        args=[on.id, [f"{post.id}:{content_version(post).hex()}"]],
    )


@pytest.mark.parametrize("handler", [{"queue": "inbox"}, {"agent": "fact-check"}])
def test_triggers_without_a_definition_are_not_matched_yet(
    db_session, admin_user, no_celery_dispatch, handler
):
    db_session.add(
        AgentTrigger(
            user_id=admin_user.id,
            name="q",
            handler=handler,
            kind="ingest",
            config=IngestConfig.model_validate({"filter": {"tags_any": ["ai"]}}).model_dump(),
        )
    )
    db_session.commit()
    post = blog_post(db_session)

    assert route_item(db_session, post.id) == []
    no_celery_dispatch.assert_not_called()


@pytest.mark.parametrize(
    "with_definition,handler",
    [
        (True, {"agent": 999}),
        (True, {"queue": "inbox"}),
        (False, {"agent": 1}),
        (False, {}),
        (False, {"agent": "x", "queue": "y"}),
        (False, {"agent": ""}),
        (False, {"queue": ""}),
        (False, {"queue": "inbox", "extra": 1}),
    ],
)
def test_a_triggers_handler_must_agree_with_its_definition(
    db_session, admin_user, with_definition, handler
):
    d = make_definition(db_session, admin_user)
    db_session.add(
        AgentTrigger(
            user_id=admin_user.id,
            name="t",
            definition_id=d.id if with_definition else None,
            handler=handler,
            kind="ingest",
            config={},
        )
    )

    with pytest.raises(IntegrityError, match="agent_triggers_handler_check"):
        db_session.commit()


def test_items_the_owner_cannot_read_never_match(
    db_session, regular_user, no_celery_dispatch
):
    d = make_definition(db_session, regular_user)
    make_trigger(db_session, d)
    post = blog_post(db_session, project_id=None)

    assert route_item(db_session, post.id) == []
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
    db_session, admin_user, no_celery_dispatch, author_id, bot_token, fires
):
    """Only bots that authenticate with a token are known to post as their
    application id; a token-less row's id is just what its owner typed."""
    make_trigger(db_session, make_definition(db_session, admin_user))
    message = discord_message(db_session, author_id, bot_token)

    assert bool(route_item(db_session, message.id)) is fires


@pytest.mark.parametrize("content,fires", [("<@200>", True), ("hi", False)])
def test_a_short_message_mentioning_our_bot_fires(
    db_session, admin_user, no_celery_dispatch, content, fires
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        filter={"source_types": ["discord_message"], "mentions_me": True},
    )
    message = discord_message(db_session, 100, content=content, owner=admin_user)
    message.embed_status = "RAW"

    process_content_item(message, db_session)

    assert message.embed_status == "SKIPPED"
    assert triggers.route_events() == ([trigger.id] if fires else [])


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
    db_session, admin_user, no_celery_dispatch, author_id, fires
):
    make_trigger(db_session, make_definition(db_session, admin_user))
    message = slack_message(db_session, admin_user, author_id)

    assert bool(route_item(db_session, message.id)) is fires


def delivery(item_id, version, edited=False, changes=()):
    return AgentTriggerMatch(
        item_id=item_id, content_hash=version, edited=edited, changes=list(changes)
    )


def test_dedupe_keys_ignore_order_and_change_with_the_claimed_version():
    a = delivery(1, b"\x01" * 32)
    b = delivery(5, b"\x05" * 32)
    b_edited = delivery(5, b"\x06" * 32, edited=True)

    assert triggers.dedupe_key(3, [b, a]) == triggers.dedupe_key(3, [a, b])
    assert triggers.dedupe_key(3, [b]) == f"3:5:{'05' * 8}"
    assert triggers.dedupe_key(3, [b]) != triggers.dedupe_key(3, [b_edited])


@pytest.mark.parametrize(
    "deliveries,expected",
    [
        (
            [delivery(1, b"a", changes=["new"]), delivery(2, b"b", True, ["commits"])],
            {"1": ["new"], "2": ["commits"]},
        ),
        # Buffered fresh, then edited before the batch went out: the run is its first sight.
        (
            [delivery(1, b"a", changes=["new"]), delivery(1, b"b", True, ["comments", "edited"])],
            {"1": ["new"]},
        ),
        # Never seen, matched for the news its sync reported.
        (
            [delivery(1, b"a", changes=["commits", "new"]), delivery(1, b"b", True, ["comments"])],
            {"1": ["commits", "new"]},
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


def pr(**overrides):
    fields = {
        "title": "T",
        "content": "body",
        "head_sha": "a",
        "state": "open",
        "checks": {"tests": "failure"},
        "labels": ["bug"],
        "assignees": ["bo"],
        "draft": False,
    }
    return GithubPR(**{**fields, **overrides})


@pytest.mark.parametrize(
    "changed",
    [
        {"head_sha": "b"},
        {"state": "closed"},
        {"merged_at": datetime(2024, 1, 2, tzinfo=timezone.utc)},
        {"checks": {"tests": "success"}},
        {"pr_data": GithubPRData(reviews=[{"id": 1, "state": "approved"}])},
        {"labels": ["bug", "urgent"]},
        {"assignees": ["al"]},
        {"draft": True},
    ],
)
def test_content_version_counts_what_a_github_sync_reports(changed):
    assert content_version(pr()) == content_version(pr())
    assert content_version(pr()) != content_version(pr(**changed))


def test_content_version_ignores_label_and_assignee_order():
    assert content_version(pr(labels=["urgent", "bug"], assignees=["bo", "al"])) == content_version(
        pr(labels=["bug", "urgent"], assignees=["al", "bo"])
    )


@pytest.mark.parametrize("on_edit", [False, True])
@pytest.mark.parametrize(
    "delivery", [{"mode": "per_item"}, {"mode": "batch", "window_sec": 60, "max_items": 10}]
)
def test_reprocessing_a_matched_item_matches_nothing(
    db_session, admin_user, no_celery_dispatch, delivery, on_edit
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        delivery=delivery,
        on_edit=on_edit,
    )
    post = blog_post(db_session)

    first = route_item(db_session, post.id)
    again = route_item(db_session, post.id)

    assert (first, again) == ([trigger.id], [])
    assert db_session.query(AgentTriggerMatch).count() == 1


def edit(db, item):
    """Change the content only: not every ingester updates sha256 on an edit."""
    item.content = f"{item.content}, edited"
    db.commit()


@pytest.mark.parametrize("on_edit,runs", [(False, 1), (True, 2)])
def test_an_edit_fires_again_only_with_on_edit(
    db_session, admin_user, no_celery_dispatch, on_edit, runs
):
    make_trigger(db_session, make_definition(db_session, admin_user), on_edit=on_edit)
    post = blog_post(db_session)

    route_item(db_session, post.id)
    edit(db_session, post)
    route_item(db_session, post.id)
    route_item(db_session, post.id)

    assert no_celery_dispatch.call_count == runs
    assert db_session.query(AgentTriggerMatch).count() == runs


def test_on_changes_limits_which_edits_fire(
    db_session, admin_user, no_celery_dispatch
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=True, on_changes=["commits"]
    )
    post = blog_post(db_session)

    new = route_item(db_session, post.id, changes=["state"])
    edit(db_session, post)
    commented = route_item(db_session, post.id, changes=["comments"])
    unknown = route_item(db_session, post.id)
    pushed = route_item(db_session, post.id, changes=["comments", "commits"])

    assert (new, commented, unknown, pushed) == ([trigger.id], [], [], [trigger.id])
    assert sorted(m.changes for m in db_session.query(AgentTriggerMatch)) == [
        ["comments", "commits"],
        ["new", "state"],
    ]


def test_a_batch_holds_each_version_of_an_item_edited_while_waiting(
    db_session, admin_user, no_celery_dispatch
):
    trigger = make_trigger(
        db_session,
        make_definition(db_session, admin_user),
        delivery={"mode": "batch", "window_sec": 60, "max_items": 10},
        on_edit=True,
    )
    post = blog_post(db_session)

    first = route_item(db_session, post.id)
    edit(db_session, post)
    edited = route_item(db_session, post.id)

    assert (first, edited) == ([trigger.id], [trigger.id])
    assert sorted(m.edited for m in db_session.query(AgentTriggerMatch)) == [False, True]


@pytest.mark.parametrize("on_edit", [False, True])
def test_items_stored_before_the_trigger_was_active_never_match(
    db_session, admin_user, no_celery_dispatch, on_edit
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=on_edit
    )
    old = blog_post(db_session, inserted_at=trigger.active_since - timedelta(minutes=1))

    before = route_item(db_session, old.id)
    edit(db_session, old)

    assert (before, route_item(db_session, old.id)) == ([], [])


def test_an_edit_to_an_item_matched_long_ago_still_fires(
    db_session, admin_user, no_celery_dispatch
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

    unchanged = route_item(db_session, post.id)
    edit(db_session, post)

    assert (unchanged, route_item(db_session, post.id)) == ([], [trigger.id])


@pytest.mark.parametrize(
    "on_edit,kept",
    [
        (False, {b"fresh", b"edited new", b"pending"}),
        (True, {b"fresh", b"stale newest", b"edited new", b"pending"}),
    ],
)
def test_old_claims_are_purged_but_on_edit_keeps_each_items_newest_and_pending_stay(
    db_session, admin_user, on_edit, kept
):
    trigger = make_trigger(
        db_session, make_definition(db_session, admin_user), on_edit=on_edit
    )
    fresh, stale, edited, stuck = (blog_post(db_session) for _ in range(4))
    now = datetime.now(timezone.utc)
    claims = [
        (fresh, b"fresh", now, "done"),
        (stale, b"stale oldest", now - timedelta(days=40), "done"),
        (stale, b"stale newest", now - timedelta(days=31), "skipped:inaccessible"),
        (edited, b"edited old", now - timedelta(days=40), "done"),
        (edited, b"edited new", now, "done"),
        (stuck, b"pending", now - timedelta(days=40), "pending"),
    ]
    db_session.add_all(
        AgentTriggerMatch(
            trigger_id=trigger.id, item_id=item.id, content_hash=version, created_at=at, status=status
        )
        for item, version, at, status in claims
    )
    db_session.commit()

    purged = triggers.purge_matches(db_session, timedelta(days=30))

    assert purged == len(claims) - len(kept)
    assert {bytes(m.content_hash) for m in db_session.query(AgentTriggerMatch)} == kept


def test_items_older_than_claim_retention_never_match(
    db_session, admin_user, no_celery_dispatch
):
    trigger = make_trigger(db_session, make_definition(db_session, admin_user))
    trigger.active_since = datetime.now(timezone.utc) - timedelta(days=60)
    db_session.commit()
    old = blog_post(
        db_session,
        inserted_at=datetime.now(timezone.utc) - triggers.MATCH_RETENTION - timedelta(hours=1),
    )

    assert route_item(db_session, old.id) == []
