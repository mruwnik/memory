"""Push ingest for transcript accounts: POST /transcripts/ingest/{account_id}.

Senders authenticate with the account's webhook secret, either as an
HMAC-SHA256 of the raw body (``X-Hub-Signature: sha256=<hex>``, the scheme
Fireflies uses) or as ``Authorization: Bearer <secret>`` for senders that
can't sign. The path is whitelisted from the auth middleware for that reason.

Two body shapes are accepted: a generic transcript, queued straight to
process_meeting, and a Fireflies ``meeting.transcribed`` notification, which
only names a meeting and so queues a one-meeting fetch.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from memory.api.meetings import MAX_TRANSCRIPT_SIZE
from memory.api.request_body import read_request_body_with_cap
from memory.common.celery_app import INGEST_TRANSCRIPT_MEETING, PROCESS_MEETING
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import get_session
from memory.common.db.models import JobType
from memory.common.db.models.sources import TranscriptAccount
from memory.common.jobs import dispatch_job
from memory.common.rate_limit import check_rate_limit_spec
from memory.common.transcripts import PUSH_ONLY_PROVIDERS
from memory.parsers.fireflies import format_transcript_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcripts", tags=["transcripts"])

MAX_BODY_BYTES = 1_048_576
# Each accepted push costs an LLM extraction, so a leaked secret must not be
# able to queue unbounded work. Per-IP limits come from the SlowAPI default.
ACCOUNT_RATE_LIMIT = "30/minute"

FIREFLIES_TRANSCRIBED_EVENTS = {
    ("event", "meeting.transcribed"),
    ("eventType", "Transcription completed"),
}


class Participant(BaseModel):
    name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=320)


class Segment(BaseModel):
    speaker: str | None = Field(None, max_length=200)
    text: str


class GenericTranscript(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    title: str | None = Field(None, max_length=500)
    started_at: datetime | None = None
    duration_minutes: int | None = Field(None, ge=0)
    participants: list[Participant] = []
    text: str | None = Field(None, max_length=MAX_TRANSCRIPT_SIZE)
    segments: list[Segment] | None = None

    @model_validator(mode="after")
    def exactly_one_body(self) -> "GenericTranscript":
        if (self.text is None) == (self.segments is None):
            raise ValueError("provide exactly one of text or segments")
        if len(self.transcript()) > MAX_TRANSCRIPT_SIZE:
            raise ValueError(f"transcript exceeds {MAX_TRANSCRIPT_SIZE} characters")
        return self

    def transcript(self) -> str:
        if self.text is not None:
            return self.text
        return format_transcript_text(
            {"speaker_name": s.speaker, "text": s.text} for s in self.segments or []
        )


def secret_matches(secret: str, body: bytes, request: Request) -> bool:
    signature = request.headers.get("x-hub-signature")
    if signature:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        presented = signature.removeprefix("sha256=").lower()
        return hmac.compare_digest(expected.encode(), presented.encode())

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(secret.encode(), token.encode())


def queue_fireflies_meeting(account: TranscriptAccount, payload: dict) -> JSONResponse:
    if account.provider != "fireflies":
        raise HTTPException(status_code=422, detail="Fireflies notifications need a fireflies source")
    if not any(payload.get(k) == v for k, v in FIREFLIES_TRANSCRIBED_EVENTS):
        return JSONResponse({"status": "ignored"}, status_code=200)

    meeting_id = payload.get("meeting_id") or payload.get("meetingId")
    if not isinstance(meeting_id, str) or not 0 < len(meeting_id) <= 200:
        raise HTTPException(status_code=422, detail="Invalid meeting id")
    celery_app.send_task(INGEST_TRANSCRIPT_MEETING, args=[account.id, meeting_id])
    return JSONResponse({"status": "queued"}, status_code=202)


def queue_generic_transcript(
    db: Session, account: TranscriptAccount, payload: Any
) -> JSONResponse:
    try:
        data = GenericTranscript.model_validate(payload)
    except ValidationError as exc:
        # Built by hand: pydantic's default error includes the offending input.
        errors = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    # Push-only sources have no poll, so "last sync" is the last accepted push.
    # Committed here because dispatch_job doesn't commit on a dedupe hit.
    if account.provider in PUSH_ONLY_PROVIDERS:
        account.last_sync_at = datetime.now(timezone.utc)
        db.commit()

    # Meeting and job dedupe is global, so a sender's ids are namespaced to
    # the account to stop them colliding with (or squatting) anyone else's.
    external_id = f"push:{account.id}:{data.external_id}"
    result = dispatch_job(
        session=db,
        job_type=JobType.MEETING,
        task_name=PROCESS_MEETING,
        task_kwargs={
            "transcript": data.transcript(),
            "title": data.title,
            "meeting_date": data.started_at.isoformat() if data.started_at else None,
            "duration_minutes": data.duration_minutes,
            "attendee_pairs": [[p.email.strip(), p.name or ""] for p in data.participants if p.email and p.email.strip()],
            "attendee_names": [p.name for p in data.participants if p.name and not (p.email and p.email.strip())],
            "source_tool": account.provider,
            "external_id": external_id,
            "tags": list(account.tags or []),
            "transcript_account_id": account.id,
        },
        external_id=external_id,
        user_id=account.user_id,
    )
    status = "queued" if result.is_new else result.job.status
    return JSONResponse({"status": status, "job_id": result.job.id}, status_code=202)


@router.post("/ingest/{account_id}")
async def ingest_transcript(
    account_id: int,
    request: Request,
    db: Session = Depends(get_session),
) -> JSONResponse:
    """Accept a pushed transcript or provider notification for one account."""
    body = await read_request_body_with_cap(request, MAX_BODY_BYTES)
    # The DB work and the broker publish are blocking; a stalled Redis must
    # not freeze the event loop.
    return await run_in_threadpool(handle_push, db, account_id, body, request)


def handle_push(db: Session, account_id: int, body: bytes, request: Request) -> JSONResponse:
    account = db.get(TranscriptAccount, account_id)
    secret = account.webhook_secret if account else None
    if account is None or not secret or not secret_matches(secret, body, request):
        logger.info(f"transcript push rejected account={account_id}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not account.active:
        raise HTTPException(status_code=403, detail="Source is inactive")
    if not check_rate_limit_spec(f"transcript_ingest:{account.id}", ACCOUNT_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Too many pushes for this source")

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise HTTPException(status_code=400, detail="Body must be JSON")

    logger.info(
        f"transcript push accepted account={account_id} "
        f"body_sha256={hashlib.sha256(body).hexdigest()[:16]}"
    )
    if isinstance(payload, dict) and ("meeting_id" in payload or "meetingId" in payload):
        return queue_fireflies_meeting(account, payload)
    return queue_generic_transcript(db, account, payload)
