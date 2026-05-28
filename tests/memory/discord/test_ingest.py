from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from discord import MessageType

from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    DiscordUser,
)
from memory.discord.ingest import (
    build_message_task_kwargs,
    ensure_message_entities,
    get_message_type,
)


def fake_message(**overrides):
    """Build a MagicMock that quacks like a discord.py Message."""
    msg = MagicMock()
    msg.id = 111
    msg.guild = MagicMock(id=222)
    msg.channel = MagicMock(id=333)
    msg.author = MagicMock(id=444)
    msg.content = "hello world"
    msg.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    msg.edited_at = None
    msg.reference = None
    msg.thread = None
    msg.type = MessageType.default
    msg.pinned = False
    msg.attachments = []
    msg.embeds = []
    for key, value in overrides.items():
        setattr(msg, key, value)
    return msg


def test_build_message_task_kwargs_plain_message():
    kwargs = build_message_task_kwargs(fake_message(), bot_id=42)
    assert kwargs == {
        "bot_id": 42,
        "message_id": 111,
        "channel_id": 333,
        "server_id": 222,
        "author_id": 444,
        "content": "hello world",
        "sent_at": "2024-01-01T00:00:00+00:00",
        "edited_at": None,
        "reply_to_message_id": None,
        "thread_id": None,
        "message_type": "default",
        "is_pinned": False,
        "images": None,
        "embeds": None,
        "attachments": None,
        "is_edit": False,
    }


def test_build_message_task_kwargs_no_guild_means_null_server():
    kwargs = build_message_task_kwargs(fake_message(guild=None), bot_id=42)
    assert kwargs["server_id"] is None


def test_build_message_task_kwargs_splits_image_and_other_attachments():
    image = MagicMock(url="http://x/i.png", content_type="image/png",
                      filename="i.png", size=10)
    doc = MagicMock(url="http://x/d.pdf", content_type="application/pdf",
                    filename="d.pdf", size=20)
    kwargs = build_message_task_kwargs(
        fake_message(attachments=[image, doc]), bot_id=42
    )
    assert kwargs["images"] == ["http://x/i.png"]
    assert kwargs["attachments"] == [
        {"filename": "d.pdf", "content_type": "application/pdf",
         "size": 20, "url": "http://x/d.pdf"}
    ]


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"reference": MagicMock()}, "reply"),
        ({"thread": MagicMock(), "reference": None}, "thread_starter"),
        ({"type": MessageType.pins_add, "reference": None, "thread": None}, "system"),
        ({"reference": None, "thread": None}, "default"),
    ],
)
def test_get_message_type(overrides, expected):
    assert get_message_type(fake_message(**overrides)) == expected


def fake_db_message():
    """Build a fake discord.py Message with concrete (non-Mock) entity fields.

    ``ensure_channel``/``ensure_server``/``ensure_user`` write these into real
    DB columns, so the string/int attributes must be plain values rather than
    MagicMocks (which would fail psycopg2 adaptation).
    """
    # NOTE: ``name`` is a reserved MagicMock constructor kwarg (sets the mock's
    # repr, not a ``.name`` attribute), so assign it explicitly afterwards.
    channel = MagicMock(id=333, category_id=None)
    channel.name = "general"
    channel.type = MagicMock()
    channel.type.name = "text"

    guild = MagicMock(id=222, description=None, member_count=10)
    guild.name = "Test Server"

    author = MagicMock(id=444, display_name="Alice")
    author.name = "alice"

    return fake_message(guild=guild, channel=channel, author=author)


def test_ensure_message_entities_creates_rows_and_returns_channel(db_session):
    # DiscordServer.bot_id is a FK to discord_bots.id, so the bot row must exist.
    db_session.add(DiscordBot(id=42, name="Test Bot"))
    db_session.flush()

    message = fake_db_message()

    channel_model = ensure_message_entities(db_session, message, bot_id=42)
    db_session.commit()

    assert isinstance(channel_model, DiscordChannel)
    assert channel_model.id == 333

    server = db_session.get(DiscordServer, 222)
    assert server is not None
    assert server.bot_id == 42

    user = db_session.get(DiscordUser, 444)
    assert user is not None
    assert user.username == "alice"
