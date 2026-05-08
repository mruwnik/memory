"""First-class date-anchored bundles ("Deadlines").

A Deadline anchors a single prepare-by date and aggregates SourceItems
(tickets, drafts, references) into one bundle. Distinct from Task (which
answers *what to do*) and from CalendarEvent (imported from external
calendars). Solves the "things fall through the cracks" problem by giving
the system a unit it can surface as the date approaches.
"""

from __future__ import annotations

from datetime import date as date_type, datetime
from typing import TYPE_CHECKING, Annotated, TypedDict

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base
from memory.common.db.models.source_item import AccessControlMixin

if TYPE_CHECKING:
    from memory.common.db.models.source_item import SourceItem


# Junction: many Deadlines <-> many SourceItems
deadline_attachments = Table(
    "deadline_attachments",
    Base.metadata,
    Column(
        "deadline_id",
        BigInteger,
        ForeignKey("deadlines.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "source_item_id",
        BigInteger,
        ForeignKey("source_item.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "attached_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Index("deadline_attachments_deadline_idx", "deadline_id"),
    Index("deadline_attachments_source_idx", "source_item_id"),
)


class DeadlinePayload(TypedDict):
    id: Annotated[int, "Deadline ID"]
    title: Annotated[str, "Deadline title"]
    date: Annotated[str, "Prepare-by date in ISO format (YYYY-MM-DD)"]
    description: Annotated[str | None, "Free-text notes / context"]
    priority: Annotated[str | None, "Priority level: low, medium, high, urgent"]
    owner_id: Annotated[int | None, "Person responsible (people.id)"]
    project_id: Annotated[int | None, "Project ID for access control"]
    sensitivity: Annotated[
        str, "Sensitivity: public, basic, internal, confidential"
    ]
    creator_id: Annotated[int | None, "User who filed the deadline"]
    tags: Annotated[list[str], "Categorization tags"]
    attachment_ids: Annotated[list[int], "IDs of attached SourceItems"]


class Deadline(AccessControlMixin, Base):
    """A date you must be ready by, plus its surrounding context.

    Conceptually orthogonal to Task: a Task answers *what to do*, a Deadline
    answers *when to be ready*. Tasks may be attached to Deadlines (Tasks are
    SourceItems), but they don't replace each other.
    """

    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    attachments: Mapped[list["SourceItem"]] = relationship(
        "SourceItem",
        secondary=deadline_attachments,
        lazy="select",
    )

    __table_args__ = (
        CheckConstraint(
            "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
            name="deadline_valid_sensitivity_level",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('low', 'medium', 'high', 'urgent')",
            name="deadline_priority_check",
        ),
        Index("deadline_date_idx", "date"),
        Index("deadline_priority_idx", "priority"),
        Index("deadline_owner_idx", "owner_id"),
        Index("deadline_project_idx", "project_id"),
        Index("deadline_sensitivity_idx", "sensitivity"),
        Index("deadline_creator_idx", "creator_id"),
        Index("deadline_tags_idx", "tags", postgresql_using="gin"),
    )

    def as_payload(self) -> DeadlinePayload:
        return DeadlinePayload(
            id=self.id,
            title=self.title,
            date=self.date.isoformat(),
            description=self.description,
            priority=self.priority,
            owner_id=self.owner_id,
            project_id=self.project_id,
            sensitivity=self.sensitivity or "basic",
            creator_id=self.creator_id,
            tags=list(self.tags or []),
            attachment_ids=[a.id for a in self.attachments],
        )

    def __repr__(self) -> str:
        return f"<Deadline id={self.id} title={self.title!r} date={self.date}>"
