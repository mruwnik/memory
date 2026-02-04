"""
Database models for Claude Code config snapshots.

Stores config snapshots for running Claude Code in containers.
"""

from __future__ import annotations
from datetime import datetime
from typing import Annotated, TypedDict, TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.sql import func

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class ClaudeConfigSnapshotPayload(TypedDict):
    id: Annotated[int, "Snapshot ID"]
    name: Annotated[str, "Snapshot name"]
    content_hash: Annotated[str, "SHA256 hash of snapshot content"]
    claude_account_email: Annotated[str | None, "Claude account email"]
    subscription_type: Annotated[str | None, "Claude subscription type"]
    summary: Annotated[str | None, "JSON summary of snapshot contents"]
    filename: Annotated[str, "Storage filename"]
    size: Annotated[int, "Size in bytes"]
    created_at: Annotated[str | None, "ISO timestamp of creation"]


class ClaudeConfigSnapshot(Base):
    """
    A snapshot of Claude Code configuration.

    Stores credentials, MCP server configs, skills, agents, plugins, etc.
    for running Claude Code in containers.
    """

    __tablename__ = "claude_config_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # User who owns this snapshot
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user: Mapped[User] = relationship("User", backref="claude_snapshots", lazy="select")

    # Snapshot metadata
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # Claude account info (extracted from .credentials.json)
    claude_account_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_type: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Summary for UI (JSON)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Storage
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="unique_snapshot_hash"),
        Index("idx_snapshots_user", "user_id"),
        Index("idx_snapshots_hash", "content_hash"),
    )

    def as_payload(self) -> ClaudeConfigSnapshotPayload:
        return ClaudeConfigSnapshotPayload(
            id=self.id,
            name=self.name,
            content_hash=self.content_hash,
            claude_account_email=self.claude_account_email,
            subscription_type=self.subscription_type,
            summary=self.summary,
            filename=self.filename,
            size=self.size,
            created_at=self.created_at.isoformat() if self.created_at else None,
        )


class ClaudeEnvironmentPayload(TypedDict):
    id: Annotated[int, "Environment ID"]
    name: Annotated[str, "Environment name"]
    volume_name: Annotated[str, "Docker volume name"]
    description: Annotated[str | None, "User description"]
    initialized_from_snapshot_id: Annotated[int | None, "Snapshot used for initialization"]
    cloned_from_environment_id: Annotated[int | None, "Environment cloned from"]
    size_bytes: Annotated[int | None, "Last known size in bytes"]
    last_used_at: Annotated[str | None, "ISO timestamp of last use"]
    created_at: Annotated[str | None, "ISO timestamp of creation"]
    session_count: Annotated[int, "Number of sessions that have used this environment"]


class ClaudeEnvironment(Base):
    """
    A persistent environment for Claude Code sessions.

    Unlike snapshots (immutable config tarballs), environments are Docker volumes
    that persist state across container restarts. Multiple sessions can use the
    same environment, accumulating changes over time.
    """

    __tablename__ = "claude_environments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # User who owns this environment
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user: Mapped[User] = relationship("User", backref="claude_environments", lazy="select")

    # Environment metadata
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # Initialization source (optional - tracks what snapshot was used to init)
    initialized_from_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("claude_config_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    initialized_from_snapshot: Mapped[ClaudeConfigSnapshot | None] = relationship("ClaudeConfigSnapshot", lazy="select")

    # Clone source (optional - tracks what environment was cloned from)
    cloned_from_environment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("claude_environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    cloned_from_environment: Mapped[ClaudeEnvironment | None] = relationship(
        "ClaudeEnvironment", remote_side="ClaudeEnvironment.id", lazy="select"
    )

    # Usage tracking
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("volume_name", name="unique_environment_volume"),
        Index("idx_environments_user", "user_id"),
        # Note: No explicit index on volume_name needed - the unique constraint creates one
    )

    def as_payload(self) -> ClaudeEnvironmentPayload:
        return ClaudeEnvironmentPayload(
            id=self.id,
            name=self.name,
            volume_name=self.volume_name,
            description=self.description,
            initialized_from_snapshot_id=self.initialized_from_snapshot_id,
            cloned_from_environment_id=self.cloned_from_environment_id,
            size_bytes=self.size_bytes,
            last_used_at=self.last_used_at.isoformat() if self.last_used_at else None,
            created_at=self.created_at.isoformat() if self.created_at else None,
            session_count=self.session_count,
        )
