import pytest
from unittest.mock import MagicMock

import discord

from memory.common.db.models import DiscordChannel, DiscordServer, DiscordUser
from memory.discord.commands import (
    CommandError,
    CommandResponse,
    run_command,
    handle_prompt,
    handle_chattiness,
    handle_ignore,
    handle_summary,
)


class DummyInteraction:
    """Lightweight stand-in for :class:`discord.Interaction` used in tests."""

    def __init__(
        self,
        *,
        guild: discord.Guild | None,
        channel: discord.abc.Messageable | None,
        user: discord.abc.User,
    ) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.guild_id = getattr(guild, "id", None)
        self.channel_id = getattr(channel, "id", None)


@pytest.fixture
def guild() -> discord.Guild:
    guild = MagicMock(spec=discord.Guild)
    guild.id = 123
    guild.name = "Test Guild"
    guild.description = "Guild description"
    guild.member_count = 42
    return guild


@pytest.fixture
def text_channel(guild: discord.Guild) -> discord.TextChannel:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 456
    channel.name = "general"
    channel.guild = guild
    channel.type = discord.ChannelType.text
    return channel


@pytest.fixture
def discord_user() -> discord.User:
    user = MagicMock(spec=discord.User)
    user.id = 789
    user.name = "command-user"
    user.display_name = "Commander"
    return user


@pytest.fixture
def interaction(guild, text_channel, discord_user) -> DummyInteraction:
    return DummyInteraction(guild=guild, channel=text_channel, user=discord_user)


def test_handle_command_prompt_server(db_session, guild, interaction):
    server = DiscordServer(id=guild.id, name="Test Guild", system_prompt="Be helpful")
    db_session.add(server)
    db_session.commit()

    response = run_command(
        db_session,
        interaction,
        scope="server",
        handler=handle_prompt,
    )

    assert isinstance(response, CommandResponse)
    assert "Be helpful" in response.content


def test_handle_command_prompt_channel_creates_channel(db_session, interaction, text_channel):
    response = run_command(
        db_session,
        interaction,
        scope="channel",
        handler=handle_prompt,
    )

    assert "No prompt" in response.content
    channel = db_session.get(DiscordChannel, text_channel.id)
    assert channel is not None
    assert channel.name == text_channel.name


def test_handle_command_chattiness_show(db_session, interaction, guild):
    server = DiscordServer(id=guild.id, name="Guild", chattiness_threshold=73)
    db_session.add(server)
    db_session.commit()

    response = run_command(
        db_session,
        interaction,
        scope="server",
        handler=handle_chattiness,
    )

    assert str(server.chattiness_threshold) in response.content


def test_handle_command_chattiness_update(db_session, interaction):
    user_model = DiscordUser(id=interaction.user.id, username="command-user", chattiness_threshold=15)
    db_session.add(user_model)
    db_session.commit()

    response = run_command(
        db_session,
        interaction,
        scope="user",
        handler=handle_chattiness,
        value=80,
    )

    db_session.flush()

    assert "Updated" in response.content
    assert user_model.chattiness_threshold == 80


def test_handle_command_chattiness_invalid_value(db_session, interaction):
    with pytest.raises(CommandError):
        run_command(
            db_session,
            interaction,
            scope="user",
            handler=handle_chattiness,
            value=150,
        )


def test_handle_command_ignore_toggle(db_session, interaction, guild):
    channel = DiscordChannel(id=interaction.channel.id, name="general", channel_type="text", server_id=guild.id)
    db_session.add(channel)
    db_session.commit()

    response = run_command(
        db_session,
        interaction,
        scope="channel",
        handler=handle_ignore,
        ignore_enabled=True,
    )

    db_session.flush()

    assert "no longer" not in response.content
    assert channel.ignore_messages is True


def test_handle_command_summary_missing(db_session, interaction):
    response = run_command(
        db_session,
        interaction,
        scope="user",
        handler=handle_summary,
    )

    assert "No summary" in response.content

