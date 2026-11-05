import logging
from unittest.mock import patch

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


def recording_client(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["authorization"]))
        return httpx.Response(200, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, "https://slack.com/api/auth.test"),
        ({"base_url": "https://chat.example.com/api"}, "https://chat.example.com/api/auth.test"),
        ({"base_url": "https://chat.example.com/api/"}, "https://chat.example.com/api/auth.test"),
    ],
)
def test_client_calls_the_configured_api_and_checks_addresses(kwargs, expected):
    seen: list = []
    with patch.object(slack, "public_sync_http_client", return_value=recording_client(seen)) as safe:
        with slack.SlackClient("xoxp-1", **kwargs) as client:
            client.call("auth.test")

    assert safe.called
    assert seen == [(expected, "Bearer xoxp-1")]


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://slack.com/api", "https://slack.com/api/chat.postMessage"),
        ("https://chat.example.com/api", "https://chat.example.com/api/chat.postMessage"),
    ],
)
@pytest.mark.asyncio
async def test_async_call_uses_the_base_url(base_url, expected):
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    with patch.object(
        slack,
        "public_http_client",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ):
        await slack.async_slack_call("xoxp-1", "chat.postMessage", base_url=base_url, channel="C1")

    assert seen == [expected]


def rate_limited_client(retry_after: str) -> slack.SlackClient:
    client = slack.SlackClient("xoxb-test")
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Retry-After": retry_after}, json={"ok": False, "error": "ratelimited"}
            )
        ),
    )
    return client


@pytest.mark.parametrize("retry_after", ["3600", "31"])
def test_a_long_retry_after_gives_up_instead_of_waiting(retry_after):
    with patch.object(slack.time, "sleep") as sleep, pytest.raises(slack.SlackAPIError, match="ratelimited"):
        rate_limited_client(retry_after).call("conversations.history", channel="C123")

    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_async_call_gives_up_on_a_long_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Retry-After": "3600"}, json={"ok": False, "error": "ratelimited"})

    with (
        patch.object(slack, "public_http_client", return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))),
        patch.object(slack.asyncio, "sleep") as sleep,
        pytest.raises(slack.SlackAPIError, match="ratelimited"),
    ):
        await slack.async_slack_call("xoxp-1", "chat.postMessage", channel="C1")

    sleep.assert_not_called()
