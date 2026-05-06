"""Tests for the Fireflies parser/client."""

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from memory.parsers.fireflies import (
    FirefliesClient,
    FirefliesError,
    build_meeting_kwargs,
    format_transcript_text,
)


# Captured response shapes used as test fixtures so the tests stay grounded
# in real Fireflies API behaviour. Truncated to a few sentences each.
SAMPLE_TRANSCRIPT_LIST = [
    {
        "id": "01KQRQHKNTJ1CYBCHPFFEFGNZY",
        "title": "Equistamp Finance meeting",
        "date": 1777997700000,
        "dateString": "2026-04-04T00:00:00.000Z",
        "organizer_email": "chris@equistamp.com",
        "participants": [
            "chris@equistamp.com",
            "fraser@equistamp.com",
            "daniel@equistamp.com",
        ],
    },
    {
        "id": "01KQMG8Z6VNMPEEK8WS8M0SHZ4",
        "title": "EU Tender briefing",
        "date": 1777903200000,
        "dateString": "2026-04-03T00:00:00.000Z",
        "organizer_email": "chris@equistamp.com",
        "participants": ["chris@equistamp.com", "daniel@equistamp.com"],
    },
]

SAMPLE_TRANSCRIPT_FULL = {
    "id": "01KQCWVF0BCAD0C7Q5C6027BAS",
    "title": "Project Close-out Huddle",
    "date": 1777475700000,
    "dateString": "2026-04-29T15:15:00.000Z",
    "duration": 47.88999938964844,
    "transcript_url": "https://app.fireflies.ai/view/01KQCWVF0BCAD0C7Q5C6027BAS",
    "organizer_email": "honor@equistamp.com",
    "participants": [
        "chris@equistamp.com",
        "daniel@equistamp.com",
        "honor@equistamp.com",
    ],
    "meeting_attendees": [
        {"displayName": None, "email": "chris@equistamp.com", "name": None, "location": None},
        {"displayName": None, "email": "daniel@equistamp.com", "name": None, "location": None},
        {"displayName": None, "email": "honor@equistamp.com", "name": None, "location": None},
    ],
    "speakers": [
        {"id": 0, "name": "jgh"},
        {"id": 1, "name": "Honor Chan"},
    ],
    "sentences": [
        {"speaker_id": 0, "speaker_name": "jgh", "text": "Things mentioned, Singapore stuff.", "start_time": 0.4, "end_time": 2.7},
        {"speaker_id": 0, "speaker_name": "jgh", "text": "So I put those in cloud.", "start_time": 2.7, "end_time": 4.0},
        {"speaker_id": 1, "speaker_name": "Honor Chan", "text": "Got it.", "start_time": 4.0, "end_time": 5.0},
        {"speaker_id": 0, "speaker_name": "jgh", "text": "Thanks.", "start_time": 5.0, "end_time": 6.0},
    ],
    "summary": {
        "overview": "- Discussed registry and skills tracking.",
        "action_items": "**Daniel**\nDocument roles (40:31)",
        "keywords": ["Organizational Processes"],
    },
}


@pytest.mark.parametrize(
    "sentences,expected",
    [
        ([], ""),
        (
            [{"speaker_name": "Alice", "text": "Hi."}],
            "Alice: Hi.",
        ),
        (
            [
                {"speaker_name": "Alice", "text": "Hi."},
                {"speaker_name": "Alice", "text": "How are you?"},
                {"speaker_name": "Bob", "text": "Good."},
            ],
            "Alice: Hi. How are you?\nBob: Good.",
        ),
        (
            [
                {"speaker_name": "", "text": "anonymous"},
                {"speaker_name": "Alice", "text": "follow-up"},
            ],
            "Unknown: anonymous\nAlice: follow-up",
        ),
        (
            [
                {"speaker_name": "Alice", "text": "  spaced  "},
                {"speaker_name": "Alice", "text": ""},
                {"speaker_name": "Alice", "text": "again"},
            ],
            "Alice: spaced again",
        ),
    ],
)
def test_format_transcript_text(sentences, expected):
    assert format_transcript_text(sentences) == expected


def test_build_meeting_kwargs_full_shape():
    kwargs = build_meeting_kwargs(SAMPLE_TRANSCRIPT_FULL)

    assert kwargs["title"] == "Project Close-out Huddle"
    assert kwargs["meeting_date"] == "2026-04-29T15:15:00.000Z"
    assert kwargs["duration_minutes"] == 48  # rounded from 47.89
    assert kwargs["source_tool"] == "fireflies"
    assert kwargs["external_id"] == "01KQCWVF0BCAD0C7Q5C6027BAS"
    assert kwargs["attendee_emails"] == [
        "chris@equistamp.com",
        "daniel@equistamp.com",
        "honor@equistamp.com",
    ]
    assert "jgh:" in kwargs["transcript"]
    assert "Honor Chan: Got it." in kwargs["transcript"]


def test_build_meeting_kwargs_filters_empty_emails():
    transcript = {
        **SAMPLE_TRANSCRIPT_FULL,
        "meeting_attendees": [
            {"email": "real@example.com"},
            {"email": ""},
            {"email": None},
            {"email": "  spaced@example.com  "},
        ],
    }
    kwargs = build_meeting_kwargs(transcript)
    assert kwargs["attendee_emails"] == [
        "real@example.com",
        "spaced@example.com",
    ]


def test_build_meeting_kwargs_handles_missing_duration():
    transcript = {**SAMPLE_TRANSCRIPT_FULL, "duration": None}
    kwargs = build_meeting_kwargs(transcript)
    assert kwargs["duration_minutes"] is None


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def _stub_post(response_body, status_code=200):
    """Return a callable that records calls and returns the canned response."""
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(status_code, response_body)

    return _post, calls


def test_client_requires_api_key():
    with pytest.raises(ValueError):
        FirefliesClient(api_key="")


def test_client_list_transcripts_passes_iso_date():
    post, calls = _stub_post({"data": {"transcripts": SAMPLE_TRANSCRIPT_LIST}})
    with patch.object(httpx, "post", post):
        client = FirefliesClient("k")
        from_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
        result = client.list_transcripts(from_date=from_date, limit=5)

    assert len(result) == 2
    body = calls[0]["json"]
    assert body["variables"]["fromDate"] == from_date.isoformat()
    assert body["variables"]["limit"] == 5
    assert "fromDate: $fromDate" in body["query"]
    assert calls[0]["headers"]["Authorization"] == "Bearer k"


def test_client_list_transcripts_with_to_date():
    post, calls = _stub_post({"data": {"transcripts": []}})
    with patch.object(httpx, "post", post):
        client = FirefliesClient("k")
        to_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
        client.list_transcripts(to_date=to_date)

    body = calls[0]["json"]
    assert body["variables"]["toDate"] == to_date.isoformat()
    assert "toDate: $toDate" in body["query"]


def test_client_get_transcript():
    post, calls = _stub_post({"data": {"transcript": SAMPLE_TRANSCRIPT_FULL}})
    with patch.object(httpx, "post", post):
        client = FirefliesClient("k")
        result = client.get_transcript("01KQCWVF0BCAD0C7Q5C6027BAS")

    assert result["id"] == "01KQCWVF0BCAD0C7Q5C6027BAS"
    assert calls[0]["json"]["variables"] == {"id": "01KQCWVF0BCAD0C7Q5C6027BAS"}


def test_client_raises_on_http_error():
    """4xx → not retryable (auth / bad query — retry won't fix it)."""
    post, _ = _stub_post({"errors": [{"message": "Unauthorized"}]}, status_code=401)
    with patch.object(httpx, "post", post):
        client = FirefliesClient("k")
        with pytest.raises(FirefliesError, match="HTTP 401") as excinfo:
            client.list_transcripts()
    assert excinfo.value.retryable is False
    assert excinfo.value.status_code == 401


def test_client_raises_retryable_on_5xx():
    """5xx → retryable (transient server-side issue)."""
    post, _ = _stub_post({"errors": [{"message": "boom"}]}, status_code=503)
    with patch.object(httpx, "post", post):
        client = FirefliesClient("k")
        with pytest.raises(FirefliesError, match="HTTP 503") as excinfo:
            client.list_transcripts()
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code == 503


def test_client_raises_on_graphql_errors():
    """GraphQL-level errors → not retryable (bad query / persistent auth)."""
    post, _ = _stub_post({"errors": [{"message": "Invalid argument(s)"}]})
    with patch.object(httpx, "post", post):
        client = FirefliesClient("k")
        with pytest.raises(FirefliesError, match="GraphQL errors") as excinfo:
            client.list_transcripts()
    assert excinfo.value.retryable is False


def test_client_wraps_transport_errors():
    """Transport failures (DNS, connection reset, timeout) → retryable."""

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("nope")

    with patch.object(httpx, "post", _raise):
        client = FirefliesClient("k")
        with pytest.raises(FirefliesError, match="Fireflies request failed") as excinfo:
            client.list_transcripts()
    assert excinfo.value.retryable is True
