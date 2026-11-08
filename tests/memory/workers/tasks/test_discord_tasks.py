"""Tests for Discord Celery tasks."""

from unittest.mock import patch

import pytest

from memory.agents import subscriptions
from memory.common.db.models import (
    AgentDefinition,
    Subscription,
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    Event,
    HumanUser,
    Person,
)
from memory.workers.tasks import discord
from tests.conftest import item_events


@pytest.fixture
def discord_bot(db_session):
    """Create a Discord bot for testing."""
    bot = DiscordBot(
        id=999999999,
        application_id=999999999,
        name="Test Bot",
        is_active=True,
    )
    db_session.add(bot)
    db_session.commit()
    return bot


@pytest.fixture
def discord_user(db_session):
    """Create a Discord user for testing."""
    user = DiscordUser(
        id=123456789,
        username="testuser",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def discord_server(db_session):
    """Create a Discord server for testing."""
    server = DiscordServer(
        id=987654321,
        name="Test Server",
        collect_messages=True,
    )
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def discord_channel(db_session, discord_server):
    """Create a Discord channel for testing."""
    channel = DiscordChannel(
        id=111222333,
        name="test-channel",
        channel_type="text",
        server_id=discord_server.id,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def sample_message_data(discord_bot, discord_user, discord_channel):
    """Sample message data for testing."""
    return {
        "bot_id": discord_bot.id,
        "message_id": 999888777,
        "channel_id": discord_channel.id,
        "author_id": discord_user.id,
        "content": "This is a test Discord message with enough content to be processed.",
        "sent_at": "2024-01-01T12:00:00Z",
    }


def test_add_discord_message_success(db_session, sample_message_data, qdrant):
    """Test successful Discord message addition."""
    result = discord.add_discord_message(**sample_message_data)

    assert result["status"] == "processed"
    assert "discordmessage_id" in result

    # Verify the message was created in the database
    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message is not None
    assert message.content == sample_message_data["content"]
    assert message.message_type == "default"
    assert message.reply_to_message_id is None
    assert message.bot_id == sample_message_data["bot_id"]
    assert message.author_id == sample_message_data["author_id"]


def test_add_discord_message_with_reply(db_session, sample_message_data, qdrant):
    """Test adding a Discord message that is a reply."""
    sample_message_data["reply_to_message_id"] = 111222333
    sample_message_data["message_type"] = "reply"

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.message_type == "reply"
    assert message.reply_to_message_id == 111222333


def test_add_discord_message_already_exists(db_session, sample_message_data, qdrant):
    """Test adding a message that already exists."""
    # Add the message once
    discord.add_discord_message(**sample_message_data)

    # Try to add it again
    result = discord.add_discord_message(**sample_message_data)

    assert result["status"] == "already_exists"
    assert result["message_id"] == sample_message_data["message_id"]

    # Verify no duplicate was created
    messages = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .all()
    )
    assert len(messages) == 1


def test_add_discord_message_with_reactions(db_session, sample_message_data, qdrant):
    """Test adding a Discord message with reactions."""
    sample_message_data["reactions"] = {"👍": ["1", "2"], "❤️": ["3"]}

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.reactions == {"👍": ["1", "2"], "❤️": ["3"]}


def test_add_discord_message_with_embeds(db_session, sample_message_data, qdrant):
    """Test adding a Discord message with embeds."""
    sample_message_data["embeds"] = [
        {"title": "Test Embed", "description": "This is a test embed"}
    ]

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.embeds is not None
    assert len(message.embeds) == 1
    assert message.embeds[0]["title"] == "Test Embed"


def test_add_discord_message_pinned(db_session, sample_message_data, qdrant):
    """Test adding a pinned Discord message."""
    sample_message_data["is_pinned"] = True

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.is_pinned is True


def test_add_discord_message_edit_existing(db_session, sample_message_data, qdrant):
    """Test editing an existing message via add_discord_message with is_edit=True."""
    # Add the message first
    discord.add_discord_message(**sample_message_data)

    # Edit it via add_discord_message with is_edit=True. Use long-enough
    # content so DiscordMessage.should_embed is True (it skips embedding for
    # messages shorter than 20 chars, returning "skipped" instead).
    sample_message_data["content"] = "Edited content with enough length to be embedded."
    sample_message_data["edited_at"] = "2024-01-01T13:00:00Z"
    sample_message_data["is_edit"] = True

    result = discord.add_discord_message(**sample_message_data)

    assert result["status"] == "processed"

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.content == "Edited content with enough length to be embedded."
    assert message.edited_at is not None


def test_edit_discord_message_success(db_session, sample_message_data, qdrant):
    """Test successful Discord message edit."""
    # First add the message
    discord.add_discord_message(**sample_message_data)

    # Edit it
    new_content = "This is the edited content with enough text to be meaningful and processed."
    edited_at = "2024-01-01T13:00:00Z"

    result = discord.edit_discord_message(
        sample_message_data["message_id"],
        new_content,
        edited_at,
    )

    assert result["status"] == "processed"

    # Verify the message was updated
    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.content == new_content
    assert message.edited_at is not None


EDITED = "This is the edited content with enough text to be meaningful and processed."


def edit_via_edit_task(data):
    return discord.edit_discord_message(data["message_id"], EDITED, "2024-01-01T13:00:00Z")


@pytest.mark.parametrize("backfill,fires", [(False, True), (True, False)])
def test_a_backfilled_message_is_stored_but_subscriptions_nothing(
    db_session, admin_user, sample_message_data, qdrant, no_celery_dispatch, backfill, fires
):
    definition = AgentDefinition(user_id=admin_user.id, name="t", spec={"system_prompt": "x"})
    db_session.add(definition)
    db_session.flush()
    subscription = Subscription(
        user_id=definition.user_id,
        name="t",
        definition_id=definition.id,
        handler={"agent": definition.id},
        kind="ingest",
        config=subscriptions.IngestConfig.model_validate(
            {"filter": {"source_types": ["discord_message"]}}
        ).model_dump(mode="json"),
    )
    db_session.add(subscription)
    db_session.commit()

    discord.add_discord_message(**sample_message_data, backfill=backfill)

    db_session.expire_all()
    assert [e.backfill for e in db_session.query(Event)] == [backfill]
    assert subscriptions.route_events() == ([subscription.id] if fires else [])


def edit_via_add_task(data):
    return discord.add_discord_message(
        **data | {"content": EDITED, "edited_at": "2024-01-01T13:00:00Z", "is_edit": True}
    )


@pytest.mark.parametrize("edit", [edit_via_edit_task, edit_via_add_task])
def test_an_edited_message_refires_an_on_edit_trigger(
    db_session, admin_user, sample_message_data, qdrant, no_celery_dispatch, edit
):
    definition = AgentDefinition(user_id=admin_user.id, name="t", spec={"system_prompt": "x"})
    db_session.add(definition)
    db_session.flush()
    subscription = Subscription(
        user_id=definition.user_id,
        name="t",
        definition_id=definition.id,
        handler={"agent": definition.id},
        kind="ingest",
        config=subscriptions.IngestConfig.model_validate(
            {"filter": {"source_types": ["discord_message"]}, "on_edit": True}
        ).model_dump(mode="json"),
    )
    db_session.add(subscription)
    db_session.commit()

    discord.add_discord_message(**sample_message_data)
    first = subscriptions.route_events()
    edit(sample_message_data)
    [(item_id, _, _), edited] = item_events(db_session)

    assert edited == (item_id, "changed", None)
    assert (first, subscriptions.route_events()) == ([subscription.id], [subscription.id])


def test_edit_discord_message_not_found(db_session):
    """Test editing a message that doesn't exist."""
    result = discord.edit_discord_message(
        999999,
        "New content",
        "2024-01-01T13:00:00Z",
    )

    assert result["status"] == "error"
    assert result["error"] == "Message not found"
    assert result["message_id"] == 999999


@pytest.mark.parametrize(
    "events, expected",
    [
        ([("add", "🎉", "1")], {"🎉": ["1"]}),
        ([("add", "🎉", "1"), ("add", "🎉", "1")], {"🎉": ["1"]}),
        ([("add", "🎉", "1"), ("add", "🎉", "2"), ("remove", "🎉", "1")], {"🎉": ["2"]}),
        ([("add", "🎉", "1"), ("add", "🔥", "2"), ("clear_emoji", "🎉", None)], {"🔥": ["2"]}),
        ([("add", "🎉", "1"), ("add", "🔥", "2"), ("clear", None, None)], {}),
        ([("remove", "🎉", "1")], {}),
    ],
)
def test_update_reactions_applies_events_per_user(
    db_session, sample_message_data, events, expected, qdrant
):
    discord.add_discord_message(**sample_message_data)

    for action, emoji, user_id in events:
        result = discord.update_reactions(
            sample_message_data["message_id"], action, emoji=emoji, user_id=user_id
        )
        assert result["status"] == "updated"

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .one()
    )
    assert message.reactions == expected


@pytest.mark.parametrize(
    "attempt, status, retried",
    [
        (0, "retrying", True),
        (discord.RETRIES - 1, "retrying", True),
        (discord.RETRIES, "not_found", False),
    ],
)
def test_update_reactions_waits_for_a_message_not_stored_yet(db_session, attempt, status, retried):
    with patch.object(discord.update_reactions, "apply_async") as retry:
        result = discord.update_reactions(999999, "add", emoji="👍", user_id="1", attempt=attempt)

    assert (result["status"], retry.called) == (status, retried)


def test_delete_discord_messages_removes_rows(db_session, sample_message_data, qdrant):
    discord.add_discord_message(**sample_message_data)
    discord.add_discord_message(**sample_message_data | {"message_id": 999888778})

    result = discord.delete_discord_messages([999888777, 999888778, 123])

    assert result == {"status": "deleted", "deleted": 2}
    assert db_session.query(DiscordMessage).count() == 0


def test_delete_discord_messages_is_idempotent(db_session, sample_message_data, qdrant):
    discord.add_discord_message(**sample_message_data)
    discord.delete_discord_messages([999888777])

    assert discord.delete_discord_messages([999888777]) == {"status": "deleted", "deleted": 0}


def test_add_discord_message_unique_hash(db_session, sample_message_data, qdrant):
    """Test that message hash includes message_id for uniqueness."""
    # Add first message
    discord.add_discord_message(**sample_message_data)

    # Try to add another message with same content but different message_id
    sample_message_data["message_id"] = 888777666

    result = discord.add_discord_message(**sample_message_data)

    # Should succeed because hash includes message_id
    assert result["status"] == "processed"

    # Verify both messages exist
    messages = (
        db_session.query(DiscordMessage)
        .filter_by(content=sample_message_data["content"])
        .all()
    )
    assert len(messages) == 2


@pytest.mark.parametrize(
    "sent_at_str,expected_hour",
    [
        ("2024-01-01T12:00:00Z", 12),
        ("2024-01-01T00:00:00+00:00", 0),
        ("2024-01-01T23:59:59Z", 23),
    ],
)
def test_add_discord_message_datetime_parsing(
    db_session, sample_message_data, sent_at_str, expected_hour, qdrant
):
    """Test that various datetime formats are parsed correctly."""
    sample_message_data["sent_at"] = sent_at_str

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.sent_at.hour == expected_hour


def test_add_discord_message_takes_its_channels_server(
    db_session, sample_message_data, discord_server, qdrant
):
    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.server_id == discord_server.id


def test_add_discord_message_with_thread(db_session, sample_message_data, qdrant):
    """Test adding a Discord message with a thread reference."""
    sample_message_data["thread_id"] = 555666777

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.thread_id == 555666777


@patch("memory.workers.tasks.discord.download_and_save_images")
def test_add_discord_message_with_images(
    mock_download, db_session, sample_message_data, qdrant
):
    """Test adding a Discord message with images."""
    mock_download.return_value = ["discord/999888777/image1.jpg"]

    sample_message_data["images"] = ["https://example.com/image1.jpg"]

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.images is not None
    assert "discord/999888777/image1.jpg" in message.images
    mock_download.assert_called_once()


@pytest.mark.parametrize(
    "username, display_name",
    [("alice_admin", None), ("mallory", "Alice Admin"), ("mallory", "alice_admin")],
)
def test_a_discord_name_never_attaches_the_author_to_someones_person(
    db_session, sample_message_data, discord_user, qdrant, username, display_name
):
    alice = HumanUser(email="alice@example.com", name="Alice Admin", password_hash="x")
    db_session.add(alice)
    db_session.flush()
    alices_person = Person(
        identifier="alice_admin", display_name="Alice Admin", aliases=["Alice Admin"], user_id=alice.id
    )
    db_session.add(alices_person)
    discord_user.username = username
    discord_user.display_name = display_name
    db_session.commit()

    discord.add_discord_message(**sample_message_data)

    db_session.expire_all()
    author = db_session.get(DiscordUser, discord_user.id)
    assert author.person_id is not None
    assert author.person_id != alices_person.id


@pytest.mark.parametrize(
    "named_by, attached",
    [
        ("123456789", True),
        (["old", "123456789"], True),
        (123456789, True),
        ([1, 123456789], True),
        ("testuser", False),
        (["old", "testuser"], False),
    ],
)
def test_the_author_attaches_only_to_the_person_whose_contact_info_has_their_id(
    db_session, sample_message_data, discord_user, qdrant, named_by, attached
):
    person = Person(identifier="tess", display_name="Tess", contact_info={"discord": named_by})
    db_session.add(person)
    db_session.commit()

    discord.add_discord_message(**sample_message_data)

    db_session.expire_all()
    assert (db_session.get(DiscordUser, discord_user.id).person_id == person.id) == attached


def test_discord_person_identifier_collision_does_not_claim_existing_person(db_session):
    from memory.common.db.models import Person, DiscordUser
    from memory.workers.tasks.discord import person_for_discord_user
    claimed = Person(identifier="discord_123456789", display_name="Someone else")
    db_session.add(claimed)
    db_session.flush()
    user = DiscordUser(id=123456789, username="real-user", display_name="Real User")
    person = person_for_discord_user(db_session, user)
    db_session.flush()
    assert person.id != claimed.id
    assert person.identifier.startswith("discord_123456789_")
    assert person.contact_info["discord"] == "123456789"


def test_an_unchanged_re_read_message_is_left_alone(db_session, sample_message_data, qdrant):
    discord.add_discord_message(**sample_message_data)

    with patch.object(discord, "process_content_item") as process:
        result = discord.add_discord_message(**sample_message_data, is_edit=True)

    assert result["status"] == "already_exists"
    process.assert_not_called()


def test_a_re_read_message_with_new_content_is_rewritten(db_session, sample_message_data, qdrant):
    discord.add_discord_message(**sample_message_data)
    edited = "a later edit of this message read back by the catch-up"

    discord.add_discord_message(**sample_message_data | {"content": edited}, is_edit=True)

    message = db_session.query(DiscordMessage).one()
    db_session.refresh(message)
    assert message.content == edited


def test_a_concurrent_duplicate_insert_is_reported_as_existing(db_session, sample_message_data, qdrant, caplog):
    """Two deliveries of one message racing past the existence check (with
    different content, e.g. an edit) collide on the unique message_id."""
    discord.add_discord_message(**sample_message_data)
    racing = sample_message_data | {"content": "the racing delivery's own text for this message"}

    with patch.object(discord, "find_message", return_value=None), caplog.at_level("INFO"):
        result = discord.add_discord_message(**racing)

    assert result["status"] == "already_exists"
    assert db_session.query(DiscordMessage).count() == 1
    assert racing["content"] not in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
