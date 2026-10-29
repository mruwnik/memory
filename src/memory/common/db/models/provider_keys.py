from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from memory.common.db.models.base import Base
from memory.common.encryption import decrypt_value, encrypt_value

KEY_PROVIDERS = ("anthropic", "openai")


class KeyStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class LLMKeyChoice:
    """Which provider key pays for LLM work on a source's items.

    Unset (no key, not disabled): the default key of whoever brought each item in.
    """

    @declared_attr
    def llm_key_id(cls) -> Mapped[int | None]:
        return mapped_column(
            Integer, ForeignKey("llm_provider_keys.id", ondelete="SET NULL"), nullable=True
        )

    llm_disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    @property
    def llm_key_holder(self) -> "LLMKeyChoice":
        return self

    @property
    def content_owner_id(self) -> int | None:
        return None

    @property
    def llm_registrant_id(self) -> int | None:
        """Who pays by default for items that have no creator."""
        return None


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
    is_system_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=KeyStatus.OK.value, server_default=KeyStatus.OK.value
    )
    status_detail: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_cost_usd_per_day: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    # Fewer summary requests per batch than the provider's usual, since
    # the provider refused a bigger one for its queue limit
    summary_batch_limit: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "llm_provider_keys_one_system_default",
            "provider",
            unique=True,
            postgresql_where=text("is_system_default"),
        ),
        UniqueConstraint("user_id", "label", name="llm_provider_keys_user_label"),
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
        # The new secret may belong to another project, with another queue limit
        self.summary_batch_limit = None

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "label": self.label,
            "key_hint": self.key_hint,
            "is_default": self.is_default,
            "is_system_default": self.is_system_default,
            "status": self.status,
            "status_detail": self.status_detail,
            "last_used_at": iso(self.last_used_at),
            "max_cost_usd_per_day": (
                None if self.max_cost_usd_per_day is None else str(self.max_cost_usd_per_day)
            ),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class LLMSpend(Base):
    """What a provider key spent on calls kept without a run row, per user it was spent
    for, agent, model and hour.

    Hourly buckets keep the search and ingest hot paths to one upsert per call
    while the daily caps can still sum a rolling 24 hours. A deleted key or user
    leaves its buckets as history.
    """

    __tablename__ = "llm_spend"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("llm_provider_keys.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    # "run": purged agent runs folded in, which their caps already counted as runs
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="memory", server_default="memory")
    # NULL only on buckets from before models were recorded
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        # Keyed buckets only: buckets of deleted keys may collide once their key is NULL
        Index(
            "llm_spend_bucket",
            "provider_key_id",
            "user_id",
            "agent",
            "model",
            "hour",
            "kind",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("provider_key_id IS NOT NULL"),
        ),
        Index("llm_spend_hour", "hour"),
        CheckConstraint("kind IN ('memory', 'run')", name="llm_spend_kind"),
    )
