import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.sql import func

from memory.common.db.models.base import Base
from memory.common.db.models.source_item import SourceItem

ITEM = "item"
RAW = "raw"
STORED, NOT_COLLECTED, NO_ITEM_TYPE, REJECTED = "stored", "not_collected", "no_item_type", "rejected"
# A cron subscription's tick, or a timer addressed straight to a handler.
TICK, TIMER = "tick", "timer"
TIME_KINDS = (TICK, TIMER)
ADDED, CHANGED, REMOVED = "added", "changed", "removed"
# A raw event's data key: an ingest task linking its item events was sent.
LINKS_ITEMS = "links_items"
# session.info key: ids of the items this transaction inserted.
INSERTED_ITEMS_KEY = "_inserted_source_items"
# The raw event behind the task running now; item events it writes link it.
CAUSING_EVENT: ContextVar[int | None] = ContextVar("causing_event", default=None)


class Event(Base):
    """Something that happened, written in the transaction that made it happen;
    pending until memory.agents.subscriptions.route_events routes it. Ticks and timers
    are pending until memory.agents.clock delivers them, from `fire_at` on; their
    `result` says how that went."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=ITEM)
    action: Mapped[str | None] = mapped_column(Text)
    # No foreign key: an item's removed event outlives the item.
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    # The ingester's Change tags; NULL when it didn't say what changed.
    changes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # Who made each change, where the ingester knows: {change: [author ids or logins]}.
    actors: Mapped[dict[str, list[str]] | None] = mapped_column(JSONB)
    backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Failed routing attempts; after settings.EVENT_MAX_ATTEMPTS the event is
    # routed anyway, with the last error's type.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    routed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A tick's cron subscription.
    subscription_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="CASCADE")
    )
    fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A timer's handler, shaped as Subscription.handler.
    handler: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # A tick's missed count; a timer's run input and the API key that set it.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # {"status": done | sent | failed | skipped:<reason>, ...}.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Last handed to the agents queue; resent while still undelivered.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Raw events: one webhook delivery, visible only to its source account's owner.
    source: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    # The id of the source's account row: GithubAccount, SlackApp, DiscordBot or TranscriptAccount.
    account_id: Mapped[int | None] = mapped_column(BigInteger)
    # The repo or channel the delivery is about, in the provider's ids.
    container: Mapped[str | None] = mapped_column(Text)
    delivery_id: Mapped[str | None] = mapped_column(Text)
    # Whose raw event, tick or timer it is.
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    outcome: Mapped[str | None] = mapped_column(Text)
    # Untrusted, and kept only by deliveries nothing stores (see record_raw_event),
    # until settings.EVENT_RAW_PAYLOAD_DAYS; deferred so routing never loads it.
    payload: Mapped[Any] = mapped_column(JSONB(none_as_null=True), deferred=True)
    # No foreign key: a job replayed after its raw event was purged still stores its items.
    caused_by: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("kind IN ('item', 'raw', 'tick', 'timer')", name="events_kind_check"),
        CheckConstraint(
            "kind <> 'item' OR (item_id IS NOT NULL "
            "AND action IN ('added', 'changed', 'removed'))",
            name="events_item_check",
        ),
        CheckConstraint(
            "outcome IN ('stored', 'not_collected', 'no_item_type', 'rejected')",
            name="events_outcome_check",
        ),
        CheckConstraint(
            "kind <> 'raw' OR (source IS NOT NULL AND type IS NOT NULL AND account_id IS NOT NULL "
            "AND delivery_id IS NOT NULL AND outcome IS NOT NULL)",
            name="events_raw_check",
        ),
        CheckConstraint(
            "kind <> 'tick' OR (subscription_id IS NOT NULL AND owner_id IS NOT NULL "
            "AND fire_at IS NOT NULL)",
            name="events_tick_check",
        ),
        CheckConstraint(
            "kind <> 'timer' OR (owner_id IS NOT NULL AND fire_at IS NOT NULL "
            "AND handler IS NOT NULL)",
            name="events_timer_check",
        ),
        Index("events_pending_idx", "id", postgresql_where=routed_at.is_(None)),
        Index("events_routed_at_idx", "routed_at"),
        Index(
            "events_delivery_idx", "source", "account_id", "delivery_id", unique=True, postgresql_where=kind == RAW
        ),
        Index("events_account_idx", "source", "account_id", "id", postgresql_where=kind == RAW),
        Index("events_caused_by_idx", "caused_by", postgresql_where=caused_by.is_not(None)),
        Index("events_payload_age_idx", "created_at", postgresql_where=text("payload IS NOT NULL")),
        Index(
            "events_tick_key",
            "subscription_id",
            "fire_at",
            unique=True,
            postgresql_where=text("kind = 'tick'"),
        ),
        Index(
            "events_time_due_idx",
            "fire_at",
            postgresql_where=text("routed_at IS NULL AND kind IN ('tick', 'timer')"),
        ),
        Index(
            "events_owner_timers_idx",
            "owner_id",
            "fire_at",
            postgresql_where=text("kind = 'timer'"),
        ),
    )

    def serialize_timer(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fire_at": self.fire_at.isoformat() if self.fire_at else None,
            "handler": self.handler,
            "input": (self.data or {}).get("input"),
            "result": self.result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


MAX_RAW_PAYLOAD_BYTES = 256 * 1024
UNSTORABLE_CHARS = re.compile("[\x00\ud800-\udfff]")


def storable(value: Any) -> Any:
    """`value` without the NULs and lone surrogates JSONB rejects."""
    if isinstance(value, str):
        return UNSTORABLE_CHARS.sub("", value)
    if isinstance(value, dict):
        return {storable(k): storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [storable(v) for v in value]
    return value


def kept_payload(outcome: str, payload: Any) -> Any:
    """What a raw event keeps of its delivery's payload. Only a delivery that
    stores nothing keeps it, since nothing else holds what it said; an oversized
    or too deeply nested one keeps only a marker."""
    if outcome != NO_ITEM_TYPE or payload is None:
        return None
    try:
        payload = storable(payload)
        size = len(json.dumps(payload))
    except RecursionError:
        return {"truncated": True}
    return payload if size <= MAX_RAW_PAYLOAD_BYTES else {"truncated": True, "bytes": size}


def record_raw_event(
    db: Session,
    *,
    source: str,
    event_type: str,
    account_id: int,
    owner_id: int | None,
    delivery_id: str,
    outcome: str,
    container: str | None = None,
    payload: Any = None,
    links_items: bool = False,
) -> int | None:
    """Add, uncommitted, the raw event for one delivery; None when the account
    already recorded `delivery_id`. Until the caller commits, a concurrent repeat
    of the delivery waits on this one. A delivery not collected keeps no
    container: an untracked channel stays unseen. `links_items`: the caller sends
    an ingest task that links the item events it writes (caused_by), which a raw
    subscription's delivery waits for."""
    values = dict(
        kind=RAW,
        source=source,
        type=event_type,
        account_id=account_id,
        owner_id=owner_id,
        delivery_id=delivery_id,
        outcome=outcome,
        container=None if outcome == NOT_COLLECTED else container,
        payload=kept_payload(outcome, payload),
        data={LINKS_ITEMS: True} if links_items else None,
    )
    statement = (
        insert(Event)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["source", "account_id", "delivery_id"], index_where=Event.kind == RAW
        )
        .returning(Event.id)
    )
    return db.scalar(statement)


@contextmanager
def caused_by_event(event_id: int | None) -> Iterator[None]:
    """Link the item events written inside this block to raw event `event_id`."""
    token = CAUSING_EVENT.set(event_id)
    try:
        yield
    finally:
        CAUSING_EVENT.reset(token)


def item_event(
    session: Session,
    item: SourceItem,
    changes: list[str] | None,
    backfill: bool = False,
    actors: dict[str, list[str]] | None = None,
) -> Event:
    """The event for storing `item`, which reported `changes` (made by `actors`);
    `backfill` when a history import stored it."""
    inserted = session.info.get(INSERTED_ITEMS_KEY, set())
    action = ADDED if item.id in inserted else CHANGED
    return Event(
        kind=ITEM,
        action=action,
        item_id=item.id,
        changes=changes,
        backfill=backfill,
        caused_by=CAUSING_EVENT.get(),
        actors=actors or None,
    )


@event.listens_for(Session, "before_flush")
def record_removed_items(session, flush_context, instances):
    """Every ORM delete of an item writes its removed event in the same flush."""
    session.add_all(
        # The identity, as reading obj.id can reload a row that is already gone.
        Event(
            kind=ITEM,
            action=REMOVED,
            item_id=cast(tuple, inspect(obj).identity)[0],
            caused_by=CAUSING_EVENT.get(),
        )
        for obj in session.deleted
        if isinstance(obj, SourceItem)
    )


@event.listens_for(Session, "after_flush")
def remember_inserted_items(session, flush_context):
    inserted = session.info.setdefault(INSERTED_ITEMS_KEY, set())
    inserted.update(obj.id for obj in session.new if isinstance(obj, SourceItem))


@event.listens_for(Session, "after_transaction_end")
def forget_inserted_items(session, transaction):
    # A savepoint ending doesn't end the transaction its inserts belong to.
    if transaction.parent is None:
        session.info.pop(INSERTED_ITEMS_KEY, None)
