from datetime import datetime
from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.sql import func

from memory.common.db.models.base import Base
from memory.common.db.models.source_item import SourceItem

ITEM = "item"
ADDED, CHANGED, REMOVED = "added", "changed", "removed"
# session.info key: ids of the items this transaction inserted.
INSERTED_ITEMS_KEY = "_inserted_source_items"


class Event(Base):
    """Something that happened, written in the transaction that made it happen;
    pending until memory.agents.triggers.route_events routes it."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=ITEM)
    action: Mapped[str | None] = mapped_column(Text)
    # No foreign key: an item's removed event outlives the item.
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    # The ingester's Change tags; NULL when it didn't say what changed.
    changes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Failed routing attempts; after settings.EVENT_MAX_ATTEMPTS the event is
    # routed anyway, with the last error's type.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    routed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('item', 'raw', 'tick', 'timer')", name="events_kind_check"),
        CheckConstraint(
            "kind <> 'item' OR (item_id IS NOT NULL "
            "AND action IN ('added', 'changed', 'removed'))",
            name="events_item_check",
        ),
        Index("events_pending_idx", "id", postgresql_where=routed_at.is_(None)),
        Index("events_routed_at_idx", "routed_at"),
    )


def item_event(
    session: Session, item: SourceItem, changes: list[str] | None, backfill: bool = False
) -> Event:
    """The event for storing `item`, which reported `changes`; `backfill` when a
    history import stored it."""
    inserted = session.info.get(INSERTED_ITEMS_KEY, set())
    action = ADDED if item.id in inserted else CHANGED
    return Event(kind=ITEM, action=action, item_id=item.id, changes=changes, backfill=backfill)


@event.listens_for(Session, "before_flush")
def record_removed_items(session, flush_context, instances):
    """Every ORM delete of an item writes its removed event in the same flush."""
    session.add_all(
        # The identity, as reading obj.id can reload a row that is already gone.
        Event(kind=ITEM, action=REMOVED, item_id=cast(tuple, inspect(obj).identity)[0])
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
