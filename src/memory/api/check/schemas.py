"""Pydantic models and typed errors for the check job queue."""

import json
from typing import Any, Literal, TypedDict, cast

from pydantic import BaseModel, Field

Mode = Literal["verify", "research", "link"]
ResultStatus = Literal["ok", "error"]
JobStatus = Literal["queued", "in_flight", "ok", "error", "expired"]


class JobRecord(TypedDict):
    """Raw Redis hash for a check job (every field is a string)."""

    job_id: str
    user_id: str
    status: str
    mode: str
    text: str
    context: str
    callback_url: str
    callback_token: str
    submitted_at: str
    completed_at: str
    lease_id: str
    result: str
    error: str
    attempts: str


class SubmitRequest(BaseModel):
    text: str = Field(min_length=1)
    mode: Mode = "research"
    context: dict[str, Any] = Field(default_factory=dict)
    callback_url: str | None = None
    callback_token: str | None = None


class SubmitResponse(BaseModel):
    job_id: str
    status: JobStatus


class ResultRequest(BaseModel):
    status: ResultStatus
    lease_id: str
    result: dict[str, Any] | None = None
    error: str | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: str
    completed_at: str | None = None

    @classmethod
    def from_record(cls, rec: "JobRecord") -> "JobStatusResponse":
        return cls(
            job_id=rec["job_id"],
            status=cast(JobStatus, rec["status"]),
            result=json.loads(rec["result"]) if rec.get("result") else None,
            error=rec.get("error") or None,
            submitted_at=rec["submitted_at"],
            completed_at=rec.get("completed_at") or None,
        )


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    mode: Mode
    submitted_at: str
    completed_at: str | None = None

    @classmethod
    def from_record(cls, rec: "JobRecord") -> "JobSummary":
        return cls(
            job_id=rec["job_id"],
            status=cast(JobStatus, rec["status"]),
            mode=cast(Mode, rec["mode"]),
            submitted_at=rec["submitted_at"],
            completed_at=rec.get("completed_at") or None,
        )


class ListResponse(BaseModel):
    jobs: list[JobSummary]


class NextJob(BaseModel):
    job_id: str
    text: str
    mode: Mode
    context: dict[str, Any]
    lease_id: str
    lease_expires_at: str
    submitted_at: str

    @classmethod
    def from_record(
        cls, rec: "JobRecord", lease_id: str, lease_expires_at: str
    ) -> "NextJob":
        return cls(
            job_id=rec["job_id"],
            text=rec["text"],
            mode=cast(Mode, rec["mode"]),
            context=json.loads(rec["context"] or "{}"),
            lease_id=lease_id,
            lease_expires_at=lease_expires_at,
            submitted_at=rec["submitted_at"],
        )


class CallbackPayload(JobStatusResponse):
    callback_token: str | None = None

    @classmethod
    def from_record(cls, rec: "JobRecord") -> "CallbackPayload":
        base = JobStatusResponse.from_record(rec)
        return cls(**base.model_dump(), callback_token=rec.get("callback_token") or None)


class QueueFull(Exception):
    """Raised when a user's pending queue is at CHECK_QUEUE_MAX_DEPTH."""


class JobGone(Exception):
    """Raised on a result submission whose lease is no longer valid (410)."""


class JobAlreadyComplete(Exception):
    """Raised on a result submission for an already-completed job (409)."""
