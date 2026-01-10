"""
Database models for availability polls (LettuceMeet-style meeting scheduling).
"""

import secrets
import string
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
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


def generate_slug(length: int = 12) -> str:
    """Generate a random URL-safe slug (12 chars = 62^12 ≈ 3×10^21 combinations)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class PollStatus(str, Enum):
    """Status values for availability polls."""

    OPEN = "open"
    CLOSED = "closed"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"


class AvailabilityLevel(int, Enum):
    """Availability level for a time slot."""

    AVAILABLE = 1
    IF_NEEDED = 2


class AvailabilityPoll(Base):
    """
    An availability poll for scheduling meetings.

    Users create polls with date ranges and time configurations.
    Respondents can submit their availability without authentication.
    """

    __tablename__ = "availability_polls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    slug = Column(String(20), unique=True, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=PollStatus.OPEN.value)

    # Poll time window (stored in UTC - clients handle timezone conversion)
    datetime_start = Column(DateTime(timezone=True), nullable=False)
    datetime_end = Column(DateTime(timezone=True), nullable=False)
    slot_duration_minutes = Column(Integer, nullable=False, default=30)

    # Creator (optional - polls can be created anonymously via MCP)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    closes_at = Column(DateTime(timezone=True), nullable=True)
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    finalized_time = Column(DateTime(timezone=True), nullable=True)  # Selected meeting time

    # Relationships
    user = relationship("User", backref="availability_polls")
    responses = relationship(
        "PollResponse",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        # slug already indexed via unique=True constraint
        Index("idx_polls_user_id", "user_id"),
        Index("idx_polls_status", "status"),
        Index("idx_polls_created_at", "created_at"),
    )

    def __init__(self, **kwargs: Any) -> None:
        if "slug" not in kwargs:
            kwargs["slug"] = generate_slug()
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<AvailabilityPoll(id={self.id}, slug={self.slug}, title={self.title})>"

    @property
    def response_count(self) -> int:
        return len(self.responses) if self.responses else 0

    @property
    def is_open(self) -> bool:
        if self.status != PollStatus.OPEN.value:
            return False
        if self.closes_at:
            # Defensively handle naive datetimes
            closes_at = self.closes_at
            if closes_at.tzinfo is None:
                closes_at = closes_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > closes_at:
                return False
        return True


class PollResponse(Base):
    """
    A respondent's availability submission for a poll.

    Respondents are identified by name (optional) and can update their responses.
    """

    __tablename__ = "poll_responses"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    poll_id = Column(
        BigInteger,
        ForeignKey("availability_polls.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Respondent identification (anonymous allowed)
    respondent_name = Column(String(255), nullable=True)
    respondent_email = Column(String(255), nullable=True)
    person_id = Column(
        BigInteger,
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Edit token for updating responses without auth
    edit_token = Column(String(32), nullable=False)

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

    # Relationships
    poll = relationship("AvailabilityPoll", back_populates="responses")
    person = relationship("Person", backref="poll_responses")
    availabilities = relationship(
        "PollAvailability",
        back_populates="response",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_poll_responses_poll_id", "poll_id"),
        Index("idx_poll_responses_edit_token", "edit_token"),
        Index("idx_poll_responses_person_id", "person_id"),
    )

    def __init__(self, **kwargs: Any) -> None:
        if "edit_token" not in kwargs:
            kwargs["edit_token"] = secrets.token_hex(16)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        name = self.respondent_name or "Anonymous"
        return f"<PollResponse(id={self.id}, poll_id={self.poll_id}, name={name})>"


class PollAvailability(Base):
    """
    A single time slot selection within a poll response.

    Each record represents one time slot that the respondent marked as available.
    """

    __tablename__ = "poll_availabilities"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    response_id = Column(
        BigInteger,
        ForeignKey("poll_responses.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Time slot (stored in UTC)
    slot_start = Column(DateTime(timezone=True), nullable=False)
    slot_end = Column(DateTime(timezone=True), nullable=False)

    # Availability level: 1 = available, 2 = if needed
    availability_level = Column(
        Integer,
        nullable=False,
        default=AvailabilityLevel.AVAILABLE.value,
    )

    # Relationships
    response = relationship("PollResponse", back_populates="availabilities")

    __table_args__ = (
        Index("idx_poll_availability_response_id", "response_id"),
        Index("idx_poll_availability_slots", "slot_start", "slot_end"),
    )

    def __repr__(self) -> str:
        return f"<PollAvailability(id={self.id}, start={self.slot_start}, level={self.availability_level})>"


# Pydantic models for API responses


class PollAvailabilityPayload(BaseModel):
    """API response for a single availability slot."""

    slot_start: datetime
    slot_end: datetime
    availability_level: int

    model_config = {"from_attributes": True}


class PollResponsePayload(BaseModel):
    """API response for a poll response."""

    id: int
    respondent_name: str | None
    respondent_email: str | None
    person_id: int | None
    availabilities: list[PollAvailabilityPayload]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AvailabilityPollPayload(BaseModel):
    """API response for an availability poll."""

    id: int
    slug: str
    title: str
    description: str | None
    status: str
    datetime_start: datetime  # UTC
    datetime_end: datetime  # UTC
    slot_duration_minutes: int
    response_count: int
    created_at: datetime
    closes_at: datetime | None
    finalized_at: datetime | None
    finalized_time: datetime | None

    model_config = {"from_attributes": True}


class AvailabilityPollDetailPayload(AvailabilityPollPayload):
    """API response for poll details including responses."""

    responses: list[PollResponsePayload]


class SlotAggregation(BaseModel):
    """Aggregated availability data for a time slot."""

    slot_start: datetime
    slot_end: datetime
    available_count: int  # Number of respondents who marked "available"
    if_needed_count: int  # Number who marked "if needed"
    total_count: int  # Total responses
    respondents: list[str]  # Names of respondents who are available
