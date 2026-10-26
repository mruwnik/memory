"""Tests for Discord setup CLI utilities."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from tools.discord_setup import generate_bot_invite_url, make_invite


def test_make_invite_generates_expected_url():
    result = make_invite(123456789)

    # Permissions: VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS | ATTACH_FILES |
    #              READ_MESSAGE_HISTORY | ADD_REACTIONS | MANAGE_CHANNELS | MANAGE_ROLES
    # = 1024 + 2048 + 16384 + 32768 + 65536 + 64 + 16 + 268435456 = 268553296
    assert (
        result
        == "https://discord.com/oauth2/authorize?client_id=123456789&scope=bot&permissions=268553296"
    )


@patch("tools.discord_setup.requests.get")
def test_generate_bot_invite_url_outputs_link(mock_get):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"id": "987654321"}
    mock_get.return_value = response

    runner = CliRunner()
    result = runner.invoke(generate_bot_invite_url, ["--bot-token", "abc.def"])

    assert result.exit_code == 0
    assert "Bot invite URL" in result.output
    assert "987654321" in result.output


@patch("tools.discord_setup.requests.get", side_effect=Exception("api down"))
def test_generate_bot_invite_url_handles_errors(mock_get):
    runner = CliRunner()
    result = runner.invoke(generate_bot_invite_url, ["--bot-token", "token"])

    assert result.exit_code != 0
    assert "Could not get bot info" in result.output


def test_cli_assigns_and_lists_bot_owner(db_session, regular_user, monkeypatch):
    from contextlib import nullcontext
    from memory.common.db import connection
    from memory.common.db.models import DiscordBot
    from tools import discord_setup

    monkeypatch.setattr(connection, "make_session", lambda: nullcontext(db_session))
    monkeypatch.setattr(discord_setup, "fetch_bot_info", lambda _: {"id": "123456789", "username": "Test bot"})
    runner = CliRunner()
    result = runner.invoke(discord_setup.cli, ["add-bot", "--bot-token", "test-token", "--user-email", regular_user.email])
    assert result.exit_code == 0, result.output
    bot = db_session.get(DiscordBot, 123456789)
    assert bot is not None and bot.created_by_user_id == regular_user.id
    result = runner.invoke(discord_setup.cli, ["list-bots"])
    assert result.exit_code == 0, result.output
    assert f"Owner: {regular_user.email}" in result.output


def test_cli_set_owner_reassigns_bot(db_session, admin_user, regular_user, monkeypatch):
    from contextlib import nullcontext
    from memory.common.db import connection
    from memory.common.db.models import DiscordBot
    from tools.discord_setup import cli

    bot = DiscordBot(id=987654321, name="Test bot", created_by_user_id=admin_user.id)
    db_session.add(bot)
    db_session.commit()
    monkeypatch.setattr(connection, "make_session", lambda: nullcontext(db_session))
    result = CliRunner().invoke(cli, ["set-owner", "--bot-id", str(bot.id), "--user-email", regular_user.email])
    assert result.exit_code == 0, result.output
    db_session.refresh(bot)
    assert bot.created_by_user_id == regular_user.id
