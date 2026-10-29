"""Admin-only read access to audit records."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from memory.api.auth import require_scope
from memory.common.db.connection import get_session
from memory.common.db.models import AuditEvent, User
from memory.common.scopes import SCOPE_ADMIN

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit_events(
    action: str | None = Query(None),
    actor_id: int | None = Query(None),
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to", description="Exclusive"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _user: User = require_scope(SCOPE_ADMIN),
    db: Session = Depends(get_session),
) -> dict:
    """Audit records, newest first."""
    query = db.query(AuditEvent)
    if action:
        query = query.filter(AuditEvent.action == action)
    if actor_id is not None:
        query = query.filter(AuditEvent.actor_id == actor_id)
    if from_time:
        query = query.filter(AuditEvent.timestamp >= from_time)
    if to_time:
        query = query.filter(AuditEvent.timestamp < to_time)

    events = (
        query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return {
        "total": query.count(),
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "actor_id": e.actor_id,
                "actor_email": e.actor_email,
                "action": e.action,
                "target": e.target,
                "attributes": e.attributes,
            }
            for e in events
        ],
    }
