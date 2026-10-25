"""Access-scoped inventory of ingested content, independent of source connections."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.api.auth import require_rest_access
from memory.common.access_control import (
    apply_access_filter_to_query,
    build_access_filter,
    get_user_project_roles,
)
from memory.common.db.connection import get_session
from memory.common.db.models import SourceItem, User

router = APIRouter(prefix="/ingestion-overview", tags=["sources"])


class ItemCounts(BaseModel):
    total: int = 0
    indexed: int = 0
    pending: int = 0
    failed: int = 0
    skipped: int = 0
    added_last_24h: int = 0


class TypeCounts(ItemCounts):
    item_type: str


class IngestionOverview(ItemCounts):
    by_type: list[TypeCounts]
    last_ingested_at: datetime | None = None


@router.get("")
def ingestion_overview(
    user: User = Depends(require_rest_access), db: Session = Depends(get_session)
) -> IngestionOverview:
    access = build_access_filter(user, get_user_project_roles(db, user))
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    query = db.query(
        SourceItem.type,
        SourceItem.embed_status,
        func.count(SourceItem.id),
        func.count(SourceItem.id).filter(SourceItem.inserted_at >= since),
        func.max(SourceItem.inserted_at),
    ).select_from(SourceItem)
    rows = (
        apply_access_filter_to_query(query, access)
        .group_by(SourceItem.type, SourceItem.embed_status)
        .all()
    )
    result = IngestionOverview(by_type=[])
    by_type: dict[str, TypeCounts] = {}
    statuses = {
        "STORED": "indexed",
        "RAW": "pending",
        "QUEUED": "pending",
        "FAILED": "failed",
        "SKIPPED": "skipped",
    }
    for item_type, status, count, recent, last in rows:
        name = item_type or "unknown"
        group = by_type.setdefault(name, TypeCounts(item_type=name))
        for target in (result, group):
            target.total += count
            target.added_last_24h += recent
            field = statuses[status]
            setattr(target, field, getattr(target, field) + count)
        if last is not None and (
            result.last_ingested_at is None or last > result.last_ingested_at
        ):
            result.last_ingested_at = last
    result.by_type = sorted(
        by_type.values(), key=lambda group: (-group.total, group.item_type)
    )
    return result
