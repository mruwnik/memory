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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Query,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user, get_user_from_token
from memory.api.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
    get_orchestrator_client,
)
from memory.common import settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import ClaudeConfigSnapshot, User
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
    "SSH_PRIVATE_KEY",
    "GITHUB_TOKEN",
    "GIT_REPO_URL",
    "SYSTEM_ID",
    "HOME",
    "PATH",
    "USER",
    "SHELL",
}


def make_session_id(user_id: int) -> str:
    """Generate a session ID that includes user ownership."""
    return f"u{user_id}-{secrets.token_hex(6)}"


def get_user_id_from_session(session_id: str) -> int | None:
    """Extract user ID from session ID, or None if not parseable."""
    if not session_id.startswith("u"):
        return None
    try:
        user_part = session_id.split("-")[0]
        return int(user_part[1:])  # Remove 'u' prefix
    except (ValueError, IndexError):
        return None


def user_owns_session(user: User, session_id: str) -> bool:
    """Check if a session belongs to a user."""
    owner_id = get_user_id_from_session(session_id)
    return owner_id == user.id


class SpawnRequest(BaseModel):
    """Request to spawn a new Claude Code session."""

    snapshot_id: int
    repo_url: str | None = None  # Git remote URL to set up in workspace
    github_token: str | None = None  # GitHub PAT for HTTPS clone (not stored)
    github_token_write: str | None = None  # Write token for differ (push, PR creation)
    use_happy: bool = False  # Run with Happy instead of Claude CLI
    allowed_tools: list[str] | None = (
        None  # Tools to pre-approve (no permission prompts)
    )
    custom_env: dict[str, str] | None = None  # Custom environment variables
    initial_prompt: str | None = None  # Prompt to start Claude with immediately


class SessionInfo(BaseModel):
    """Info about a running Claude session."""

    session_id: str
    container_id: str | None = None
    container_name: str | None = None
    status: str | None = None


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

    Returns:
        SessionInfo with session_id that can be used to find/kill the container.
        The container_name will be `claude-{session_id}`.
    """
    # Validate snapshot exists and belongs to user
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
    host_snapshot_path = settings.HOST_STORAGE_DIR / relative_path

    session_id = make_session_id(user.id)
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
            snapshot_path=str(host_snapshot_path),
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

    return SessionInfo(
        session_id=result.session_id,
        container_id=result.container_id,
        container_name=result.container_name,
        status=result.status,
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

    # Filter to only sessions owned by this user
    return [
        SessionInfo(
            session_id=s.session_id,
            container_id=s.container_id,
            container_name=s.container_name,
            status=s.status,
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
    """Validate session_id format (defense in depth - should be u{id}-{hex})."""
    return bool(re.match(r"^u\d+-[a-fA-F0-9]+$", session_id))


async def send_ws_json(
    websocket: WebSocket, msg_type: str, data: str | None = None
) -> None:
    """Send a JSON message with timestamp over WebSocket."""
    msg = {"type": msg_type, "timestamp": datetime.now(timezone.utc).isoformat()}
    if data is not None:
        msg["data"] = data
    await websocket.send_json(msg)


# Screen capture loop backoff constants
SCREEN_BACKOFF_UNCHANGED_THRESHOLD = 4  # Start slowing after this many unchanged polls
SCREEN_BACKOFF_MULTIPLIER = 1.5  # Multiply interval by this on each idle poll


async def screen_capture_loop(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
    base_interval: float = 0.5,
    max_interval: float = 2.0,
) -> None:
    """Stream tmux screen captures to WebSocket until container exits.

    Uses adaptive backoff: polls faster when screen is changing, slower when idle.
    This reduces server load during long-running commands while maintaining
    responsiveness during interactive use.
    """
    last_screen = ""
    consecutive_errors = 0
    max_errors = 5
    consecutive_unchanged = 0
    interval = base_interval

    while True:
        try:
            result = await client.capture_screen(session_id)
        except OrchestratorError as e:
            await send_ws_json(websocket, "error", str(e))
            break

        status = result["status"]

        if status == "ok":
            consecutive_errors = 0
            screen = result["screen"]
            # Only send if screen changed (avoid noise)
            if screen and screen != last_screen:
                await send_ws_json(websocket, "screen", screen)
                last_screen = screen
                # Reset to fast polling when content changes
                consecutive_unchanged = 0
                interval = base_interval
            else:
                # Adaptive backoff: slow down polling when screen is idle
                consecutive_unchanged += 1
                if consecutive_unchanged >= SCREEN_BACKOFF_UNCHANGED_THRESHOLD:
                    interval = min(interval * SCREEN_BACKOFF_MULTIPLIER, max_interval)
        elif status in ("not_found", "not_running"):
            await send_ws_json(websocket, "status", "Container exited")
            break
        elif status == "tmux_not_ready":
            # Tmux may not be ready yet, wait and retry
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                await send_ws_json(websocket, "status", "Tmux session not available")
                break
        else:
            # Generic error
            await send_ws_json(websocket, "error", result.get("error", "Unknown error"))
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                break

        await asyncio.sleep(interval)


async def input_handler_loop(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
) -> None:
    """Receive input from WebSocket and send to tmux session."""
    while True:
        try:
            message = await websocket.receive_json()
        except Exception as e:
            # Connection closed or error - log the exception type for debugging
            logger.debug(f"WebSocket receive ended for {session_id}: {type(e).__name__}")
            break

        logger.debug(f"Received WebSocket message type: {message.get('type')}")
        msg_type = message.get("type")
        if msg_type == "input":
            keys = message.get("keys", "")
            if keys:
                try:
                    result = await client.send_keys(session_id, keys)
                    logger.debug(f"send_keys result: {result.get('status')}")
                    if result["status"] != "ok":
                        await send_ws_json(
                            websocket, "error", result.get("error", "Failed to send input")
                        )
                except OrchestratorError as e:
                    await send_ws_json(websocket, "error", str(e))


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

    try:
        await send_ws_json(websocket, "status", f"Connected to {session_id}")

        # Run screen capture and input handling concurrently
        screen_task = asyncio.create_task(
            screen_capture_loop(websocket, session_id, client)
        )
        input_task = asyncio.create_task(
            input_handler_loop(websocket, session_id, client)
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
