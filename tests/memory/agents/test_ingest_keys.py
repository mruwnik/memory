from datetime import datetime, timezone

import pytest

from memory.agents.provider_keys import ingest_key, personal_key
from memory.agents.system import SUMMARISE
from memory.common.db.models import (
    ArticleFeed,
    BlogPost,
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    LLMProviderKey,
    Note,
    SlackApp,
    SlackChannel,
    SlackMessage,
    SlackUserCredentials,
    SlackWorkspace,
)
from tests.conftest import unique_sha256


def key(db, user, label, provider="anthropic", is_default=False, status="ok"):
    row = LLMProviderKey(
        user_id=user.id, provider=provider, label=label, is_default=is_default, status=status
    )
    row.key = f"sk-{label}"
    db.add(row)
    db.commit()
    return row


def blog_post(db, feed):
    post = BlogPost(modality="blog", sha256=unique_sha256("post"), feed_id=feed.id, content="x")
    db.add(post)
    db.commit()
    return post


def feed_of(db, user, **choice):
    feed = ArticleFeed(url=f"https://example.com/{unique_sha256().hex()}", user_id=user.id, **choice)
    db.add(feed)
    db.commit()
    return feed


def test_the_owners_default_key_pays_for_items_from_a_source_left_on_default(db_session, regular_user):
    default = key(db_session, regular_user, "default", is_default=True)
    post = blog_post(db_session, feed_of(db_session, regular_user))
    post.creator_id = regular_user.id

    assert ingest_key(db_session, post, SUMMARISE) == default


def test_a_sources_explicit_key_wins_over_the_default(db_session, regular_user):
    key(db_session, regular_user, "default", is_default=True)
    explicit = key(db_session, regular_user, "work")
    post = blog_post(db_session, feed_of(db_session, regular_user, llm_key_id=explicit.id))
    post.creator_id = regular_user.id

    assert ingest_key(db_session, post, SUMMARISE) == explicit


def test_a_source_cannot_spend_someone_elses_key(db_session, regular_user, admin_user):
    default = key(db_session, regular_user, "default", is_default=True)
    theirs = key(db_session, admin_user, "theirs")
    post = blog_post(db_session, feed_of(db_session, regular_user, llm_key_id=theirs.id))
    post.creator_id = regular_user.id

    assert ingest_key(db_session, post, SUMMARISE) == default


@pytest.mark.parametrize("choice", ["disabled", "other_provider", "invalid"])
def test_no_key_pays_when_the_source_says_so_or_its_key_cannot(db_session, regular_user, choice):
    key(db_session, regular_user, "default", is_default=True)
    keys = {
        "other_provider": key(db_session, regular_user, "gpt", provider="openai"),
        "invalid": key(db_session, regular_user, "bad", status="invalid"),
    }
    settings = {
        "disabled": {"llm_disabled": True},
        "other_provider": {"llm_key_id": keys["other_provider"].id},
        "invalid": {"llm_key_id": keys["invalid"].id},
    }[choice]
    post = blog_post(db_session, feed_of(db_session, regular_user, **settings))
    post.creator_id = regular_user.id

    assert ingest_key(db_session, post, SUMMARISE) is None


def note_by(db, user):
    note = Note(modality="note", sha256=unique_sha256("note"), content="x", creator_id=user.id)
    db.add(note)
    db.commit()
    return note


def test_items_without_a_source_use_their_creators_default(db_session, regular_user):
    default = key(db_session, regular_user, "default", is_default=True)

    assert ingest_key(db_session, note_by(db_session, regular_user), SUMMARISE) == default


def test_a_rejected_default_pays_for_nothing(db_session, regular_user):
    key(db_session, regular_user, "default", is_default=True, status="invalid")

    assert ingest_key(db_session, note_by(db_session, regular_user), SUMMARISE) is None


def test_items_nobody_brought_in_get_no_llm_work(db_session):
    note = Note(modality="note", sha256=unique_sha256("note"), content="x")
    db_session.add(note)
    db_session.commit()

    assert ingest_key(db_session, note, SUMMARISE) is None


def test_slack_messages_use_the_app_registrants_default(db_session, regular_user, admin_user):
    default = key(db_session, regular_user, "default", is_default=True)
    workspace = SlackWorkspace(id="T1", name="w")
    older = SlackApp(client_id="a", name="first", created_by_user_id=regular_user.id)
    newer = SlackApp(client_id="b", name="second", created_by_user_id=admin_user.id)
    db_session.add_all([workspace, older, newer])
    db_session.flush()
    for app, user in ((older, regular_user), (newer, admin_user)):
        cred = SlackUserCredentials(
            slack_app_id=app.id, workspace_id="T1", user_id=user.id, scopes=[], slack_user_id=f"U{user.id}"
        )
        cred.access_token = "xoxp"
        db_session.add(cred)
    channel = SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel")
    message = SlackMessage(
        modality="message",
        sha256=unique_sha256("slack"),
        message_ts="1.0",
        channel_id="C1",
        workspace_id="T1",
        content="x",
    )
    db_session.add_all([channel, message])
    db_session.commit()

    assert ingest_key(db_session, message, SUMMARISE) == default


def discord_message(db, channel_id, server_id=None):
    message = DiscordMessage(
        modality="message",
        sha256=unique_sha256("discord"),
        message_id=channel_id * 10,
        channel_id=channel_id,
        server_id=server_id,
        author_id=9,
        bot_id=1,
        sent_at=datetime.now(timezone.utc),
        content="x",
    )
    db.add(message)
    db.commit()
    return message


def discord_bot_of(db, user):
    db.add_all(
        [DiscordBot(id=1, name="bot", created_by_user_id=user.id), DiscordUser(id=9, username="u")]
    )
    db.commit()


def test_discord_server_messages_are_paid_for_by_the_bots_registrant(db_session, regular_user):
    default = key(db_session, regular_user, "default", is_default=True)
    discord_bot_of(db_session, regular_user)
    db_session.add(DiscordServer(id=2, name="s", bot_id=1))
    db_session.flush()
    db_session.add(DiscordChannel(id=3, server_id=2, name="general", channel_type="text"))
    db_session.commit()

    assert ingest_key(db_session, discord_message(db_session, 3, server_id=2), SUMMARISE) == default


@pytest.mark.parametrize("channel_type", ["dm", "group_dm"])
def test_discord_dms_are_paid_for_by_the_bots_registrant(db_session, regular_user, channel_type):
    default = key(db_session, regular_user, "default", is_default=True)
    discord_bot_of(db_session, regular_user)
    db_session.add(DiscordChannel(id=4, name="dm", channel_type=channel_type))
    db_session.commit()

    assert ingest_key(db_session, discord_message(db_session, 4), SUMMARISE) == default


def test_searches_use_the_searchers_default_for_the_agents_provider(db_session, regular_user):
    key(db_session, regular_user, "gpt", provider="openai", is_default=True)

    assert personal_key(db_session, regular_user.id, SUMMARISE) is None
