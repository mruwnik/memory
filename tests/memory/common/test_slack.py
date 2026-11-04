import logging

import httpx
import pytest

from memory.common import slack


def failing_client(error: str) -> slack.SlackClient:
    client = slack.SlackClient("xoxb-test")
    client._client = httpx.Client(
        base_url="https://slack.com/api/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": False, "error": error})
        ),
    )
    return client


@pytest.mark.parametrize(
    "error, level",
    [
        ("channel_not_found", logging.INFO),
        ("not_in_channel", logging.INFO),
        ("invalid_auth", logging.ERROR),
    ],
)
def test_call_logs_unreachable_channels_below_error(caplog, error, level):
    with caplog.at_level(logging.INFO, logger=slack.logger.name):
        with pytest.raises(slack.SlackAPIError, match=error):
            failing_client(error).call("conversations.history", channel="C123")

    assert [(r.levelno, r.exc_info) for r in caplog.records] == [(level, None)]
