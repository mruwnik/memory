"""
Database models for coding session storage.

Stores coding sessions and projects for analysis and retrieval.
Session messages are stored as JSONL files on disk.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Annotated, TypedDict, TYPE_CHECKING
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, Mapped, mapped_column

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class ProjectPayload(TypedDict):
    id: Annotated[int, "Project ID"]
    directory: Annotated[str, "Project directory path"]
    name: Annotated[str | None, "Optional friendly name"]
    source: Annotated[str | None, "Source identifier (hostname, IP, etc)"]
    created_at: Annotated[str | None, "ISO timestamp of creation"]
    last_accessed_at: Annotated[str | None, "ISO timestamp of last access"]
    session_count: Annotated[int, "Number of sessions"]


class SessionPayload(TypedDict):
    session_id: Annotated[str, "Session UUID (primary key)"]
    project_id: Annotated[int | None, "Associated project ID"]
    parent_session_id: Annotated[str | None, "Parent session UUID (for subagents)"]
    git_branch: Annotated[str | None, "Git branch name"]
    tool_version: Annotated[str | None, "Tool version (e.g., Claude Code version)"]
    source: Annotated[str | None, "Source identifier (hostname, IP, etc)"]
    started_at: Annotated[str | None, "ISO timestamp when session started"]
    ended_at: Annotated[str | None, "ISO timestamp when session ended"]
    transcript_path: Annotated[str | None, "Path to JSONL transcript file"]


class Project(Base):
    """
    A coding project, corresponding to a working directory.

    Projects group sessions that were run in the same directory.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # User who owns this project
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped[User] = relationship("User", lazy="select")

    # Directory information
    directory: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Optional friendly name
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Source identifier (hostname, IP)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    sessions: Mapped[list[Session]] = relationship(
        "Session", back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "directory", name="unique_user_project"),
        Index("idx_projects_user", "user_id"),
        Index("idx_projects_directory", "directory"),
        Index("idx_projects_source", "source"),
    )

    def as_payload(self) -> ProjectPayload:
        return ProjectPayload(
            id=self.id,
            directory=self.directory,
            name=self.name,
            source=self.source,
            created_at=self.created_at.isoformat() if self.created_at else None,
            last_accessed_at=(
                self.last_accessed_at.isoformat() if self.last_accessed_at else None
            ),
            session_count=len(self.sessions) if self.sessions else 0,
        )


class Session(Base):
    """
    A coding session.

    Sessions are individual conversation threads.
    They may belong to a project and can have parent sessions (for subagents).
    Messages are stored as JSONL files on disk, not in the database.
    """

    __tablename__ = "sessions"

    # Use the session UUID as the primary key
    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    # User who owns this session
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped[User] = relationship("User", lazy="select")

    # Optional project association
    project_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project: Mapped[Project | None] = relationship("Project", back_populates="sessions")

    # Parent session (for subagents) - references by UUID
    parent_session_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_session: Mapped[Session | None] = relationship(
        "Session", remote_side="Session.id", backref="child_sessions"
    )

    # Session metadata
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g., Claude Code version
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Source identifier (hostname, IP)

    # Path to JSONL transcript file (relative to storage dir)
    transcript_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_sessions_user", "user_id"),
        Index("idx_sessions_project", "project_id"),
        Index("idx_sessions_parent", "parent_session_id"),
        Index("idx_sessions_started", "started_at"),
        Index("idx_sessions_ended", "ended_at"),
        Index("idx_sessions_source", "source"),
    )

    def as_payload(self) -> SessionPayload:
        return SessionPayload(
            session_id=str(self.id),
            project_id=self.project_id,
            parent_session_id=str(self.parent_session_id) if self.parent_session_id else None,
            git_branch=self.git_branch,
            tool_version=self.tool_version,
            source=self.source,
            started_at=self.started_at.isoformat() if self.started_at else None,
            ended_at=self.ended_at.isoformat() if self.ended_at else None,
            transcript_path=self.transcript_path,
        )
