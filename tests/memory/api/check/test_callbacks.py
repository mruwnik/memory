import ipaddress
from typing import cast

import httpcore
import httpx
import pytest

from memory.common.check import callbacks
from memory.common.check.schemas import JobRecord
from memory.common.safe_http import PinnedBackend

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("allow_private", [False, True])
@pytest.mark.parametrize("address", [
    "169.254.169.254",
    "127.0.0.1",
    "::1",
    "172.17.0.2",
])
async def test_internal_addresses_refused(address, allow_private, monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_ALLOW_PRIVATE_CALLBACKS", allow_private)
    assert callbacks.callback_policy().blocked(ipaddress.ip_address(address), frozenset())


@pytest.mark.parametrize("allow_private, blocked", [(False, True), (True, False)])
@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.20"])
async def test_private_ranges_only_with_flag(address, allow_private, blocked, monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_ALLOW_PRIVATE_CALLBACKS", allow_private)
    assert callbacks.callback_policy().blocked(ipaddress.ip_address(address), frozenset()) is blocked


async def test_public_address_allowed(monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_ALLOW_PRIVATE_CALLBACKS", False)
    policy = callbacks.callback_policy()
    assert not policy.blocked(ipaddress.ip_address("93.184.216.34"), frozenset())


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.dials = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dials.append((host, port))
        raise httpcore.ConnectError("not dialing in tests")

    async def sleep(self, seconds):
        pass


async def test_rebinding_host_is_never_dialed(monkeypatch):
    """The guard runs at dial time, on the address actually dialed."""
    monkeypatch.setattr("memory.common.settings.CHECK_ALLOW_PRIVATE_CALLBACKS", False)
    inner = RecordingBackend()

    async def resolve(host):
        return [ipaddress.ip_address("169.254.169.254")]

    monkeypatch.setattr(
        callbacks, "callback_backend",
        lambda: PinnedBackend(policy=callbacks.callback_policy(), resolve=resolve, inner=inner),
    )
    assert await callbacks._deliver_callback(_job("http://rebind.example.com/cb")) is False
    assert inner.dials == []


@pytest.mark.parametrize("url", ["ftp://example.com/x", "not-a-url", ""])
async def test_non_http_urls_rejected(url, monkeypatch):
    def no_client(**kw):
        raise AssertionError("must not build a client")

    monkeypatch.setattr(callbacks, "public_http_client", no_client)
    assert await callbacks._deliver_callback(_job(url)) is False


async def test_deliver_callback_swallows_unexpected_errors(monkeypatch):
    # build_callback_payload raising must not propagate out of deliver_callback
    def boom(job):
        raise ValueError("bad payload")

    monkeypatch.setattr(callbacks, "build_callback_payload", boom)
    job = cast(JobRecord, {"job_id": "chk_x",
                           "callback_url": "https://example.com/cb"})
    result = await callbacks.deliver_callback(job)
    assert result is False


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient.

    ``post`` pops the next queued response; a queued ``Exception`` instance is
    raised instead of returned, simulating a network error.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json):
        self.post_calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def install_fake(monkeypatch, responses):
    client = FakeClient(responses)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(callbacks.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(callbacks, "public_http_client", lambda **kw: client)
    return client


def _job(url: str = "https://example.com/cb") -> JobRecord:
    return cast(JobRecord, {
        "job_id": "chk_x", "callback_url": url,
        "status": "ok", "result": "", "callback_token": "t",
        "error": "", "submitted_at": "2026-01-01T00:00:00+00:00",
        "completed_at": ""})


async def test_deliver_2xx_first_attempt(monkeypatch):
    client = install_fake(monkeypatch, [FakeResponse(200)])
    assert await callbacks._deliver_callback(_job()) is True
    assert client.post_calls == 1


async def test_deliver_4xx_one_retry_then_stop(monkeypatch):
    client = install_fake(monkeypatch, [FakeResponse(400), FakeResponse(400),
                                        FakeResponse(400)])
    assert await callbacks._deliver_callback(_job()) is False
    assert client.post_calls <= 2


async def test_deliver_5xx_exhausts_attempts(monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_CALLBACK_MAX_ATTEMPTS", 3)
    client = install_fake(monkeypatch, [FakeResponse(500)] * 3)
    assert await callbacks._deliver_callback(_job()) is False
    assert client.post_calls == 3


async def test_deliver_network_error_then_2xx(monkeypatch):
    client = install_fake(monkeypatch, [httpx.HTTPError("boom"), FakeResponse(200)])
    assert await callbacks._deliver_callback(_job()) is True
    assert client.post_calls == 2


async def test_build_payload_echoes_token():
    job = cast(JobRecord, {
        "job_id": "chk_1", "status": "ok", "result": '{"summary":"s"}',
        "error": "", "callback_token": "tok",
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:01:00+00:00",
    })
    payload = callbacks.build_callback_payload(job)
    assert payload.job_id == "chk_1"
    assert payload.status == "ok"
    assert payload.result == {"summary": "s"}
    assert payload.error is None
    assert payload.callback_token == "tok"
