"""
Celery tasks for tracking people.
"""

import logging

from memory.common.db.connection import make_session
from memory.common.db.models import Person, PersonTidbit
from memory.common.celery_app import (
    app,
    SYNC_PERSON_TIDBIT,
)
from memory.common.content_processing import (
    create_content_hash,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


@app.task(name=SYNC_PERSON_TIDBIT)
@safe_task_execution
def sync_person_tidbit(
    person_id: int,
    content: str,
    tidbit_type: str = "note",
    tags: list[str] | None = None,
    project_id: int | None = None,
    sensitivity: str = "basic",
    creator_id: int | None = None,
):
    """
    Create a tidbit of information about a person.

    Tidbits are searchable pieces of information with access control.

    Args:
        person_id: ID of the person this tidbit is about
        content: The information to record
        tidbit_type: Type of tidbit (note, preference, fact, etc.)
        tags: Categorization tags
        project_id: Project ID for access control (None = creator-only)
        sensitivity: Sensitivity level
        creator_id: ID of user creating this tidbit
    """
    logger.info(f"Creating tidbit for person: {person_id}")

    with make_session() as session:
        # Verify person exists
        person = session.get(Person, person_id)
        if not person:
            logger.warning(f"Person not found: {person_id}")
            return {"status": "not_found", "person_id": person_id}

        # Create content hash from person + content (full content for uniqueness)
        sha256 = create_content_hash(
            f"person_tidbit:{person.identifier}:{tidbit_type}:{content}"
        )

        tidbit = PersonTidbit(
            person_id=person_id,
            creator_id=creator_id,
            tidbit_type=tidbit_type,
            modality="person_tidbit",
            mime_type="text/plain",
            sha256=sha256,
            size=len(content),
            content=content,
            tags=tags or [],
            project_id=project_id,
            sensitivity=sensitivity,
        )

        result = process_content_item(tidbit, session)
        result["person_id"] = person_id
        result["person_identifier"] = person.identifier

    return result
