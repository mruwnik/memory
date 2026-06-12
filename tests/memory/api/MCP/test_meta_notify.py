# pyright: reportFunctionMemberAccess=false
from unittest.mock import patch

import pytest

from memory.api.MCP.servers import meta
from memory.api.MCP.servers.meta import (
    format_notification_message,
    get_notification_channel,
)
from memory.common.db.models import ScheduledTask
from tests.conftest import mcp_auth_context


def test_get_notification_channel_coerces_int_discord_id():
    # User.serialize() keys discord_accounts by int snowflake; the identifier
    # must be coerced to str so it survives the celery JSON boundary as the
    # str the downstream .isdigit() classification expects.
    user_info = {
        "user": {
            "discord_accounts": {123456789: "someuser"},
            "discord_bots": [7],
        }
    }
    channel = get_notification_channel(user_info, None)
    assert channel is not None
    ch_type, ch_id, extra = channel
    assert ch_type == "discord"
    assert ch_id == "123456789"
    assert isinstance(ch_id, str)
    assert extra == {"discord_bot_id": 7}


def test_format_notification_message_with_url():
    out = format_notification_message("Subject", "Body", "https://x/y")
    assert out == "**Subject**\n\nBody\n\n[View details](https://x/y)"


def test_format_notification_message_no_url():
    out = format_notification_message("Subject", "Body", None)
    assert out == "**Subject**\n\nBody"


@patch("memory.api.MCP.servers.meta.get_notification_channel")
@patch("memory.api.MCP.servers.meta.celery_app.send_task")
@pytest.mark.asyncio
async def test_notify_user_immediate_creates_no_rows(
    mock_send, mock_channel, db_session, regular_user, user_session
):
    mock_channel.return_value = ("discord", "123456789", {"discord_bot_id": 7})

    with mcp_auth_context(user_session.id):
        result = await meta.notify_user.fn(subject="Hi", message="body")

    assert result["scheduled"] is False
    # Immediate sends report "queued", not "success": delivery isn't confirmed.
    assert result["queued"] is True
    assert "success" not in result
    assert (
        db_session.query(ScheduledTask)
        .filter(ScheduledTask.user_id == regular_user.id)
        .count()
        == 0
    )
    assert mock_send.called
    assert mock_send.call_args.args[0].endswith(".send_notification")
    # Pin the positional payload so a reorder of either the send_task call or
    # the send_notification(channel, target, message, user_id, topic, data)
    # signature is caught (subject->topic, channel extra_data->data).
    assert mock_send.call_args.kwargs["args"] == [
        "discord",
        "123456789",
        "**Hi**\n\nbody",
        regular_user.id,
        "Hi",
        {"discord_bot_id": 7},
    ]
