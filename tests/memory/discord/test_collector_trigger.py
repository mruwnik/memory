from unittest.mock import MagicMock

import pytest

from memory.common.celery_app import BACKFILL_DISCORD_CHANNEL
from memory.common.db.models import DiscordServer
from memory.discord.collector import BotInfo, MessageCollector


def fake_guild_channel(channel_id=10, guild_id=1):
    ch = MagicMock()
    ch.id = channel_id
    ch.name = "general"
    ch.guild = MagicMock(id=guild_id)
    ch.category_id = None
    ch.type = None  # get_channel_type -> "unknown" (string), avoids MagicMock->Text crash
    ch.send = MagicMock()  # marks it as messageable (hasattr "send")
    return ch


@pytest.mark.asyncio
async def test_new_collectible_channel_triggers_backfill(db_session):
    db_session.add(DiscordServer(id=1, name="s", collect_messages=True))
    db_session.commit()

    celery_app = MagicMock()
    collector = MessageCollector(BotInfo(id=42, name="bot", token="t"), celery_app)

    await collector.on_guild_channel_create(fake_guild_channel())

    calls = [c for c in celery_app.send_task.call_args_list
             if c.args and c.args[0] == BACKFILL_DISCORD_CHANNEL]
    assert len(calls) == 1
    assert calls[0].kwargs["args"] == [10]


@pytest.mark.asyncio
async def test_new_non_collectible_channel_does_not_trigger_backfill(db_session):
    db_session.add(DiscordServer(id=1, name="s", collect_messages=False))
    db_session.commit()

    celery_app = MagicMock()
    collector = MessageCollector(BotInfo(id=42, name="bot", token="t"), celery_app)

    await collector.on_guild_channel_create(fake_guild_channel())

    calls = [c for c in celery_app.send_task.call_args_list
             if c.args and c.args[0] == BACKFILL_DISCORD_CHANNEL]
    assert calls == []
