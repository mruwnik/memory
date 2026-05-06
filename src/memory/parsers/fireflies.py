"""Fireflies.ai GraphQL client and transcript formatter.

Fireflies provides meeting transcripts via a GraphQL API at api.fireflies.ai.
Authentication is via a per-user API key (Bearer token).

The default `transcripts` query returns transcripts where the API key holder
is a participant — including meetings organized by other people. This makes
polling sufficient for users who never organize meetings (the common case).

Empirically verified behaviors:
- `fromDate` / `toDate` accept ISO 8601 strings, NOT epoch ms
- Default scope = participant (not just organizer)
- Pagination via `skip`; ordering is newest-first
- `meeting_attendees[].email` is reliable; `name`/`displayName` are usually null
- `speakers[].name` is sometimes garbage (e.g. "jgh" for users who type
  initials to bypass empty-name fields when joining a meeting)
- Rate limit: ~50 req per ~30s window
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"

# Default page size. Fireflies' rate limit is generous (50/30s) but each
# transcript fetch returns a sizable payload, so 50 keeps individual responses
# manageable.
DEFAULT_PAGE_LIMIT = 50

# Fields to request when listing transcripts. Keep small — we only need to
# decide whether to fetch the full transcript next; the prefilter uses `id`
# and the rare logging path reads `dateString`. ``meeting_attendees`` /
# ``participants`` are deliberately omitted so list pages stay light on
# rescans (5000-item potential).
LIST_FIELDS = """
    id
    title
    date
    dateString
"""

# Fields to request when fetching a single transcript for ingestion.
TRANSCRIPT_FIELDS = """
    id
    title
    date
    dateString
    duration
    transcript_url
    organizer_email
    participants
    meeting_attendees {
        displayName
        email
        name
        location
    }
    speakers {
        id
        name
    }
    sentences {
        speaker_id
        speaker_name
        text
        start_time
        end_time
    }
    summary {
        overview
        action_items
        keywords
    }
"""


class FirefliesError(Exception):
    """Raised when the Fireflies GraphQL API returns an error response.

    Attributes:
        status_code: HTTP status code (or None for transport / GraphQL errors).
        retryable: True if a sync should be retried automatically (transient
            transport / 5xx errors), False if the error is persistent and
            retrying without intervention will keep failing (auth / 4xx /
            GraphQL response errors).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class FirefliesClient:
    """Synchronous Fireflies GraphQL client for use in Celery tasks."""

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._timeout = timeout

    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        """Issue a GraphQL POST and return the `data` payload.

        Raises FirefliesError on transport or GraphQL errors so callers can
        record sync_error on the TranscriptAccount and back off.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = httpx.post(
                FIREFLIES_GRAPHQL_URL,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            # Transport-level failures (DNS, connection reset, timeout) are
            # almost always transient. `from None` strips the chain so the
            # caller's logs don't include the underlying request repr (some
            # httpx versions echo headers there).
            raise FirefliesError(
                f"Fireflies request failed: {exc}", retryable=True
            ) from None

        if response.status_code != 200:
            # 5xx are transient (retry), 4xx are persistent (auth/bad query).
            retryable = response.status_code >= 500
            raise FirefliesError(
                f"Fireflies returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                retryable=retryable,
            )

        body = response.json()
        if body.get("errors"):
            # GraphQL-level errors (bad query, auth) — not retryable.
            raise FirefliesError(
                f"Fireflies GraphQL errors: {body['errors']}", retryable=False
            )

        return body.get("data", {})

    def list_transcripts(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        skip: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[dict]:
        """List transcripts visible to the API key holder.

        from_date / to_date filter by meeting date. Both inclusive.
        Results ordered newest-first.

        Always declares all four variables (fromDate/toDate/limit/skip);
        omitting from the variables dict makes Fireflies treat them as null.
        """
        query = f"""
            query Transcripts($fromDate: DateTime, $toDate: DateTime, $limit: Int, $skip: Int) {{
                transcripts(
                    fromDate: $fromDate,
                    toDate: $toDate,
                    limit: $limit,
                    skip: $skip
                ) {{{LIST_FIELDS}}}
            }}
        """
        variables: dict[str, Any] = {"limit": limit, "skip": skip}
        if from_date is not None:
            variables["fromDate"] = from_date.isoformat()
        if to_date is not None:
            variables["toDate"] = to_date.isoformat()

        data = self._post(query, variables)
        return data.get("transcripts", []) or []

    def get_transcript(self, meeting_id: str) -> dict | None:
        """Fetch a full transcript by ID. Returns None if not found."""
        query = f"""
            query Transcript($id: String!) {{
                transcript(id: $id) {{{TRANSCRIPT_FIELDS}}}
            }}
        """
        data = self._post(query, {"id": meeting_id})
        return data.get("transcript")


def format_transcript_text(sentences: Iterable[dict]) -> str:
    """Join sentences into 'Speaker: text' lines for downstream LLM extraction.

    Speaker labels are passed through as-is — Fireflies sometimes returns
    cryptic labels ("jgh", "sdf") for participants who typed initials to
    join a meeting. The downstream extractor reads natural-language text
    and tolerates noisy speaker tags fine.
    """
    lines = []
    last_speaker: str | None = None
    buffer: list[str] = []

    for sentence in sentences:
        speaker = (sentence.get("speaker_name") or "").strip() or "Unknown"
        text = (sentence.get("text") or "").strip()
        if not text:
            continue
        if speaker == last_speaker:
            buffer.append(text)
            continue
        if buffer and last_speaker is not None:
            lines.append(f"{last_speaker}: {' '.join(buffer)}")
        last_speaker = speaker
        buffer = [text]

    if buffer and last_speaker is not None:
        lines.append(f"{last_speaker}: {' '.join(buffer)}")

    return "\n".join(lines)


def build_meeting_kwargs(transcript: dict) -> dict[str, Any]:
    """Map a Fireflies transcript into kwargs for process_meeting().

    `attendee_emails` is preferred over speaker names: meeting_attendees[].email
    is consistently populated, while speakers[].name is sometimes garbage.
    """
    sentences = transcript.get("sentences") or []
    attendees = transcript.get("meeting_attendees") or []
    duration_minutes = transcript.get("duration")
    if duration_minutes is not None:
        duration_minutes = int(round(float(duration_minutes)))

    emails = [
        (a.get("email") or "").strip()
        for a in attendees
        if (a.get("email") or "").strip()
    ]

    return {
        "transcript": format_transcript_text(sentences),
        "title": transcript.get("title"),
        "meeting_date": transcript.get("dateString"),
        "duration_minutes": duration_minutes,
        "attendee_emails": emails,
        "source_tool": "fireflies",
        "external_id": transcript.get("id"),
    }
