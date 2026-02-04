"""Journal entries for various entities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, or_
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.access_control import has_admin_scope
from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User

# Valid target types for journal entries
TargetType = Literal["source_item", "project", "team", "poll"]
VALID_TARGET_TYPES: set[str] = {"source_item", "project", "team", "poll"}


class JournalEntry(Base):
    """A journal entry attached to an entity.

    Journal entries are append-only notes that accumulate over time.
    Supports attaching to: SourceItems, Projects, Teams, Polls.

    Visibility modes:
    - private=False (default): visible to anyone who can see target entity
    - private=True: only creator (and admins) can see
    """

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Polymorphic target: target_type + target_id identify the entity
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    creator_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # project_id for access control (inherited from target or set directly)
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[creator_id])

    __table_args__ = (
        Index("journal_entries_target_idx", "target_type", "target_id"),
        Index("journal_entries_creator_idx", "creator_id"),
        Index("journal_entries_project_idx", "project_id"),
        Index("journal_entries_created_idx", "created_at"),
        CheckConstraint(
            "target_type IN ('source_item', 'project', 'team', 'poll')",
            name="journal_entries_target_type_check",
        ),
    )

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "creator_id": self.creator_id,
            "project_id": self.project_id,
            "content": self.content,
            "private": self.private,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def user_can_access_journal_entry(user: Any, entry: JournalEntry) -> bool:
    """Check if user can access a journal entry."""
    if has_admin_scope(user):
        return True
    if entry.private:
        return entry.creator_id is not None and entry.creator_id == getattr(user, "id", None)
    return True  # Shared entries - caller verifies target access


def build_journal_access_filter(user: Any, user_id: int | None):
    """Build SQLAlchemy filter for journal entry access.

    Returns a filter clause that can be applied to JournalEntry queries.
    """
    if has_admin_scope(user):
        return True  # No filter needed
    return or_(
        JournalEntry.private == False,  # noqa: E712
        JournalEntry.creator_id == user_id,
    )
