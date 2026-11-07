from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from memory.api.auth import require_rest_access
from memory.common.db.connection import get_session
from memory.common.db.models import Event, User
from memory.common.db.models.discord import DiscordBot
from memory.common.db.models.events import RAW
from memory.common.db.models.slack import SlackApp

EVENT_ARRIVAL_RESOLUTION = timedelta(minutes=1)


def record_event_arrival(db: Session, row: SlackApp | DiscordBot) -> None:
    """Stamp last_event_at, at most once a minute so busy integrations don't write per event."""
    now = datetime.now(timezone.utc)
    if row.last_event_at and now - row.last_event_at < EVENT_ARRIVAL_RESOLUTION:
        return
    row.last_event_at = now
    db.commit()


router = APIRouter(prefix="/webhook-events", tags=["sources"])


class RawEventView(BaseModel):
    id: int
    created_at: datetime
    type: str
    outcome: str
    container: str | None
    item_ids: list[int]
    payload: Any = None


@router.get("/{source}/{account_id}")
def webhook_log(
    source: Literal["github", "slack", "discord", "transcripts"],
    account_id: int,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    payload: bool = False,
    user: User = Depends(require_rest_access),
    db: Session = Depends(get_session),
) -> list[RawEventView]:
    """The newest webhook deliveries to one of the caller's source accounts,
    before event id `before`, with the items each led to. Payloads are untrusted
    and only included on request, until they expire."""
    query = (
        select(Event)
        .where(
            Event.kind == RAW,
            Event.source == source,
            Event.account_id == account_id,
            Event.owner_id == user.id,
        )
        .order_by(Event.id.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.where(Event.id < before)
    if payload:
        query = query.options(undefer(Event.payload))
    events = db.scalars(query).all()
    items: dict[int, list[int]] = {}
    caused = select(Event.caused_by, Event.item_id).where(Event.caused_by.in_([e.id for e in events]))
    for raw_id, item_id in db.execute(caused.order_by(Event.id)):
        items.setdefault(raw_id, []).append(item_id)
    return [
        RawEventView(
            id=e.id,
            created_at=e.created_at,
            type=e.type or "",
            outcome=e.outcome or "",
            container=e.container,
            item_ids=items.get(e.id, []),
            payload=e.payload if payload else None,
        )
        for e in events
    ]
