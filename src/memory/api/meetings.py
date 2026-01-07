"""API endpoints for Meeting management."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from memory.api.auth import get_current_user
from memory.common.celery_app import app as celery_app, PROCESS_MEETING
from memory.common.db.models import User

router = APIRouter(prefix="/meetings", tags=["meetings"])


class MeetingCreate(BaseModel):
    """Request body for creating a meeting transcript."""

    transcript: str
    title: str | None = None
    meeting_date: datetime | None = None
    duration_minutes: int | None = None
    attendees: list[str] | None = None
    source_tool: str | None = None
    external_id: str | None = None
    tags: list[str] = []


class MeetingQueued(BaseModel):
    """Response when a meeting is queued for processing."""

    status: str
    external_id: str | None
    message: str


MAX_TRANSCRIPT_SIZE = 500_000  # 500KB limit for transcripts


@router.post("")
def create_meeting(
    data: MeetingCreate,
    user: User = Depends(get_current_user),
) -> MeetingQueued:
    """
    Queue a meeting transcript for processing.

    The worker will:
    1. Create the Meeting record (idempotent via external_id)
    2. Extract summary, notes, and action items via LLM
    3. Create linked Task records
    4. Match attendee names to existing Person records

    Use external_id for idempotency - resubmitting with the same external_id
    will skip creation if the meeting already exists.
    """
    if len(data.transcript) > MAX_TRANSCRIPT_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Transcript exceeds maximum size of {MAX_TRANSCRIPT_SIZE} bytes",
        )

    celery_app.send_task(
        PROCESS_MEETING,
        kwargs={
            "transcript": data.transcript,
            "title": data.title,
            "meeting_date": data.meeting_date.isoformat() if data.meeting_date else None,
            "duration_minutes": data.duration_minutes,
            "attendee_names": data.attendees or [],
            "source_tool": data.source_tool,
            "external_id": data.external_id,
            "tags": data.tags,
        },
    )

    return MeetingQueued(
        status="queued",
        external_id=data.external_id,
        message="Meeting queued for processing. Use external_id to check status via search.",
    )
