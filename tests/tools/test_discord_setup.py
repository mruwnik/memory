"""Tests for Discord setup CLI utilities."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from tools.discord_setup import generate_bot_invite_url, make_invite


def test_make_invite_generates_expected_url():
    result = make_invite(123456789)

    assert (
        result
        == "https://discord.com/oauth2/authorize?client_id=123456789&scope=bot&permissions=3088"
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
    assert isinstance(result.exception, ValueError)
    assert "Could not get bot info" in str(result.exception)
