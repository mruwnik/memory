"""
Database models for Claude Code config snapshots.

Stores config snapshots for running Claude Code in containers.
"""

from typing import Annotated, TypedDict

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from memory.common.db.models.base import Base


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

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # User who owns this snapshot
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    user = relationship("User", backref="claude_snapshots", lazy="select")

    # Snapshot metadata
    name = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=False, unique=True)

    # Claude account info (extracted from .credentials.json)
    claude_account_email = Column(Text, nullable=True)
    subscription_type = Column(Text, nullable=True)

    # Summary for UI (JSON)
    summary = Column(Text, nullable=True)

    # Storage
    filename = Column(Text, nullable=False)
    size = Column(Integer, nullable=False)

    created_at = Column(
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

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # User who owns this environment
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    user = relationship("User", backref="claude_environments", lazy="select")

    # Environment metadata
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    volume_name = Column(Text, nullable=False, unique=True)

    # Initialization source (optional - tracks what snapshot was used to init)
    initialized_from_snapshot_id = Column(
        BigInteger,
        ForeignKey("claude_config_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    initialized_from_snapshot = relationship("ClaudeConfigSnapshot", lazy="select")

    # Usage tracking
    size_bytes = Column(BigInteger, nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    session_count = Column(Integer, default=0, nullable=False)

    created_at = Column(
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
            size_bytes=self.size_bytes,
            last_used_at=self.last_used_at.isoformat() if self.last_used_at else None,
            created_at=self.created_at.isoformat() if self.created_at else None,
            session_count=self.session_count,
        )
