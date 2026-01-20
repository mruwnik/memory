import logging
from typing import cast

from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation
from memory.common.celery_app import app, SYNC_OBSERVATION
from memory.common.content_processing import (
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
    confidences: dict[str, float] = {},
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
        evidence=evidence,
        tags=tags or [],
        session_id=session_id,
        agent_model=agent_model,
        size=len(content),
        mime_type="text/plain",
        sha256=sha256,
        modality="observation",
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

        return process_content_item(observation, session)
