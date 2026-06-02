from typing import cast

import pytest

from memory.api.check import callbacks
from memory.api.check.schemas import JobRecord

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/admin",
    "http://localhost/x",
    "http://10.0.0.5/x",
    "ftp://example.com/x",
    "not-a-url",
])
async def test_unsafe_callback_urls_rejected(url, monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_ALLOW_PRIVATE_CALLBACKS", False)
    assert await callbacks.is_safe_callback_url(url) is False


async def test_public_https_url_allowed(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34"]  # public
    monkeypatch.setattr(callbacks, "_resolve", fake_resolve)
    assert await callbacks.is_safe_callback_url("https://example.com/cb") is True


async def test_private_allowed_when_configured(monkeypatch):
    monkeypatch.setattr("memory.common.settings.CHECK_ALLOW_PRIVATE_CALLBACKS", True)
    assert await callbacks.is_safe_callback_url("http://127.0.0.1:9000/cb") is True


async def test_deliver_callback_swallows_unexpected_errors(monkeypatch):
    # build_callback_payload raising must not propagate out of deliver_callback
    def boom(job):
        raise ValueError("bad payload")

    async def always_true(url):
        return True

    monkeypatch.setattr(callbacks, "build_callback_payload", boom)
    monkeypatch.setattr(callbacks, "is_safe_callback_url", always_true)
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

    async def always_safe(url):
        return True

    monkeypatch.setattr(callbacks, "is_safe_callback_url", always_safe)
    monkeypatch.setattr(callbacks.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(callbacks.httpx, "AsyncClient", lambda **kw: client)
    return client


def _job() -> JobRecord:
    return cast(JobRecord, {
        "job_id": "chk_x", "callback_url": "https://example.com/cb",
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
    import httpx
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
