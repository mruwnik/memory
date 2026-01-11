"""Celery tasks for orphan verification."""

import logging
from typing import Any

from memory.common import settings
from memory.common.celery_app import (
    app,
    VERIFY_ORPHANS,
    VERIFY_SOURCE_BATCH,
)
from memory.common.db.connection import make_session
from memory.workers.verification import (
    VERIFIERS,
    select_items_for_verification,
    group_items_by_batch_key,
    verify_items,
)
from memory.common.content_processing import safe_task_execution

logger = logging.getLogger(__name__)


@app.task(name=VERIFY_ORPHANS)
@safe_task_execution
def verify_orphans(
    batch_size: int | None = None,
    source_types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Main orphan verification task. Scheduled to run periodically.

    Selects a batch of items needing verification, groups them by source/account,
    and dispatches verification tasks for each group.

    Args:
        batch_size: Max items to verify in this run
        source_types: Optional filter by source type (e.g., ["mail_message"])

    Returns:
        Summary of dispatched verification tasks
    """
    if batch_size is None:
        batch_size = settings.VERIFICATION_BATCH_SIZE

    with make_session() as session:
        items = select_items_for_verification(
            session,
            batch_size=batch_size,
            source_types=source_types,
        )

        if not items:
            return {"status": "no_items", "checked": 0}

        # Group by batch key and dispatch
        groups = group_items_by_batch_key(items)
        dispatched = []

        for (source_type, key), group_items in groups.items():
            item_ids = [item.id for item in group_items]
            task = verify_source_batch.delay(source_type, key, item_ids)
            dispatched.append(
                {
                    "source_type": source_type,
                    "key": str(key),
                    "items": len(item_ids),
                    "task_id": task.id,
                }
            )

        return {
            "status": "dispatched",
            "total_items": len(items),
            "groups": len(groups),
            "tasks": dispatched,
        }


@app.task(name=VERIFY_SOURCE_BATCH)
@safe_task_execution
def verify_source_batch(
    source_type: str,
    batch_key: Any,
    item_ids: list[int],
) -> dict[str, Any]:
    """
    Verify a batch of items from a single source/account.

    Args:
        source_type: Type of source (mail_message, github_item, etc.)
        batch_key: Account/repo ID for grouping
        item_ids: Database IDs of items to verify

    Returns:
        Verification results summary
    """
    if source_type not in VERIFIERS:
        return {"status": "error", "error": f"No verifier for {source_type}"}

    with make_session() as session:
        result = verify_items(session, source_type, batch_key, item_ids)
        session.commit()

        return {
            "status": "completed",
            "source_type": source_type,
            "batch_key": str(batch_key),
            "verified": result.verified,
            "orphaned": result.orphaned,
            "errors": result.errors,
            "deleted": result.deleted,
        }
