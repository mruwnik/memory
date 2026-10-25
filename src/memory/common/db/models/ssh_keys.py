"""Reusable SSH identities; NULL owner denotes an administrator-managed instance key."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from memory.common.db.models.base import Base


class SSHKey(Base):
    __tablename__ = "ssh_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotesSSHConfig(Base):
    __tablename__ = "notes_ssh_config"
    __table_args__ = (CheckConstraint("id = 1", name="notes_ssh_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ssh_key_id: Mapped[int] = mapped_column(
        ForeignKey("ssh_keys.id", ondelete="RESTRICT"), nullable=False
    )
    remote_url: Mapped[str] = mapped_column(Text, nullable=False)
    host_keys: Mapped[str] = mapped_column(Text, nullable=False)
