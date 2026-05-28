import logging
from typing import cast

from sqlalchemy.exc import IntegrityError

from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation
from memory.common.celery_app import app, SYNC_OBSERVATION
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task

logger = logging.getLogger(__name__)


@app.task(name=SYNC_OBSERVATION)
@tracked_task
def sync_observation(
    subject: str,
    content: str,
    observation_type: str,
    evidence: dict | None = None,
    confidences: dict[str, float] | None = None,
    session_id: str | None = None,
    agent_model: str = "unknown",
    tags: list[str] | None = None,
    creator_id: int | None = None,
    project_id: int | None = None,
    sensitivity: str = "basic",
):
    """Persist an observation, attaching the calling user as creator.

    ``creator_id`` is required for the row to be readable by anyone
    other than admins: ``user_can_access`` checks ``creator_id ==
    user.id`` first, and observations otherwise default to
    ``project_id=NULL`` which the access-control layer treats as
    "superadmin only" (CWE-862, broken access control). Older callers
    that pass no creator_id will produce admin-only observations as
    before — but the MCP ``observe`` tool now always supplies one.
    """
    confidences = confidences or {}
    tags = tags or []
    logger.info(f"Syncing observation {subject}")
    sha256 = create_content_hash(f"{content}{subject}{observation_type}")

    observation = AgentObservation(
        content=content,
        subject=subject,
        observation_type=observation_type,
        evidence=evidence,
        tags=tags,
        session_id=session_id,
        agent_model=agent_model,
        size=len(content),
        mime_type="text/plain",
        sha256=sha256,
        modality="observation",
        creator_id=creator_id,
        project_id=project_id,
        sensitivity=sensitivity,
    )
    observation.update_confidences(confidences)

    with make_session() as session:
        existing_observation = check_content_exists(
            session, AgentObservation, sha256=sha256
        )
        if existing_observation:
            existing_as_obs = cast(AgentObservation, existing_observation)
            logger.info(f"Observation already exists: {existing_as_obs.subject}")
            return create_task_result(existing_observation, "already_exists")

        try:
            return process_content_item(observation, session)
        except IntegrityError as e:
            # Race condition: another task inserted the same observation
            logger.debug(f"IntegrityError during observation insert: {e}")
            session.rollback()
            existing = check_content_exists(session, AgentObservation, sha256=sha256)
            if existing:
                logger.info(f"Observation created by concurrent task: {subject}")
                return create_task_result(existing, "already_exists")
            raise  # Re-raise if it's a different integrity error
