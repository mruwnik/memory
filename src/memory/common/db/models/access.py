"""
Database models for access control.

This module provides:
- AccessLog: Audit log for access control events

Note: Projects are defined in the Project model in sources.py.
Team-based access control is managed via team_members and project_teams junction tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, TypedDict

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.scoping import scoped_session
from sqlalchemy.orm.session import Session
from sqlalchemy.sql import func

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class AccessLogPayload(TypedDict):
    """Serialized representation of an AccessLog entry."""

    id: Annotated[int, "Log entry ID"]
    user_id: Annotated[int, "User who performed the action"]
    action: Annotated[str, "Action type: search, view_item, create, update"]
    query: Annotated[str | None, "Search query if applicable"]
    item_id: Annotated[int | None, "SourceItem ID if applicable"]
    result_count: Annotated[int | None, "Number of results returned"]
    timestamp: Annotated[str, "ISO timestamp"]


class AccessLog(Base):
    """
    Audit log for access control events.

    Records searches, item views, and modifications for audit purposes.
    All access is logged, including superadmin access.
    """

    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship
    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        Index("idx_access_logs_user_time", "user_id", "timestamp"),
        Index("idx_access_logs_item", "item_id", postgresql_where="item_id IS NOT NULL"),
        Index("idx_access_logs_time", "timestamp"),
    )

    def as_payload(self) -> AccessLogPayload:
        return AccessLogPayload(
            id=self.id,
            user_id=self.user_id,
            action=self.action,
            query=self.query,
            item_id=self.item_id,
            result_count=self.result_count,
            timestamp=self.timestamp.isoformat() if self.timestamp else "",
        )

    def __repr__(self) -> str:
        return f"<AccessLog(user_id={self.user_id}, action={self.action!r}, timestamp={self.timestamp})>"


def log_access(
    db: Session | scoped_session[Session],
    user_id: int,
    action: str,
    query: str | None = None,
    item_id: int | None = None,
    result_count: int | None = None,
) -> AccessLog:
    """
    Log an access event for audit purposes.

    Args:
        db: Database session
        user_id: ID of the user performing the action
        action: Type of action (search, view_item, create, update)
        query: Search query if applicable
        item_id: SourceItem ID if applicable
        result_count: Number of results if applicable

    Returns:
        The created AccessLog entry
    """
    log = AccessLog(
        user_id=user_id,
        action=action,
        query=query,
        item_id=item_id,
        result_count=result_count,
    )
    db.add(log)
    # Don't commit here - let the caller manage the transaction
    return log
