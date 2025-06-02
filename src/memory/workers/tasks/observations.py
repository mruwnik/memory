import logging

from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation
from memory.common.celery_app import app, SYNC_OBSERVATION
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


@app.task(name=SYNC_OBSERVATION)
@safe_task_execution
def sync_observation(
    subject: str,
    content: str,
    observation_type: str,
    evidence: dict | None = None,
    confidence: float = 0.5,
    session_id: str | None = None,
    agent_model: str = "unknown",
    tags: list[str] = [],
):
    logger.info(f"Syncing observation {subject}")
    sha256 = create_content_hash(f"{content}{subject}{observation_type}")

    observation = AgentObservation(
        content=content,
        subject=subject,
        observation_type=observation_type,
        confidence=confidence,
        evidence=evidence,
        tags=tags or [],
        session_id=session_id,
        agent_model=agent_model,
        size=len(content),
        mime_type="text/plain",
        sha256=sha256,
        modality="observation",
    )

    with make_session() as session:
        existing_observation = check_content_exists(
            session, AgentObservation, sha256=sha256
        )
        if existing_observation:
            logger.info(f"Observation already exists: {existing_observation.subject}")
            return create_task_result(existing_observation, "already_exists")

        return process_content_item(observation, session)
