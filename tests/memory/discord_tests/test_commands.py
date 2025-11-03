from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import discord

from memory.common.db.models import DiscordChannel, DiscordServer, DiscordUser
from memory.discord.commands import (
    CommandContext,
    CommandError,
    CommandResponse,
    handle_prompt,
    handle_chattiness,
    handle_ignore,
    handle_summary,
    respond,
    with_object_context,
    handle_mcp_servers,
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


@pytest.mark.asyncio
async def test_handle_command_prompt_server(db_session, guild, interaction):
    server = DiscordServer(id=guild.id, name="Test Guild", system_prompt="Be helpful")
    db_session.add(server)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=MagicMock(spec=DiscordUser),
        scope="server",
        target=server,
        display_name="server **Test Guild**",
    )

    response = await handle_prompt(context)

    assert isinstance(response, CommandResponse)
    assert "Be helpful" in response.content


@pytest.mark.asyncio
async def test_handle_command_prompt_channel_creates_channel(
    db_session, interaction, text_channel, guild
):
    # Create the server first to satisfy FK constraint
    server = DiscordServer(id=guild.id, name="Test Guild")
    db_session.add(server)

    channel_model = DiscordChannel(
        id=text_channel.id,
        name=text_channel.name,
        channel_type="text",
        server_id=guild.id,
    )
    db_session.add(channel_model)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=MagicMock(spec=DiscordUser),
        scope="channel",
        target=channel_model,
        display_name=f"channel **#{text_channel.name}**",
    )

    response = await handle_prompt(context)

    assert "No prompt" in response.content
    channel = db_session.get(DiscordChannel, text_channel.id)
    assert channel is not None
    assert channel.name == text_channel.name


@pytest.mark.asyncio
async def test_handle_command_chattiness_show(db_session, interaction, guild):
    server = DiscordServer(id=guild.id, name="Guild", chattiness_threshold=73)
    db_session.add(server)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=MagicMock(spec=DiscordUser),
        scope="server",
        target=server,
        display_name="server **Guild**",
    )

    response = await handle_chattiness(context, value=None)

    assert str(server.chattiness_threshold) in response.content


@pytest.mark.asyncio
async def test_handle_command_chattiness_update(db_session, interaction):
    user_model = DiscordUser(
        id=interaction.user.id, username="command-user", chattiness_threshold=15
    )
    db_session.add(user_model)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=user_model,
        scope="user",
        target=user_model,
        display_name="user **command-user**",
    )

    response = await handle_chattiness(context, value=80)

    db_session.flush()

    assert "Updated" in response.content
    assert user_model.chattiness_threshold == 80


@pytest.mark.asyncio
async def test_handle_command_chattiness_invalid_value(db_session, interaction):
    user_model = DiscordUser(id=interaction.user.id, username="command-user")
    db_session.add(user_model)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=user_model,
        scope="user",
        target=user_model,
        display_name="user **command-user**",
    )

    with pytest.raises(CommandError):
        await handle_chattiness(context, value=150)


@pytest.mark.asyncio
async def test_handle_command_ignore_toggle(db_session, interaction, guild):
    # Create the server first to satisfy FK constraint
    server = DiscordServer(id=guild.id, name="Test Guild")
    db_session.add(server)

    channel = DiscordChannel(
        id=interaction.channel.id,
        name="general",
        channel_type="text",
        server_id=guild.id,
    )
    db_session.add(channel)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=MagicMock(spec=DiscordUser),
        scope="channel",
        target=channel,
        display_name="channel **#general**",
    )

    response = await handle_ignore(context, ignore_enabled=True)

    db_session.flush()

    assert "no longer" not in response.content
    assert channel.ignore_messages is True


@pytest.mark.asyncio
async def test_handle_command_summary_missing(db_session, interaction):
    user_model = DiscordUser(id=interaction.user.id, username="command-user")
    db_session.add(user_model)
    db_session.commit()

    context = CommandContext(
        session=db_session,
        interaction=interaction,
        actor=user_model,
        scope="user",
        target=user_model,
        display_name="user **command-user**",
    )

    response = await handle_summary(context)

    assert "No summary" in response.content


@pytest.mark.asyncio
async def test_respond_sends_message_without_file():
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()

    await respond(interaction, "hello world", ephemeral=False)

    interaction.response.send_message.assert_awaited_once_with(
        "hello world", ephemeral=False
    )


@pytest.mark.asyncio
async def test_respond_sends_file_when_content_too_large():
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()

    oversized = "x" * 2000
    with patch("memory.discord.commands.discord.File") as mock_file:
        file_instance = MagicMock()
        mock_file.return_value = file_instance

        await respond(interaction, oversized)

    interaction.response.send_message.assert_awaited_once_with(
        "Response too large, sending as file:",
        file=file_instance,
        ephemeral=True,
    )


@patch("memory.discord.commands._ensure_channel")
@patch("memory.discord.commands.ensure_server")
@patch("memory.discord.commands.ensure_user")
@patch("memory.discord.commands.make_session")
def test_with_object_context_uses_ensured_objects(
    mock_make_session,
    mock_ensure_user,
    mock_ensure_server,
    mock_ensure_channel,
    interaction,
    guild,
    text_channel,
    discord_user,
):
    mock_session = MagicMock()

    @contextmanager
    def session_cm():
        yield mock_session

    mock_make_session.return_value = session_cm()

    bot_model = MagicMock(name="bot_model")
    user_model = MagicMock(name="user_model")
    server_model = MagicMock(name="server_model")
    channel_model = MagicMock(name="channel_model")

    mock_ensure_user.side_effect = [bot_model, user_model]
    mock_ensure_server.return_value = server_model
    mock_ensure_channel.return_value = channel_model

    handler_objects = {}

    def handler(objects):
        handler_objects["objects"] = objects
        return "done"

    bot_client = SimpleNamespace(user=MagicMock())
    override_user = MagicMock(spec=discord.User)

    result = with_object_context(bot_client, interaction, handler, override_user)

    assert result == "done"
    objects = handler_objects["objects"]
    assert objects.bot is bot_model
    assert objects.server is server_model
    assert objects.channel is channel_model
    assert objects.user is user_model

    mock_ensure_user.assert_any_call(mock_session, bot_client.user)
    mock_ensure_user.assert_any_call(mock_session, override_user)
    mock_ensure_server.assert_called_once_with(mock_session, guild)
    mock_ensure_channel.assert_called_once_with(
        mock_session, text_channel, guild.id
    )


@pytest.mark.asyncio
@patch("memory.discord.commands.run_mcp_server_command", new_callable=AsyncMock)
async def test_handle_mcp_servers_returns_response(mock_run_mcp, interaction):
    mock_run_mcp.return_value = "Listed servers"
    server_model = DiscordServer(id=interaction.guild.id, name="Guild")

    context = CommandContext(
        session=MagicMock(),
        interaction=interaction,
        actor=MagicMock(spec=DiscordUser),
        scope="server",
        target=server_model,
        display_name="server **Guild**",
    )
    interaction.client = SimpleNamespace(user=MagicMock(spec=discord.User))

    response = await handle_mcp_servers(
        context, action="list", url=None
    )

    assert response.content == "Listed servers"
    mock_run_mcp.assert_awaited_once_with(
        interaction.client.user, "list", None, "DiscordServer", server_model.id
    )


@pytest.mark.asyncio
@patch("memory.discord.commands.run_mcp_server_command", new_callable=AsyncMock)
async def test_handle_mcp_servers_wraps_errors(mock_run_mcp, interaction):
    mock_run_mcp.side_effect = RuntimeError("boom")
    server_model = DiscordServer(id=interaction.guild.id, name="Guild")

    context = CommandContext(
        session=MagicMock(),
        interaction=interaction,
        actor=MagicMock(spec=DiscordUser),
        scope="server",
        target=server_model,
        display_name="server **Guild**",
    )
    interaction.client = SimpleNamespace(user=MagicMock(spec=discord.User))

    with pytest.raises(CommandError) as exc:
        await handle_mcp_servers(context, action="list", url=None)

    assert "Error: boom" in str(exc.value)
