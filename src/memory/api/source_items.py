"""API endpoints for source item management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.common.access_control import get_user_project_roles, user_can_access
from memory.common.celery_app import REPROCESS_MEETING, REINGEST_ITEM
from memory.common.db.connection import get_session
from memory.common.db.models import User, SourceItem, JobType
from memory.common.jobs import dispatch_job

router = APIRouter(prefix="/source-items", tags=["source-items"])


class ReingestQueued(BaseModel):
    """Response when reingest is queued."""

    job_id: int
    status: str
    item_id: int
    item_type: str
    message: str


@router.post("/{item_id}/reingest")
def reingest_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> ReingestQueued:
    """
    Queue a source item for reingestion.

    This clears existing chunks/processing and re-runs the full processing pipeline.
    For meetings, this also re-extracts summary, notes, and action items via LLM.

    Returns a job_id that can be used to track processing status via GET /jobs/{job_id}
    """
    item = db.get(SourceItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source item not found")

    # Check user has access to this item
    project_roles = get_user_project_roles(db, user)
    if not user_can_access(user, item, project_roles):
        raise HTTPException(status_code=404, detail="Source item not found")

    item_type = item.__class__.__name__
    modality = item.modality

    # Dispatch to the appropriate task based on modality
    if modality == "meeting":
        # Meetings have a dedicated reprocess task
        result = dispatch_job(
            session=db,
            job_type=JobType.MEETING,
            task_name=REPROCESS_MEETING,
            task_kwargs={"item_id": item_id},
            user_id=user.id,
        )
    else:
        # Generic reingest for other source items (re-embed only)
        result = dispatch_job(
            session=db,
            job_type=JobType.REPROCESS,
            task_name=REINGEST_ITEM,
            task_kwargs={"item_id": item_id, "item_type": item_type},
            user_id=user.id,
        )

    return ReingestQueued(
        job_id=result.job.id,
        status="queued" if result.is_new else result.job.status,
        item_id=item_id,
        item_type=item_type,
        message=result.message,
    )


@router.get("/{item_id}")
def get_source_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict:
    """
    Get basic information about a source item.

    Returns the item's type, modality, tags, and processing status.
    """
    item = db.get(SourceItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source item not found")

    # Check user has access to this item
    project_roles = get_user_project_roles(db, user)
    if not user_can_access(user, item, project_roles):
        raise HTTPException(status_code=404, detail="Source item not found")

    return {
        "id": item.id,
        "type": item.__class__.__name__,
        "modality": item.modality,
        "tags": item.tags or [],
        "embed_status": item.embed_status,
        "size": item.size,
        "inserted_at": item.inserted_at,
        "chunk_count": len(item.chunks) if item.chunks else 0,
    }
