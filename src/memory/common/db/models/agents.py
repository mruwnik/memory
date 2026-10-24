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
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
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
    provider_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("llm_provider_keys.id", ondelete="SET NULL")
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
        Index("agent_runs_provider_key_created_idx", "provider_key_id", "created_at"),
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
            "provider_key_id": self.provider_key_id,
            "model": self.spec.get("model"),
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "steps_purged_at": iso(self.steps_purged_at),
        }
        return data | ({"spec": self.spec} if include_spec else {})


class AgentTrigger(Base):
    """Starts runs of a definition when something happens (phase 2: ingest filters).

    `config` is validated per kind by memory.agents.triggers. `state` holds what
    the trigger machinery records, e.g. why a circuit breaker disabled it.
    """

    __tablename__ = "agent_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # The key the trigger was set up through: its runs act with that key's scopes.
    api_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    # Survives the key's deletion (which nulls api_key_id), so a deleted key
    # stops the trigger instead of widening it to the owner's full access.
    via_api_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Set on creation and on every re-enable: items stored before it (including
    # while the trigger was paused) never match, however often they're reprocessed.
    active_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    definition: Mapped[AgentDefinition] = relationship("AgentDefinition")

    __table_args__ = (
        CheckConstraint("kind IN ('ingest')", name="agent_triggers_kind_check"),
        Index("agent_triggers_definition_idx", "definition_id"),
        Index(
            "agent_triggers_enabled_kind_idx",
            "kind",
            postgresql_where=text("enabled"),
        ),
    )

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "definition_id": self.definition_id,
            "kind": self.kind,
            "config": self.config,
            "enabled": self.enabled,
            "state": self.state,
            "api_key_id": self.api_key_id,
            "via_api_key": self.via_api_key,
            "active_since": iso(self.active_since),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class AgentTriggerMatch(Base):
    """A version of an item a trigger has matched, so reprocessing it never matches
    again: without on_edit the first version claims the item, with it each
    version (memory.agents.triggers.content_version) matches once."""

    __tablename__ = "agent_trigger_matches"

    trigger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_triggers.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("agent_trigger_matches_item_idx", "item_id"),
        Index("agent_trigger_matches_created_idx", "created_at"),
    )


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
