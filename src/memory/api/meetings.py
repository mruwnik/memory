"""API endpoints for Meeting management."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.common.celery_app import PROCESS_MEETING
from memory.common.db.connection import get_session
from memory.common.db.models import User, JobType
from memory.common.jobs import dispatch_job

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
    tags: list[str] | None = None


class MeetingQueued(BaseModel):
    """Response when a meeting is queued for processing."""

    job_id: int
    status: str
    external_id: str | None
    message: str


MAX_TRANSCRIPT_SIZE = 500_000  # 500KB limit for transcripts


@router.post("")
def create_meeting(
    data: MeetingCreate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
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

    Returns a job_id that can be used to track processing status via GET /jobs/{job_id}
    """
    if len(data.transcript) > MAX_TRANSCRIPT_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Transcript exceeds maximum size of {MAX_TRANSCRIPT_SIZE} bytes",
        )

    result = dispatch_job(
        session=db,
        job_type=JobType.MEETING,
        task_name=PROCESS_MEETING,
        task_kwargs={
            "transcript": data.transcript,
            "title": data.title,
            "meeting_date": data.meeting_date.isoformat() if data.meeting_date else None,
            "duration_minutes": data.duration_minutes,
            "attendee_names": data.attendees or [],
            "source_tool": data.source_tool,
            "external_id": data.external_id,
            "tags": data.tags or [],
        },
        external_id=data.external_id,
        user_id=user.id,
        exclude_from_params=["transcript"],  # Don't store full transcript in job params
    )

    return MeetingQueued(
        job_id=result.job.id,
        status="queued" if result.is_new else result.job.status,
        external_id=data.external_id,
        message=result.message,
    )
