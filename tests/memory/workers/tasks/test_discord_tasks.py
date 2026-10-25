"""Tests for Discord Celery tasks."""

from unittest.mock import patch

import fakeredis
import pytest

from memory.agents import triggers
from memory.common.celery_app import MATCH_INGEST_TRIGGERS
from memory.common.db.models import (
    AgentDefinition,
    AgentTrigger,
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    HumanUser,
    Person,
)
from memory.workers.tasks import discord


@pytest.fixture
def discord_bot(db_session):
    """Create a Discord bot for testing."""
    bot = DiscordBot(
        id=999999999,
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
        "server_id": None,
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
    sample_message_data["reactions"] = [
        {"emoji": "👍", "count": 5},
        {"emoji": "❤️", "count": 3},
    ]

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.reactions is not None
    assert len(message.reactions) == 2
    assert message.reactions[0]["emoji"] == "👍"


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
    trigger = AgentTrigger(
        definition_id=definition.id,
        kind="ingest",
        config=triggers.IngestConfig.model_validate(
            {"filter": {"source_types": ["discord_message"]}, "on_edit": True}
        ).model_dump(mode="json"),
    )
    db_session.add(trigger)
    db_session.commit()
    client = fakeredis.FakeRedis()

    discord.add_discord_message(**sample_message_data)
    [stored] = no_celery_dispatch.call_args_list
    [item_id] = stored.kwargs["args"]
    first = triggers.match_ingest_triggers(item_id, client)
    no_celery_dispatch.reset_mock()
    edit(sample_message_data)
    queued = no_celery_dispatch.call_args_list

    assert [(c.args[0], c.kwargs["args"]) for c in queued] == [(MATCH_INGEST_TRIGGERS, [item_id])]
    assert (first, triggers.match_ingest_triggers(item_id, client)) == ([trigger.id], [trigger.id])


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


def test_update_reactions_success(db_session, sample_message_data, qdrant):
    """Test updating reactions on a Discord message."""
    # First add the message
    discord.add_discord_message(**sample_message_data)

    # Update reactions
    reactions = [
        {"emoji": "🎉", "count": 10},
        {"emoji": "🔥", "count": 5},
    ]

    result = discord.update_reactions(
        sample_message_data["message_id"],
        reactions,
    )

    assert result["status"] == "updated"

    # Verify the reactions were updated
    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.reactions == reactions


def test_update_reactions_not_found(db_session):
    """Test updating reactions on a message that doesn't exist."""
    result = discord.update_reactions(
        999999,
        [{"emoji": "👍", "count": 1}],
    )

    assert result["status"] == "error"
    assert result["error"] == "Message not found"
    assert result["message_id"] == 999999


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


def test_add_discord_message_with_server(
    db_session, sample_message_data, discord_server, qdrant
):
    """Test adding a Discord message with a server reference."""
    sample_message_data["server_id"] = discord_server.id

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


@pytest.mark.parametrize("named_by", ["123456789", "testuser", ["old", "testuser"]])
def test_the_author_attaches_to_the_person_whose_contact_info_names_them(
    db_session, sample_message_data, discord_user, qdrant, named_by
):
    person = Person(identifier="tess", display_name="Tess", contact_info={"discord": named_by})
    db_session.add(person)
    db_session.commit()

    discord.add_discord_message(**sample_message_data)

    db_session.expire_all()
    assert db_session.get(DiscordUser, discord_user.id).person_id == person.id
