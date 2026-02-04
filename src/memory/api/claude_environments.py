"""API endpoints for Claude Code persistent environments.

Manages Docker volume-backed environments for running Claude Code in containers
with persistent state across sessions.
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.api.orchestrator_client import OrchestratorError, get_orchestrator_client
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import ClaudeConfigSnapshot, ClaudeEnvironment, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/claude/environments", tags=["claude-environments"])


class EnvironmentResponse(BaseModel):
    """Response model for environment operations."""

    id: int
    name: str
    volume_name: str
    description: str | None
    initialized_from_snapshot_id: int | None
    cloned_from_environment_id: int | None
    size_bytes: int | None
    last_used_at: str | None
    created_at: str | None
    session_count: int

    @classmethod
    def from_model(cls, env: ClaudeEnvironment) -> "EnvironmentResponse":
        return cls(
            id=env.id,
            name=env.name,
            volume_name=env.volume_name,
            description=env.description,
            initialized_from_snapshot_id=env.initialized_from_snapshot_id,
            cloned_from_environment_id=env.cloned_from_environment_id,
            size_bytes=env.size_bytes,
            last_used_at=env.last_used_at.isoformat() if env.last_used_at else None,
            created_at=env.created_at.isoformat() if env.created_at else None,
            session_count=env.session_count,
        )


class CreateEnvironmentRequest(BaseModel):
    """Request to create a new environment."""

    name: str
    description: str | None = None
    snapshot_id: int | None = None  # Optional: initialize from this snapshot
    source_environment_id: int | None = None  # Optional: clone from this environment


class ResetEnvironmentRequest(BaseModel):
    """Request to reset an environment."""

    snapshot_id: int | None = None  # Optional: reinitialize from this snapshot


def slugify(text: str, max_length: int = 50) -> str:
    """Convert text to a URL-safe slug.

    Args:
        text: The text to slugify.
        max_length: Maximum length of the resulting slug (default 50).

    Returns:
        A lowercase, hyphen-separated slug safe for use in identifiers.
        Returns "env" as fallback if input contains no valid characters.
    """
    original = text
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    result = text[:max_length].strip("-")  # Also strip leading/trailing hyphens

    # Fallback to "env" if slugify produced empty string (e.g., input was all special chars)
    if not result:
        logger.warning(f"Slugified '{original}' to empty string, using fallback 'env'")
        return "env"

    if result != original.lower().strip():
        logger.debug(f"Slugified '{original}' to '{result}'")

    return result


def generate_volume_name(user_id: int, env_id: int, name: str) -> str:
    """Generate a unique Docker volume name."""
    slug = slugify(name)
    return f"claude-env-u{user_id}-{env_id}-{slug}"


@router.post("/create")
async def create_environment(
    request: CreateEnvironmentRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> EnvironmentResponse:
    """Create a new persistent environment.

    Optionally initialize from a snapshot or clone from another environment.
    If neither is provided, creates an empty environment.
    """
    # Validate that at most one source is provided
    if request.snapshot_id and request.source_environment_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot specify both snapshot_id and source_environment_id",
        )

    # Validate snapshot if provided
    snapshot_path = None
    if request.snapshot_id:
        snapshot = (
            db.query(ClaudeConfigSnapshot)
            .filter(
                ClaudeConfigSnapshot.id == request.snapshot_id,
                ClaudeConfigSnapshot.user_id == user.id,
            )
            .first()
        )
        if not snapshot:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        snapshot_path = str(settings.HOST_STORAGE_DIR / "snapshots" / snapshot.filename)

    # Validate source environment if provided
    source_env = None
    if request.source_environment_id:
        source_env = (
            db.query(ClaudeEnvironment)
            .filter(
                ClaudeEnvironment.id == request.source_environment_id,
                ClaudeEnvironment.user_id == user.id,
            )
            .first()
        )
        if not source_env:
            raise HTTPException(status_code=404, detail="Source environment not found")

    # Create DB record first to get ID for volume name
    env = ClaudeEnvironment(
        user_id=user.id,
        name=request.name,
        description=request.description,
        volume_name="placeholder",  # Will update after we have the ID
        initialized_from_snapshot_id=request.snapshot_id,
        cloned_from_environment_id=request.source_environment_id,
    )
    db.add(env)
    db.flush()  # Get the ID without committing

    # Generate and update volume name
    env.volume_name = generate_volume_name(user.id, env.id, request.name)

    # Create the Docker volume via orchestrator
    client = get_orchestrator_client()
    try:
        if source_env:
            # Clone volume from another environment
            response = await client.clone_environment_volume(
                source_volume=source_env.volume_name,
                dest_volume=env.volume_name,
            )
        elif snapshot_path:
            # Initialize volume from snapshot
            response = await client.initialize_environment(
                volume_name=env.volume_name,
                snapshot_path=snapshot_path,
            )
        else:
            # Create empty volume
            response = await client.create_environment_volume(
                volume_name=env.volume_name,
            )

        if response.get("status") == "error":
            # Note: If the Docker volume was partially created before the error,
            # it may be orphaned. The orchestrator's initialize_environment cleans
            # up on failure, but network issues could still leave orphaned volumes.
            # These can be cleaned up with: docker volume ls --filter label=managed-by=claude-orchestrator
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create volume: {response.get('error')}",
            )
    except OrchestratorError as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    db.commit()
    db.refresh(env)

    return EnvironmentResponse.from_model(env)


@router.get("/list")
def list_environments(
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[EnvironmentResponse]:
    """List all environments for the current user."""
    environments = (
        db.query(ClaudeEnvironment)
        .filter(ClaudeEnvironment.user_id == user.id)
        .order_by(ClaudeEnvironment.last_used_at.desc().nullslast())
        .all()
    )
    return [EnvironmentResponse.from_model(e) for e in environments]


@router.get("/{env_id}")
def get_environment(
    env_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> EnvironmentResponse:
    """Get a specific environment by ID."""
    env = (
        db.query(ClaudeEnvironment)
        .filter(
            ClaudeEnvironment.id == env_id,
            ClaudeEnvironment.user_id == user.id,
        )
        .first()
    )
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")
    return EnvironmentResponse.from_model(env)


@router.delete("/{env_id}")
async def delete_environment(
    env_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict:
    """Delete an environment and its Docker volume."""
    env = (
        db.query(ClaudeEnvironment)
        .filter(
            ClaudeEnvironment.id == env_id,
            ClaudeEnvironment.user_id == user.id,
        )
        .first()
    )
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")

    # Delete the Docker volume
    client = get_orchestrator_client()
    try:
        response = await client.delete_environment_volume(
            volume_name=env.volume_name,
        )
        # Log but don't fail if volume already gone
        if response.get("status") == "error":
            logger.warning(f"Failed to delete volume {env.volume_name}: {response}")
    except OrchestratorError as e:
        logger.warning(f"Orchestrator error deleting volume: {e}")

    # Delete DB record
    db.delete(env)
    db.commit()

    return {"deleted": True}


@router.post("/{env_id}/reset")
async def reset_environment(
    env_id: int,
    request: ResetEnvironmentRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> EnvironmentResponse:
    """Reset an environment to its initial state.

    This deletes all data in the volume and optionally reinitializes
    from a snapshot.
    """
    env = (
        db.query(ClaudeEnvironment)
        .filter(
            ClaudeEnvironment.id == env_id,
            ClaudeEnvironment.user_id == user.id,
        )
        .first()
    )
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")

    # Validate snapshot if provided
    snapshot_path = None
    if request.snapshot_id:
        snapshot = (
            db.query(ClaudeConfigSnapshot)
            .filter(
                ClaudeConfigSnapshot.id == request.snapshot_id,
                ClaudeConfigSnapshot.user_id == user.id,
            )
            .first()
        )
        if not snapshot:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        snapshot_path = str(settings.HOST_STORAGE_DIR / "snapshots" / snapshot.filename)
        env.initialized_from_snapshot_id = request.snapshot_id
    else:
        env.initialized_from_snapshot_id = None

    # Reset the volume via orchestrator
    client = get_orchestrator_client()
    try:
        response = await client.reset_environment_volume(
            volume_name=env.volume_name,
            snapshot_path=snapshot_path,
        )
        if response.get("status") == "error":
            raise HTTPException(
                status_code=500,
                detail=f"Failed to reset volume: {response.get('error')}",
            )
    except OrchestratorError as e:
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    # Reset usage stats
    env.session_count = 0
    env.size_bytes = None

    db.commit()
    db.refresh(env)

    return EnvironmentResponse.from_model(env)


def mark_environment_used(env: ClaudeEnvironment, db: DBSession) -> None:
    """Update environment usage stats when spawning a session."""
    env.last_used_at = datetime.now(timezone.utc)
    env.session_count += 1
    db.commit()
