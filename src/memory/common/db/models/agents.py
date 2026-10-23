import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from memory.common.db.models.base import Base


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {RunStatus.DONE, RunStatus.FAILED, RunStatus.BUDGET_EXCEEDED, RunStatus.CANCELLED}
)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="agent_definitions_user_name_key"),
    )

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "spec": self.spec,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_definitions.id", ondelete="SET NULL")
    )
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RunStatus.PENDING.value,
        server_default=RunStatus.PENDING.value,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal(0), server_default="0"
    )
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    num_turns: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    trigger_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    api_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    # The principal's effective scopes when enqueued: the run never re-derives
    # them, so a key deleted or edited later can't widen what it may call.
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','done','failed','budget_exceeded','cancelled')",
            name="agent_runs_status_check",
        ),
        Index("agent_runs_user_created_idx", "user_id", text("created_at DESC")),
        Index(
            "agent_runs_definition_created_idx",
            "definition_id",
            text("created_at DESC"),
        ),
        Index(
            "agent_runs_active_idx",
            "status",
            postgresql_where=text("status IN ('pending','running')"),
        ),
        Index("agent_runs_api_key_created_idx", "api_key_id", "created_at"),
        Index(
            "agent_runs_definition_dedupe_key",
            "definition_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    def serialize(self, include_spec: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "definition_id": self.definition_id,
            "input": self.input,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "usage": self.usage,
            "cost_usd": money(self.cost_usd),
            "max_cost_usd": money(self.max_cost_usd),
            "num_turns": self.num_turns,
            "trigger_kind": self.trigger_kind,
            "trigger_ref": self.trigger_ref,
            "starred": self.starred,
            "model": self.spec.get("model"),
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "steps_purged_at": iso(self.steps_purged_at),
        }
        return data | ({"spec": self.spec} if include_spec else {})


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    replay: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="agent_run_steps_run_seq_key"),
        CheckConstraint(
            "kind IN ('user','assistant','tool_results','notice')",
            name="agent_run_steps_kind_check",
        ),
    )

    def serialize(self) -> dict[str, Any]:
        # replay is opaque provider data for resending within a run; never exposed.
        return {
            "seq": self.seq,
            "kind": self.kind,
            "content": self.content,
            "usage": self.usage,
            "cost_usd": money(self.cost_usd),
            "created_at": iso(self.created_at),
        }
