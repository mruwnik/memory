"""API endpoints for Cloud Claude Code session management.

Spawn, list, and kill Claude Code containers.

Sessions are managed by the Claude Session Orchestrator, a systemd service
that handles container lifecycle and networking. Communication is via Unix socket.

Session IDs are prefixed with user_id to enable authorization filtering:
  session_id = f"u{user_id}-{random_hex}"
"""

import asyncio
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Query,
)
from croniter import croniter
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user, get_user_from_token, require_scope
from memory.common.scopes import SCOPE_SCHEDULE_WRITE
from memory.api.orchestrator_client import (
    OrchestratorError,
    get_orchestrator_client,
)
from memory.api.terminal_relay_client import RelayClient
from memory.api.tmux_session import (
    input_handler_loop,
    screen_capture_loop,
    send_ws_json,
)
from memory.common import settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import ClaudeConfigSnapshot, ClaudeEnvironment, ScheduledTask, User
from memory.common.db.models.scheduled_tasks import TaskType, compute_next_cron
from memory.api.claude_environments import mark_environment_used
from memory.common.db.models.secrets import extract as extract_secret

# Log directory on host where orchestrator writes session logs
LOG_DIR = Path("/var/log/claude-sessions")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/claude", tags=["claude"])

DISALLOWED_TOOLS = set()

# Reserved env var names that cannot be overridden by custom_env
RESERVED_ENV_VARS = {
    "CLAUDE_EXECUTABLE",
    "CLAUDE_ALLOWED_TOOLS",
    "CLAUDE_INITIAL_PROMPT",
    "CLAUDE_RUN_ID",
    "SSH_PRIVATE_KEY",
    "GITHUB_TOKEN",
    "GITHUB_TOKEN_WRITE",
    "GIT_REPO_URL",
    "SYSTEM_ID",
    "HOME",
    "PATH",
    "USER",
    "SHELL",
}


def make_session_id(
    user_id: int,
    *,
    environment_id: int | None = None,
    snapshot_id: int | None = None,
) -> str:
    """Generate a session ID that includes user ownership and source info.

    Format: u{user_id}-{source}-{random_hex}
    Where source is e{env_id} for environments or s{snap_id} for snapshots.
    """
    if environment_id is not None:
        source = f"e{environment_id}"
    elif snapshot_id is not None:
        source = f"s{snapshot_id}"
    else:
        source = "x"  # Unknown source (shouldn't happen)
    return f"u{user_id}-{source}-{secrets.token_hex(6)}"


def get_user_id_from_session(session_id: str) -> int | None:
    """Extract user ID from session ID, or None if not parseable."""
    if not session_id.startswith("u"):
        return None
    try:
        user_part = session_id.split("-")[0]
        return int(user_part[1:])  # Remove 'u' prefix
    except (ValueError, IndexError):
        return None


def get_environment_id_from_session(session_id: str) -> int | None:
    """Extract environment ID from session ID, or None if not an environment session."""
    try:
        parts = session_id.split("-")
        if len(parts) >= 2 and parts[1].startswith("e"):
            return int(parts[1][1:])  # Remove 'e' prefix
    except (ValueError, IndexError):
        pass
    return None


def user_owns_session(user: User, session_id: str) -> bool:
    """Check if a session belongs to a user."""
    owner_id = get_user_id_from_session(session_id)
    return owner_id == user.id


class SpawnRequest(BaseModel):
    """Request to spawn a new Claude Code session.

    Must provide either snapshot_id OR environment_id (mutually exclusive).
    - snapshot_id: Start fresh from a static snapshot (extracted each time)
    - environment_id: Use persistent environment (Docker volume, state persists)
    """

    snapshot_id: int | None = None  # Static snapshot to extract
    environment_id: int | None = None  # Persistent environment to use
    repo_url: str | None = None  # Git remote URL to set up in workspace
    github_token: str | None = None  # GitHub PAT for HTTPS clone (not stored)
    github_token_write: str | None = None  # Write token for differ (push, PR creation)
    use_happy: bool = False  # Run with Happy instead of Claude CLI
    allowed_tools: list[str] | None = (
        None  # Tools to pre-approve (no permission prompts)
    )
    custom_env: dict[str, str] | None = None  # Custom environment variables
    initial_prompt: str | None = None  # Prompt to start Claude with immediately
    run_id: str | None = None  # Custom run ID for branch naming (defaults to session_id)

    @model_validator(mode="after")
    def check_source_mutual_exclusivity(self) -> "SpawnRequest":
        """Ensure exactly one of snapshot_id or environment_id is provided."""
        if self.snapshot_id and self.environment_id:
            raise ValueError("Cannot specify both snapshot_id and environment_id")
        if not self.snapshot_id and not self.environment_id:
            raise ValueError("Must specify either snapshot_id or environment_id")
        return self


class ScheduleRequest(BaseModel):
    """Request to schedule a recurring Claude Code session."""

    cron_expression: str
    spawn_config: SpawnRequest


class ScheduleResponse(BaseModel):
    """Response from scheduling a recurring Claude Code session."""

    task_id: str
    cron_expression: str
    next_scheduled_time: str
    topic: str


class SessionInfo(BaseModel):
    """Info about a running Claude session."""

    session_id: str
    container_id: str | None = None
    container_name: str | None = None
    status: str | None = None
    environment_id: int | None = None  # Extracted from session_id for filtering


class OrchestratorStatus(BaseModel):
    """Status of the orchestrator service."""

    available: bool
    socket_path: str | None = None


@router.get("/status")
async def orchestrator_status() -> OrchestratorStatus:
    """Check orchestrator availability."""
    client = get_orchestrator_client()
    available = await client.ping()
    return OrchestratorStatus(
        available=available,
        socket_path=client.socket_path if available else None,
    )


@router.post("/spawn")
async def spawn_session(
    request: SpawnRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SessionInfo:
    """Spawn a new Claude Code container session.

    Must provide either snapshot_id OR environment_id (mutually exclusive).

    Returns:
        SessionInfo with session_id that can be used to find/kill the container.
        The container_name will be `claude-{session_id}`.
    """
    # Note: mutual exclusivity of snapshot_id/environment_id is validated by
    # SpawnRequest.check_source_mutual_exclusivity (returns 422 on violation)

    # Variables for orchestrator call
    host_snapshot_path: str | None = None
    environment_volume: str | None = None
    environment: ClaudeEnvironment | None = None

    if request.snapshot_id:
        # Static snapshot mode: extract fresh each time
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

        snapshot_path = settings.SNAPSHOT_STORAGE_DIR / snapshot.filename
        if not snapshot_path.exists():
            raise HTTPException(status_code=500, detail="Snapshot file not found")

        # Orchestrator runs on host, needs host path (not container path)
        # Container: /app/memory_files/... -> Host: <HOST_STORAGE_DIR>/...
        relative_path = snapshot_path.relative_to(settings.FILE_STORAGE_DIR)
        host_snapshot_path = str(settings.HOST_STORAGE_DIR / relative_path)

    else:
        # Environment mode: use persistent Docker volume
        environment = (
            db.query(ClaudeEnvironment)
            .filter(
                ClaudeEnvironment.id == request.environment_id,
                ClaudeEnvironment.user_id == user.id,
            )
            .first()
        )
        if not environment:
            raise HTTPException(status_code=404, detail="Environment not found")

        environment_volume = environment.volume_name

    session_id = make_session_id(
        user.id,
        environment_id=request.environment_id,
        snapshot_id=request.snapshot_id,
    )
    client = get_orchestrator_client()

    # Use Happy image and executable if requested
    if request.use_happy:
        image = "claude-cloud-happy:latest"
        env = {"CLAUDE_EXECUTABLE": "happy claude --happy-starting-mode remote"}
    else:
        image = "claude-cloud:latest"
        env = {}

    # Always set SYSTEM_ID for Happy machineId (used by entrypoint)
    env["SYSTEM_ID"] = settings.APP_NAME

    # Add allowed tools to environment (validated against allowlist)
    if request.allowed_tools:
        allowed_tools = set(request.allowed_tools) - DISALLOWED_TOOLS
        env["CLAUDE_ALLOWED_TOOLS"] = " ".join(allowed_tools)

    # Add initial prompt if provided
    if request.initial_prompt:
        env["CLAUDE_INITIAL_PROMPT"] = request.initial_prompt

    # Set run ID for branch naming (used by entrypoint to create claude/<run_id> branch)
    # Default to session_id if not provided
    env["CLAUDE_RUN_ID"] = request.run_id or session_id

    # Add custom environment variables (with validation)
    if request.custom_env:
        for key, value in request.custom_env.items():
            # Validate key format (standard env var naming)
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid env var name '{key}': must start with letter/underscore, contain only alphanumeric/underscore",
                )
            # Check for reserved names
            if key in RESERVED_ENV_VARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot override reserved env var '{key}'",
                )
            # Validate value (no null bytes)
            if "\x00" in value:
                raise HTTPException(
                    status_code=400,
                    detail=f"Env var '{key}' contains invalid null byte",
                )
            env[key] = value

    # Resolve github tokens: either literal PATs or secret names
    github_token = None
    if request.github_token:
        github_token = extract_secret(db, user.id, request.github_token)

    github_token_write = None
    if request.github_token_write:
        github_token_write = extract_secret(db, user.id, request.github_token_write)

    try:
        result = await client.create_session(
            session_id=session_id,
            snapshot_path=host_snapshot_path,
            environment_volume=environment_volume,
            memory_stack=settings.MEMORY_STACK,
            ssh_private_key=user.ssh_private_key,
            github_token=github_token,
            github_token_write=github_token_write,
            git_repo_url=request.repo_url,
            image=image,
            env=env,
        )
    except OrchestratorError as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    # Update environment usage stats if using an environment
    if environment:
        mark_environment_used(environment, db)

    return SessionInfo(
        session_id=result.session_id,
        container_id=result.container_id,
        container_name=result.container_name,
        status=result.status,
        environment_id=request.environment_id,
    )


@router.post("/schedule")
async def schedule_session(
    request: ScheduleRequest,
    user: User = require_scope(SCOPE_SCHEDULE_WRITE),
    db: DBSession = Depends(get_session),
) -> ScheduleResponse:
    """Schedule a recurring Claude Code session.

    Creates a ScheduledTask that will spawn Claude sessions on a cron schedule.
    Requires an initial_prompt in the spawn_config.
    """
    if not croniter.is_valid(request.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    # Validate standard 5-field cron (reject 6-field seconds syntax)
    cron_parts = request.cron_expression.strip().split()
    if len(cron_parts) != 5:
        raise HTTPException(
            status_code=400,
            detail=f"Only standard 5-field cron expressions are supported, got {len(cron_parts)} fields",
        )

    # Enforce minimum interval to prevent excessive spawning.
    # NOTE: This checks only the gap between the first two upcoming occurrences,
    # not the minimum gap across all occurrences. For expressions like "0 9 * * 1-5"
    # (weekdays only), the gap varies (1 day weekday-to-weekday, 3 days Fri-to-Mon).
    # This is sufficient in practice since we're guarding against sub-10-minute crons.
    cron = croniter(request.cron_expression)
    first = cron.get_next(datetime)
    second = cron.get_next(datetime)
    interval_minutes = (second - first).total_seconds() / 60
    if interval_minutes < settings.MIN_CRON_INTERVAL_MINUTES:
        raise HTTPException(
            status_code=400,
            detail=f"Cron interval too short ({interval_minutes:.0f}m). Minimum is {settings.MIN_CRON_INTERVAL_MINUTES} minutes.",
        )

    # Enforce per-user limit on active scheduled tasks.
    # NOTE: This is a soft limit (TOCTOU race possible with concurrent requests).
    # Acceptable for this use case since concurrent scheduling is unlikely.
    active_count = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.user_id == user.id,
            ScheduledTask.enabled.is_(True),
        )
        .count()
    )
    if active_count >= settings.MAX_SCHEDULED_TASKS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {settings.MAX_SCHEDULED_TASKS_PER_USER} active scheduled tasks per user reached",
        )

    if not request.spawn_config.initial_prompt:
        raise HTTPException(
            status_code=400,
            detail="Scheduled sessions require an initial_prompt",
        )

    # Validate that referenced secrets can be resolved (early feedback)
    for token_field in ("github_token", "github_token_write"):
        token_value = getattr(request.spawn_config, token_field, None)
        if token_value:
            try:
                extract_secret(db, user.id, token_value)
            except (KeyError, ValueError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Secret '{token_value}' not found for {token_field}",
                ) from e

    topic = request.spawn_config.initial_prompt[:100]
    next_time = compute_next_cron(request.cron_expression)

    task = ScheduledTask(
        user_id=user.id,
        task_type=TaskType.CLAUDE_SESSION,
        topic=topic,
        data={"spawn_config": request.spawn_config.model_dump(exclude_none=True)},
        cron_expression=request.cron_expression,
        next_scheduled_time=next_time,
        enabled=True,
    )
    db.add(task)
    db.commit()

    return ScheduleResponse(
        task_id=task.id,
        cron_expression=task.cron_expression,
        next_scheduled_time=next_time.isoformat(),
        topic=topic,
    )


@router.get("/list")
async def list_sessions(
    user: User = Depends(get_current_user),
) -> list[SessionInfo]:
    """List active Claude sessions owned by the current user."""
    client = get_orchestrator_client()

    try:
        sessions = await client.list_sessions()
    except OrchestratorError as e:
        logger.error(f"Failed to list sessions: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    # Filter to only sessions owned by this user, extract environment_id from session_id
    return [
        SessionInfo(
            session_id=s.session_id,
            container_id=s.container_id,
            container_name=s.container_name,
            status=s.status,
            environment_id=get_environment_id_from_session(s.session_id),
        )
        for s in sessions
        if user_owns_session(user, s.session_id)
    ]


@router.get("/{session_id}")
async def get_session_info(
    session_id: str,
    user: User = Depends(get_current_user),
) -> SessionInfo:
    """Get details of a specific Claude session owned by the current user."""
    # Authorization: verify session belongs to user
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()

    try:
        session = await client.get_session(session_id)
    except OrchestratorError as e:
        logger.error(f"Failed to get session: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfo(
        session_id=session.session_id,
        container_id=session.container_id,
        container_name=session.container_name,
        status=session.status,
        environment_id=get_environment_id_from_session(session.session_id),
    )


@router.delete("/{session_id}")
async def kill_session(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Kill a Claude session owned by the current user."""
    # Authorization: verify session belongs to user
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()

    try:
        success = await client.stop_session(session_id)
    except OrchestratorError as e:
        logger.error(f"Failed to stop session: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "killed", "session_id": session_id}


@router.get("/{session_id}/attach")
async def get_attach_commands(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Get commands to attach to a session owned by the current user.

    Returns commands that can be used to attach to the container:
    - attach_cmd: docker attach (connects to main process)
    - exec_cmd: docker exec -it bash (new shell in container)
    """
    # Authorization: verify session belongs to user
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()

    try:
        info = await client.get_attach_info(session_id)
    except OrchestratorError as e:
        logger.error(f"Failed to get attach info: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return info


@router.get("/{session_id}/logs")
async def get_session_logs(
    session_id: str,
    tail: int = 100,
    user: User = Depends(get_current_user),
) -> dict:
    """Get logs for a Claude session.

    Logs are persisted even after container exit, enabling debugging of
    containers that crash immediately.

    Args:
        session_id: The session to get logs for
        tail: Number of lines from the end (default 100, 0 for all)

    Returns:
        - session_id: The session ID
        - source: "file" (persisted) or "container" (live)
        - logs: The log content
    """
    # Authorization: verify session belongs to user
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()

    try:
        result = await client.get_logs(session_id, tail=tail)
    except OrchestratorError as e:
        logger.error(f"Failed to get logs: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if result is None:
        raise HTTPException(status_code=404, detail="No logs available")

    return result


# --- WebSocket log streaming helpers ---


def is_valid_session_id(session_id: str) -> bool:
    """Validate session_id format (defense in depth).

    Formats:
    - Legacy: u{user_id}-{hex} (backward compat for old sessions)
    - New: u{user_id}-{source}-{hex} where source is:
      - e{env_id} for environment-based sessions
      - s{snap_id} for snapshot-based sessions
      - x for sessions without snapshot/environment
    """
    # Matches: u123-abc123 (legacy) or u123-e456-abc123 or u123-s789-abc123 or u123-x-abc123
    return bool(re.match(r"^u\d+-(e\d+-|s\d+-|x-)?[a-fA-F0-9]+$", session_id))


@router.websocket("/{session_id}/logs/stream")
async def stream_session_logs(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(..., description="Authentication token"),
):
    """Bidirectional terminal session via WebSocket.

    Captures the tmux pane content periodically (every 0.5s) and sends it
    when changed. Also receives input from client and sends to tmux.

    Connect with: ws://host/claude/{session_id}/logs/stream?token=<auth_token>

    Server -> Client messages (JSON):
    - screen: Full terminal content (only sent when changed)
    - status: Connection/session state changes
    - error: Error messages

    Client -> Server messages (JSON):
    - {"type": "input", "keys": "..."}: Send keystrokes to tmux
    """
    if not is_valid_session_id(session_id):
        await websocket.close(code=4004, reason="Invalid session ID format")
        return

    # Authenticate and authorize
    with make_session() as db:
        user = get_user_from_token(token, db)
        if not user:
            await websocket.close(code=4001, reason="Invalid or expired token")
            return
        if not user_owns_session(user, session_id):
            await websocket.close(code=4004, reason="Session not found")
            return

    await websocket.accept()
    client = get_orchestrator_client()

    # Connect to the in-container terminal relay for fast tmux interaction.
    # Container hostname is claude-{session_id} on the shared Docker network.
    relay = RelayClient(host=f"claude-{session_id}")

    try:
        await send_ws_json(websocket, "status", f"Connected to {session_id}")

        # Shared state for adaptive polling - input handler updates last_input_time
        # and sets input_event to wake up the capture loop immediately
        input_event = asyncio.Event()
        activity_state = {
            "last_input_time": 0.0,
            "input_event": input_event,
        }

        # Run screen capture and input handling concurrently
        screen_task = asyncio.create_task(
            screen_capture_loop(websocket, session_id, client, activity_state, relay)
        )
        input_task = asyncio.create_task(
            input_handler_loop(websocket, session_id, relay, activity_state)
        )

        # Wait for either task to complete (usually screen_task on container exit,
        # or input_task on disconnect)
        done, pending = await asyncio.wait(
            [screen_task, input_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"Error in terminal session for {session_id}: {e}")
        try:
            await send_ws_json(websocket, "error", str(e))
        except Exception:
            pass
    finally:
        await relay.close()
