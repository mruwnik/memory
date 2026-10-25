from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, ForeignKey, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from memory.common.db.models.base import Base


class InstanceSetting(Base):
    """An admin's value for a registered instance setting; the env still wins over it.

    Secrets are write-only: only `key_hint` is ever returned.
    """

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_hint: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
