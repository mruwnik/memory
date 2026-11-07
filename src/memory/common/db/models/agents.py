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
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB
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


EXECUTOR_SERVER = "server"
EXECUTOR_WORKERS = "workers"

# The longest an agent may keep its runs; also clamps older snapshots in the purge.
MAX_RETENTION_DAYS = 3650

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
    # The prebuilt or system agent's slug. With definition_id also None, an inline
    # spec, which only the snapshot in `spec` keeps.
    agent: Mapped[str | None] = mapped_column(Text)
    # A queue delivery's queue: only claims naming it take the run.
    queue: Mapped[str | None] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Where the run executes: EXECUTOR_SERVER (the agents worker) or EXECUTOR_WORKERS
    # (its owner's agents:work credentials claim it); a placement fallback moves it.
    executor: Mapped[str] = mapped_column(
        String(64), nullable=False, default=EXECUTOR_SERVER, server_default=EXECUTOR_SERVER
    )
    # Who holds (or last held) the run's worker lease: always the run's owner.
    worker_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    worker_label: Mapped[str | None] = mapped_column(Text)
    # Kept after a lapse, so a late worker can still deliver until someone else claims.
    lease_token: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_lapses: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # What the worker says it spent: shown, never counted against any cap.
    worker_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # What the worker says it posted ([{source, external_id}]), which its trigger never matches.
    posted: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
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
    trigger_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_triggers.id", ondelete="SET NULL")
    )
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
    # When the run was put on its current executor: a placement fallback resets it, so
    # waiting on the new side is timed from the move.
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        Index("agent_runs_parent_idx", "parent_run_id"),
        Index("agent_runs_finished_idx", "finished_at"),
        Index(
            "agent_runs_workers_queue_idx",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending' AND executor = 'workers'"),
        ),
        Index(
            "agent_runs_queue_pending_idx",
            "user_id",
            "queue",
            "created_at",
            postgresql_where=text("queue IS NOT NULL AND status = 'pending'"),
        ),
        Index("agent_runs_worker_user_idx", "worker_user_id"),
        # Runs without a trigger (cron) still dedupe per definition.
        Index(
            "agent_runs_definition_dedupe_key",
            "definition_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND trigger_id IS NULL"),
        ),
        Index(
            "agent_runs_trigger_dedupe_key",
            "trigger_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND trigger_id IS NOT NULL"),
        ),
        Index(
            "agent_runs_trigger_created_idx",
            "trigger_id",
            "created_at",
            postgresql_where=text("trigger_id IS NOT NULL"),
        ),
    )

    def serialize(self, include_spec: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "definition_id": self.definition_id,
            "agent": self.agent,
            "queue": self.queue,
            "executor": self.executor,
            "worker_user_id": self.worker_user_id,
            "worker_label": self.worker_label,
            "worker_usage": self.worker_usage,
            "posted": self.posted,
            "lease_expires_at": iso(self.lease_expires_at),
            "lease_lapses": self.lease_lapses,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
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
            "trigger_id": self.trigger_id,
            "starred": self.starred,
            "provider_key_id": self.provider_key_id,
            "model": self.spec.get("model"),
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
        }
        return data | ({"spec": self.spec} if include_spec else {})


class AgentTrigger(Base):
    """Hands what matches its source (`kind` and `config`) to its handler.

    `config` is validated per kind by memory.agents.triggers. `state` holds what
    the trigger machinery records, e.g. why a circuit breaker disabled it.
    `handler` is {"agent": <definition id>} (then also in definition_id, which
    cascades its deletion), {"agent": "<code agent slug>"}, {"queue": "<name>"},
    whose deliveries the owner's workers claim (memory.agents.queues), or
    {"notify": {...}} (memory.agents.clock.Notify), which only cron subscriptions
    take.

    A cron subscription's `next_at` is its next tick, in its owner's timezone.
    """

    __tablename__ = "agent_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    definition_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_definitions.id", ondelete="CASCADE")
    )
    handler: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Shaping (memory.agents.triggers.claim and memory.agents.queues).
    coalesce_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="none", server_default="none"
    )
    order_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="none", server_default="none"
    )
    # Unset: AGENT_MAX_RUNS_PER_HOUR.
    max_per_hour: Mapped[int | None] = mapped_column(Integer)
    backlog_max: Mapped[int | None] = mapped_column(Integer)
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
    # Set on creation and on every re-enable (to the trip time on a resume): items
    # stored before it match only once their sync reports a change, and events
    # written before it never do.
    active_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    definition: Mapped[AgentDefinition | None] = relationship("AgentDefinition")

    __table_args__ = (
        CheckConstraint("kind IN ('ingest', 'cron')", name="agent_triggers_kind_check"),
        UniqueConstraint("user_id", "name", name="agent_triggers_user_name_key"),
        CheckConstraint(
            "CASE WHEN definition_id IS NOT NULL "
            "THEN handler = jsonb_build_object('agent', definition_id) "
            "ELSE ((jsonb_typeof(handler->'agent') = 'string' AND handler->>'agent' <> '' "
            "AND handler - 'agent' = '{}'::jsonb) "
            "OR (jsonb_typeof(handler->'queue') = 'string' AND handler->>'queue' <> '' "
            "AND handler - 'queue' = '{}'::jsonb) "
            "OR (jsonb_typeof(handler->'notify') = 'object' "
            "AND handler - 'notify' = '{}'::jsonb)) IS TRUE END",
            name="agent_triggers_handler_check",
        ),
        CheckConstraint(
            "coalesce_mode IN ('none', 'latest')", name="agent_triggers_coalesce_mode_check"
        ),
        CheckConstraint(
            "order_by IN ('none', 'item', 'thread')", name="agent_triggers_order_by_check"
        ),
        CheckConstraint("max_per_hour > 0", name="agent_triggers_max_per_hour_check"),
        CheckConstraint("backlog_max > 0", name="agent_triggers_backlog_max_check"),
        Index("agent_triggers_definition_idx", "definition_id"),
        Index(
            "agent_triggers_enabled_kind_idx",
            "kind",
            postgresql_where=text("enabled"),
        ),
        Index(
            "agent_triggers_cron_due_idx",
            "next_at",
            postgresql_where=text("kind = 'cron' AND enabled"),
        ),
    )

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "definition_id": self.definition_id,
            "handler": self.handler,
            "coalesce_mode": self.coalesce_mode,
            "order_by": self.order_by,
            "max_per_hour": self.max_per_hour,
            "backlog_max": self.backlog_max,
            "kind": self.kind,
            "config": self.config,
            "enabled": self.enabled,
            "state": self.state,
            "api_key_id": self.api_key_id,
            "via_api_key": self.via_api_key,
            "active_since": iso(self.active_since),
            "next_at": iso(self.next_at),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class AgentTriggerMatch(Base):
    """A version of an item a trigger has matched, and its delivery: written by the
    router with the event it routes, so a match is never lost between routing and
    the run. Reprocessing never matches again: without on_edit the first version
    claims the item, with it each version (memory.agents.triggers.content_version)
    matches once.

    `status` is pending until the delivery ends: done (run_id is its run), failed,
    expired or skipped:<reason> (memory.agents.triggers).
    """

    __tablename__ = "agent_trigger_matches"

    trigger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_triggers.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # The trigger had matched an earlier version of the item.
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # The Change tags delivered for this version, sorted.
    changes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    # What the trigger's order_by orders it by (memory.agents.triggers.order_key).
    order_key: Mapped[str | None] = mapped_column(Text)
    # Items whose pending deliveries coalescing folded into this one, once per delivery.
    coalesced: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default="{}"
    )
    # Deferred by a cap; one still over it when this passes is shed.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last handed to FIRE_TRIGGER; the sweep resends it if it's still pending.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Times sent since it was routed or deferred, which spaces out its resends.
    sends: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[AgentRun | None] = relationship("AgentRun")

    __table_args__ = (
        Index(
            "agent_trigger_matches_run_idx", "run_id", postgresql_where=text("run_id IS NOT NULL")
        ),
        CheckConstraint(
            "status IN ('pending', 'done', 'failed', 'expired') OR status LIKE 'skipped:%'",
            name="agent_trigger_matches_status_check",
        ),
        Index("agent_trigger_matches_item_idx", "item_id"),
        Index("agent_trigger_matches_created_idx", "created_at"),
        Index(
            "agent_trigger_matches_pending_idx",
            "trigger_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "agent_trigger_matches_order_idx",
            "trigger_id",
            "order_key",
            "created_at",
            postgresql_where=text("order_key IS NOT NULL"),
        ),
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
