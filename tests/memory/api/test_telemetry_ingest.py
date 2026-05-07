"""Size-cap tests for /telemetry/ingest.

The endpoint used to call ``await request.body()`` with no cap, so an
authenticated user could OOM the API by POSTing a multi-GB OTLP payload
(buffered before any rate limit fires). It also called
``parse_otlp_json`` synchronously, which can fan out a small payload
into a huge in-memory event list before handoff to a background task.

These tests pin two invariants:
- ``Content-Length`` over the cap → 413 immediately, no body read.
- A request that lies about (or omits) Content-Length and streams more
  than the cap → 413 mid-stream, no parse, no background task.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def small_cap():
    """Squash the cap to 1 KiB so the tests don't have to allocate megabytes."""
    with patch("memory.common.settings.MAX_TELEMETRY_PAYLOAD_BYTES", 1024):
        yield 1024


@pytest.fixture
def app_client(small_cap):
    """Mount the telemetry router on a bare app with auth + parse mocked."""
    from memory.api import telemetry
    from memory.api.auth import get_current_user

    app = FastAPI()
    app.include_router(telemetry.router)

    user = MagicMock()
    user.id = 1
    user.scopes = ["read", "write"]
    app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_ingest_rejects_oversized_content_length(app_client, small_cap):
    """Declared Content-Length over the cap must 413 before reading the body."""
    payload = b"x" * (small_cap + 1)

    with (
        patch("memory.api.telemetry.parse_otlp_json") as mock_parse,
        patch("memory.api.telemetry.write_events_to_db") as mock_write,
    ):
        response = app_client.post(
            "/telemetry/ingest",
            content=payload,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "exceeds" in detail
    # The handler must NOT have parsed or queued anything.
    mock_parse.assert_not_called()
    mock_write.assert_not_called()


def test_ingest_streamed_payload_overflow_is_413(app_client, small_cap):
    """A client without Content-Length that exceeds the cap mid-stream
    must also 413.

    TestClient computes Content-Length from a raw bytes payload, so to
    exercise the streaming path we send a chunked iterator and pop the
    Content-Length header off the request. The handler's per-chunk
    accumulator should bail before the body finishes."""

    def gen():
        # Three chunks each just under the cap. Total > 2x the cap.
        for _ in range(3):
            yield b"y" * (small_cap // 2)

    with (
        patch("memory.api.telemetry.parse_otlp_json") as mock_parse,
        patch("memory.api.telemetry.write_events_to_db") as mock_write,
    ):
        response = app_client.post(
            "/telemetry/ingest",
            content=gen(),
            headers={
                "Content-Type": "application/json",
                # Force chunked transfer.
                "Transfer-Encoding": "chunked",
            },
        )

    assert response.status_code == 413
    mock_parse.assert_not_called()
    mock_write.assert_not_called()


def test_ingest_within_cap_succeeds(app_client, small_cap):
    """A normal, small payload still gets parsed and queued."""
    payload = b'{"resourceMetrics": []}'

    with (
        patch("memory.api.telemetry.parse_otlp_json", return_value=[{"id": 1}]) as mock_parse,
        patch("memory.api.telemetry.write_events_to_db"),
    ):
        response = app_client.post(
            "/telemetry/ingest",
            content=payload,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["events_received"] == 1
    mock_parse.assert_called_once()


def test_ingest_event_count_capped(app_client, small_cap):
    """Even for a small payload, expanded event lists are truncated."""
    huge_event_list = [{"id": i} for i in range(20000)]

    with (
        patch(
            "memory.api.telemetry.parse_otlp_json",
            return_value=huge_event_list,
        ),
        patch("memory.api.telemetry.write_events_to_db") as mock_write,
    ):
        response = app_client.post(
            "/telemetry/ingest",
            content=b'{"resourceMetrics": []}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    body = response.json()
    # Per-request event cap defined in telemetry.py.
    assert body["events_received"] == 10000
    mock_write.assert_called_once()
    forwarded_events = mock_write.call_args.args[0]
    assert len(forwarded_events) == 10000
