"""
Database models for async job tracking.

PendingJob provides client-facing status tracking for async operations,
allowing clients to check job status and retrieve results.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class JobStatus(str, Enum):
    """Status values for pending jobs."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class JobType(str, Enum):
    """Types of jobs that can be tracked."""

    MEETING = "meeting"
    REPROCESS = "reprocess"
    EMAIL_SYNC = "email_sync"
    CONTENT_INGEST = "content_ingest"


class PendingJob(Base):
    """
    Tracks async job status for client-facing operations.

    This is separate from MetricEvent which is for internal observability.
    PendingJob provides:
    - Client-queryable status
    - Result linking (what item was created/modified)
    - Retry tracking
    - Error messages for debugging
    """

    __tablename__ = "pending_jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Job identification
    job_type = Column(String(50), nullable=False)
    external_id = Column(String(200), nullable=True)  # Client idempotency key
    celery_task_id = Column(String(200), nullable=True)  # For correlation

    # Status tracking
    status = Column(String(20), nullable=False, default=JobStatus.PENDING.value)
    error_message = Column(Text, nullable=True)

    # Result linking
    result_id = Column(BigInteger, nullable=True)  # ID of created/modified item
    result_type = Column(String(50), nullable=True)  # Model name: "Meeting", etc.

    # Job parameters (for debugging/retry)
    params = Column(JSONB, default=dict, nullable=False)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Retry tracking
    attempts = Column(Integer, default=0, nullable=False)

    # User association
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    user = relationship("User", backref="jobs")

    __table_args__ = (
        Index("idx_pending_jobs_status", "status"),
        Index("idx_pending_jobs_job_type", "job_type"),
        Index("idx_pending_jobs_external_id", "external_id"),
        Index("idx_pending_jobs_user_id", "user_id"),
        Index("idx_pending_jobs_created_at", "created_at"),
        Index("idx_pending_jobs_celery_task_id", "celery_task_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<PendingJob(id={self.id}, type={self.job_type}, "
            f"status={self.status}, external_id={self.external_id})>"
        )

    def mark_processing(self) -> None:
        """Mark job as processing and increment attempts."""
        self.status = JobStatus.PROCESSING.value
        self.attempts += 1
        self.updated_at = datetime.now(timezone.utc)

    def mark_complete(
        self,
        result_id: int | None = None,
        result_type: str | None = None,
    ) -> None:
        """Mark job as complete with optional result linking."""
        self.status = JobStatus.COMPLETE.value
        self.result_id = result_id
        self.result_type = result_type
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self, error_message: str) -> None:
        """Mark job as failed with error message."""
        self.status = JobStatus.FAILED.value
        self.error_message = error_message
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class PendingJobPayload(BaseModel):
    """Pydantic model for API responses."""

    id: int
    job_type: str
    external_id: str | None
    status: str
    error_message: str | None
    result_id: int | None
    result_type: str | None
    params: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    attempts: int

    model_config = {"from_attributes": True}
