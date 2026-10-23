from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from memory.common.db.models.base import Base
from memory.common.db.models.secrets import decrypt_value, encrypt_value

KEY_PROVIDERS = ("anthropic", "openai")


class KeyStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class LLMProviderKey(Base):
    """A user's own API key for an LLM provider, used by their agent runs.

    The key itself is write-only: nothing serializes it, only its last 4 characters.
    """

    __tablename__ = "llm_provider_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_hint: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=KeyStatus.OK.value, server_default=KeyStatus.OK.value
    )
    status_detail: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_cost_usd_per_day: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "label", name="llm_provider_keys_user_provider_label"
        ),
        CheckConstraint(
            "provider IN ('anthropic', 'openai')", name="llm_provider_keys_provider"
        ),
        CheckConstraint("status IN ('ok', 'invalid')", name="llm_provider_keys_status"),
        Index(
            "llm_provider_keys_one_default",
            "user_id",
            "provider",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    @property
    def key(self) -> str:
        return decrypt_value(self.key_encrypted)

    @key.setter
    def key(self, value: str) -> None:
        self.key_encrypted = encrypt_value(value)
        self.key_hint = value[-4:]

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "label": self.label,
            "key_hint": self.key_hint,
            "is_default": self.is_default,
            "status": self.status,
            "status_detail": self.status_detail,
            "last_used_at": iso(self.last_used_at),
            "max_cost_usd_per_day": (
                None if self.max_cost_usd_per_day is None else str(self.max_cost_usd_per_day)
            ),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }
