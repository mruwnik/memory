"""API endpoints for Cloud Claude Code session management.

Spawn, list, and kill Claude Code containers.

Sessions are managed by the Claude Session Orchestrator, which provides a
REST API over Unix socket for container and volume lifecycle management.

Session IDs are prefixed with user_id to enable authorization filtering:
  session_id = f"u{user_id}-{source}-{random_hex}"
"""

import asyncio
import json
import logging
import re
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import websockets
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    Query,
)
from fastapi.responses import StreamingResponse
from croniter import croniter
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user, get_user_from_token, require_scope
from memory.api.transfer_tokens import (
    TransferTokenError,
    TransferTokenPayload,
    validate_transfer_path,
    verify_token,
)
from memory.common.scopes import SCOPE_SCHEDULE_WRITE
from memory.api.orchestrator_client import (
    ORCHESTRATOR_SOCKET,
    OrchestratorError,
    get_orchestrator_client,
)
from memory.api.terminal_relay_client import RelayClient
from memory.api.tmux_session import (
    input_handler_loop,
    pane_poll_loop,
    screen_capture_loop,
    send_ws_json,
)
from memory.common import settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import ClaudeConfigSnapshot, ClaudeEnvironment, ScheduledTask, User
from memory.common.db.models.scheduled_tasks import TaskType, compute_next_cron
from memory.api.claude_environments import mark_environment_used
from memory.common.db.models.secrets import extract as extract_secret

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
    "ENABLE_PLAYWRIGHT",
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
    enable_playwright: bool = False  # Enable Playwright MCP server in container
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
    container_name: str | None = None
    status: str | None = None
    environment_id: int | None = None  # Extracted from session_id for filtering
    differ: dict[str, Any] | None = None  # Differ server connection info (host, port)


class OrchestratorStatus(BaseModel):
    """Status of the orchestrator service."""

    available: bool
    socket_path: str | None = None
    containers: dict | None = None
    memory: dict | None = None
    cpus: dict | None = None


@router.get("/status")
async def orchestrator_status() -> OrchestratorStatus:
    """Check orchestrator availability and resource usage."""
    client = get_orchestrator_client()
    try:
        health = await client.health()
        return OrchestratorStatus(
            available=True,
            socket_path=client.socket_path,
            containers=health.containers,
            memory=health.memory,
            cpus=health.cpus,
        )
    except OrchestratorError:
        return OrchestratorStatus(available=False)


@router.post("/spawn")
async def spawn_session(
    request: SpawnRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SessionInfo:
    """Spawn a new Claude Code container session.

    Must provide either snapshot_id OR environment_id (mutually exclusive).

    For snapshot-based sessions, creates a temporary volume, initializes it
    from the snapshot, then creates the container with that volume.

    For environment-based sessions, uses the existing persistent volume.

    Returns:
        SessionInfo with session_id that can be used to find/kill the container.
        The container_name will be `claude-{session_id}`.
    """
    # Note: mutual exclusivity of snapshot_id/environment_id is validated by
    # SpawnRequest.check_source_mutual_exclusivity (returns 422 on violation)

    host_snapshot_path: str | None = None
    volume_name: str | None = None
    environment: ClaudeEnvironment | None = None

    if request.snapshot_id:
        # Static snapshot mode: extract fresh each time into a new volume
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

        volume_name = environment.volume_name

    session_id = make_session_id(
        user.id,
        environment_id=request.environment_id,
        snapshot_id=request.snapshot_id,
    )
    client = get_orchestrator_client()

    image = "claude-cloud:latest"
    env: dict[str, str] = {}

    env["SYSTEM_ID"] = settings.APP_NAME

    # Add allowed tools to environment (validated against allowlist)
    if request.allowed_tools:
        allowed_tools = set(request.allowed_tools) - DISALLOWED_TOOLS
        env["CLAUDE_ALLOWED_TOOLS"] = " ".join(allowed_tools)

    # Add initial prompt if provided
    if request.initial_prompt:
        env["CLAUDE_INITIAL_PROMPT"] = request.initial_prompt

    # Enable Playwright MCP server if requested
    if request.enable_playwright:
        env["ENABLE_PLAYWRIGHT"] = "1"

    # Set run ID for branch naming (used by entrypoint to create claude/<run_id> branch)
    env["CLAUDE_RUN_ID"] = request.run_id or session_id

    # Add custom environment variables (with validation)
    if request.custom_env:
        for key, value in request.custom_env.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid env var name '{key}': must start with letter/underscore, contain only alphanumeric/underscore",
                )
            if key in RESERVED_ENV_VARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot override reserved env var '{key}'",
                )
            if "\x00" in value:
                raise HTTPException(
                    status_code=400,
                    detail=f"Env var '{key}' contains invalid null byte",
                )
            env[key] = value

    # Inject secrets as env vars (previously handled by old orchestrator)
    if user.ssh_private_key:
        env["SSH_PRIVATE_KEY"] = user.ssh_private_key
    if request.github_token:
        env["GITHUB_TOKEN"] = extract_secret(db, user.id, request.github_token)
    if request.github_token_write:
        env["GITHUB_TOKEN_WRITE"] = extract_secret(db, user.id, request.github_token_write)
    if request.repo_url:
        env["GIT_REPO_URL"] = request.repo_url

    # Determine Docker network for communication with Memory API
    networks = [f"memory-api-{settings.MEMORY_STACK}"]

    try:
        # For snapshot-based sessions, create a temporary volume and init from snapshot
        if host_snapshot_path:
            volume_name = f"claude-snap-{session_id}"
            await client.create_initialized_volume(volume_name, host_snapshot_path)

        result = await client.create_container(
            session_id,
            image=image,
            volume=volume_name,
            env=env,
            networks=networks,
        )
    except OrchestratorError as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    # Update environment usage stats if using an environment
    if environment:
        mark_environment_used(environment, db)

    return SessionInfo(
        session_id=result.session_id,
        container_name=result.container_name,
        status=result.status,
        environment_id=request.environment_id,
        differ=result.differ or None,
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

    prompt = request.spawn_config.initial_prompt
    topic = prompt[:100]
    next_time = compute_next_cron(request.cron_expression)

    # Store initial_prompt in the message field (not in spawn_config)
    spawn_data = request.spawn_config.model_dump(exclude_none=True)
    spawn_data.pop("initial_prompt", None)

    task = ScheduledTask(
        user_id=user.id,
        task_type=TaskType.CLAUDE_SESSION,
        topic=topic,
        message=prompt,
        data={"spawn_config": spawn_data},
        cron_expression=request.cron_expression,
        next_scheduled_time=next_time,
        enabled=True,
    )
    db.add(task)
    db.commit()

    return ScheduleResponse(
        task_id=task.id,
        cron_expression=request.cron_expression,
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
        containers = await client.list_containers()
    except OrchestratorError as e:
        logger.error(f"Failed to list sessions: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    # Filter to only sessions owned by this user
    return [
        SessionInfo(
            session_id=c.session_id,
            container_name=c.container_name,
            status=c.status,
            environment_id=get_environment_id_from_session(c.session_id),
            differ=c.differ or None,
        )
        for c in containers
        if user_owns_session(user, c.session_id)
    ]


@router.get("/{session_id}")
async def get_session_info(
    session_id: str,
    user: User = Depends(get_current_user),
) -> SessionInfo:
    """Get details of a specific Claude session owned by the current user."""
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()

    try:
        container = await client.get_container(session_id)
    except OrchestratorError as e:
        logger.error(f"Failed to get session: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if container is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfo(
        session_id=container.session_id,
        container_name=container.container_name,
        status=container.status,
        environment_id=get_environment_id_from_session(container.session_id),
        differ=container.differ or None,
    )


@router.delete("/{session_id}")
async def kill_session(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Kill a Claude session owned by the current user."""
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()

    try:
        success = await client.delete_container(session_id)
    except OrchestratorError as e:
        logger.error(f"Failed to stop session: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "killed", "session_id": session_id}


@router.get("/{session_id}/panes")
async def list_panes(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """List tmux panes for a Claude session, plus container resource stats.

    Returns `{panes: [...], stats: {memory, cpu} | None}`. The pane list is
    used by the frontend to populate a pane switcher when multiple panes exist.
    """
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()
    try:
        return await client.relay_list_panes(session_id)
    except OrchestratorError as e:
        logger.warning(f"Failed to list panes for {session_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to list panes: {e}")


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
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify the container exists
    client = get_orchestrator_client()
    try:
        container = await client.get_container(session_id)
    except OrchestratorError as e:
        logger.error(f"Failed to get session for attach: {e}")
        raise HTTPException(status_code=503, detail=f"Orchestrator error: {e}")

    if container is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Construct attach commands from the container name
    container_name = container.container_name or f"claude-{session_id}"
    return {
        "attach_cmd": f"docker attach {container_name}",
        "exec_cmd": f"docker exec -it {container_name} bash",
    }


# -- File transfer ----------------------------------------------------------
#
# Streaming tar bytes to/from session containers. The MCP `claude` subserver
# mints presigned URLs pointing at these endpoints; the bundled session-files
# skill curls the URL with the token. Tokens are short-lived (~60s) and bind
# the URL to a specific user/session/path/action — so even if the URL leaks
# via logs, blast radius is one operation.
#
# Listing and URL minting are MCP-only (see `claude.session_list_dir` /
# `session_pull_url` / `session_push_url`); the frontend defaults to MCP tool
# calls and doesn't need duplicate REST endpoints for those.


def verify_transfer_token(token: str, expected_action: str) -> TransferTokenPayload:
    """Verify a token and check it grants the expected action on a session
    that the token's user actually owns. Raises HTTPException on failure.

    Re-validates ``session_id`` and ``path`` defensively. Mint-time validation
    already enforces these, but if ``TRANSFER_TOKEN_SECRET`` ever leaks (it
    falls back to ``SECRETS_ENCRYPTION_KEY`` in dev), or a future code path
    skips the mint helpers, this is the only line of defense before the
    orchestrator URL is constructed.
    """
    try:
        payload = verify_token(token)
    except TransferTokenError as e:
        msg = str(e)
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail=f"Invalid token: {msg}")

    if payload.action != expected_action:
        raise HTTPException(
            status_code=403,
            detail=f"Token does not grant {expected_action} access",
        )

    if not is_valid_session_id(payload.session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID in token")

    try:
        validate_transfer_path(payload.path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path in token")

    owner_id = get_user_id_from_session(payload.session_id)
    if owner_id != payload.user_id:
        raise HTTPException(
            status_code=403, detail="Token user does not own session"
        )
    return payload


def container_files_url(session_id: str, path: str) -> str:
    """Build the orchestrator URL for the container files endpoint.

    The path segment is percent-encoded with ``/`` preserved as the path
    separator. ``validate_transfer_path`` already rejects URL-meaningful
    characters (``?``/``#``/``%``/``;``/``&``/``\\``/space/CRLF/quote) at
    mint time, so this encoding is mostly belt-and-suspenders for non-ASCII
    filenames and any future path that slips through the validator.
    """
    clean = path.lstrip("/")
    return f"{settings.ORCHESTRATOR_BASE_URL}/containers/{session_id}/files/{quote(clean, safe='/')}"


@router.get("/transfer/pull")
async def transfer_pull(token: str = Query(...)) -> StreamingResponse:
    """Stream a tar of the file/directory referenced by a presigned token."""
    payload = verify_transfer_token(token, "read")

    upstream_url = container_files_url(payload.session_id, payload.path)
    transport = httpx.AsyncHTTPTransport(uds=ORCHESTRATOR_SOCKET)
    client = httpx.AsyncClient(transport=transport, timeout=600.0)

    try:
        upstream_resp = await client.send(
            client.build_request("GET", upstream_url),
            stream=True,
        )
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Cannot reach orchestrator")
    except Exception:
        await client.aclose()
        raise

    if upstream_resp.status_code >= 400:
        body = await upstream_resp.aread()
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=upstream_resp.status_code,
            detail=body.decode("utf-8", errors="replace") or "Orchestrator error",
        )

    media_type = upstream_resp.headers.get("content-type", "application/x-tar")
    raw_filename = payload.path.rstrip("/").split("/")[-1] or "session"
    # Sanitize before interpolating into Content-Disposition: refuse anything
    # that's not in a safe filename charset to prevent header injection /
    # response splitting (CRLF would have been blocked at mint, but defend
    # in depth — a future code path could skip mint validation).
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", raw_filename) or "session"
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}.tar"',
    }

    async def stream():
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream_resp.status_code,
        headers=headers,
        media_type=media_type,
    )


@router.put("/transfer/push")
async def transfer_push(request: Request) -> dict:
    """Accept a tar stream and extract it inside the session container."""
    auth = request.headers.get("authorization", "")
    parts = auth.split(None, 1)
    # Robustly parse "Bearer <token>" — split(None) handles repeated whitespace
    # but returns a single-element list for "Bearer" with no token, which is
    # why we check len + nonempty token explicitly here.
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = parts[1].strip()
    payload = verify_transfer_token(token, "write")

    # Buffer the request body at the API layer rather than streaming it to the
    # orchestrator. Two reasons:
    #   1. The orchestrator's Unix-socket HTTP parser reads `Content-Length`
    #      worth of bytes — it doesn't speak Transfer-Encoding: chunked. A
    #      curl-piped tar (`tar -cf - | curl --data-binary @-`) sends chunked
    #      with no Content-Length, so streaming through would hang/truncate.
    #   2. Mixing httpx's streaming `content=` with a manually-set
    #      Content-Length is ambiguous across httpx versions.
    # Trade-off: loses streaming on the push side. Acceptable here because
    # this endpoint is for skill-bundled tar uploads of small artifacts
    # (markdown reports, specs, configs). If anyone hits a buffer cap, we
    # extend the orchestrator HTTP parser to handle chunked encoding.
    body = await request.body()

    upstream_url = container_files_url(payload.session_id, payload.path)
    transport = httpx.AsyncHTTPTransport(uds=ORCHESTRATOR_SOCKET)
    client = httpx.AsyncClient(transport=transport, timeout=600.0)

    forward_headers = {
        "content-type": "application/x-tar",
        "content-length": str(len(body)),
    }

    try:
        upstream_resp = await client.send(
            client.build_request(
                "PUT",
                upstream_url,
                headers=forward_headers,
                content=body,
            ),
        )
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Cannot reach orchestrator")
    except Exception:
        # Mirror the pull-side pattern: any failure during send must not leak
        # the httpx client.
        await client.aclose()
        raise

    try:
        resp_body = await upstream_resp.aread()
    finally:
        await upstream_resp.aclose()
        await client.aclose()

    if upstream_resp.status_code >= 400:
        raise HTTPException(
            status_code=upstream_resp.status_code,
            detail=resp_body.decode("utf-8", errors="replace") or "Orchestrator error",
        )

    try:
        return json.loads(resp_body)
    except ValueError:
        return {"status": "ok"}


@router.get("/{session_id}/logs")
async def get_session_logs(
    session_id: str,
    tail: int = 100,
    user: User = Depends(get_current_user),
) -> dict:
    """Get logs for a Claude session.

    Reads logs from the host log directory where the orchestrator persists them.
    Logs are available even after container exit, enabling debugging of
    containers that crash immediately.

    Args:
        session_id: The session to get logs for
        tail: Number of lines from the end (default 100, 0 for all)

    Returns:
        - session_id: The session ID
        - source: "file"
        - logs: The log content
    """
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    client = get_orchestrator_client()
    result = await client.get_logs(session_id, tail=tail)

    if result is None:
        raise HTTPException(status_code=404, detail="No logs available")

    return result


# --- Differ proxy ---

# Headers that should not be forwarded between client and upstream
HOP_BY_HOP_HEADERS = frozenset({
    "host", "connection", "transfer-encoding", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
})


def rewrite_html_for_proxy(html: bytes, proxy_prefix: str) -> bytes:
    """Rewrite HTML so the SPA resolves all URLs through the proxy.

    1. Convert absolute paths in HTML attributes to relative so <base> applies.
    2. Inject a <base href> tag so relative URLs resolve through the proxy.
    3. Inject a JS shim that patches fetch() and EventSource to rewrite
       absolute paths through the proxy — this covers runtime API calls
       that <base href> can't reach.
    """
    base_href = proxy_prefix.rstrip("/") + "/"

    # Convert absolute paths to relative FIRST (before injecting base tag,
    # otherwise the regex would strip the / from the base tag's href too):
    #   href="/css/style.css" → href="css/style.css"
    #   src="/js/main.js"     → src="js/main.js"
    html = re.sub(rb'((?:href|src|action)=")/', rb'\1', html, flags=re.IGNORECASE)

    # Build the shim + base tag to inject after <head>
    shim = f"""\
<base href="{base_href}">
<script>
(function() {{
  var pfx = "{proxy_prefix}";
  var _fetch = window.fetch;
  window.fetch = function(url, opts) {{
    if (typeof url === "string" && url.startsWith("/")) url = pfx + url;
    return _fetch.call(this, url, opts);
  }};
  var _ES = window.EventSource;
  window.EventSource = function(url, opts) {{
    if (typeof url === "string" && url.startsWith("/")) url = pfx + url;
    var es = new _ES(url, opts);
    es.onerror = function() {{ /* SSE proxy may not be available; suppress */ }};
    return es;
  }};
  window.EventSource.prototype = _ES.prototype;
  window.EventSource.CONNECTING = _ES.CONNECTING;
  window.EventSource.OPEN = _ES.OPEN;
  window.EventSource.CLOSED = _ES.CLOSED;
}})();
</script>""".encode()

    for marker in [b"<head>", b"<HEAD>"]:
        if marker in html:
            html = html.replace(marker, marker + shim, 1)
            break
    else:
        html, _ = re.subn(
            rb"(<head[^>]*>)", rb"\1" + shim, html, count=1, flags=re.IGNORECASE,
        )

    return html


@router.api_route(
    "/{session_id}/differ/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_differ(
    session_id: str,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Reverse-proxy HTTP requests to the differ server inside a container.

    Streams responses so SSE (text/event-stream) works transparently.
    Injects a <base href> tag into HTML responses so the differ SPA
    resolves relative URLs through this proxy.
    """
    if not is_valid_session_id(session_id):
        raise HTTPException(status_code=404, detail="Invalid session ID format")
    if not user_owns_session(user, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    # Extract the differ subpath from raw_path (bytes) to preserve
    # percent-encoding — scope["path"] decodes %2F to /, which breaks
    # differ endpoints that embed filesystem paths in URL segments.
    prefix = f"/claude/{session_id}/differ/".encode()
    raw_path = request.scope.get("raw_path", b"")
    differ_path = raw_path.split(prefix, 1)[1].decode("ascii") if prefix in raw_path else path

    upstream_url = f"{settings.ORCHESTRATOR_BASE_URL}/containers/{session_id}/differ/{differ_path}"
    query_string = str(request.url.query)
    if query_string:
        upstream_url += f"?{query_string}"

    # Forward headers, stripping hop-by-hop
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    body = await request.body()

    transport = httpx.AsyncHTTPTransport(uds=ORCHESTRATOR_SOCKET)
    client = httpx.AsyncClient(transport=transport, timeout=120.0)

    try:
        upstream_resp = await client.send(
            client.build_request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                content=body if body else None,
            ),
            stream=True,
        )
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Cannot reach differ server")
    except Exception:
        await client.aclose()
        raise

    content_type = upstream_resp.headers.get("content-type", "")
    is_streaming = "text/event-stream" in content_type

    # Filter response headers
    resp_headers = {
        k: v for k, v in upstream_resp.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
        and k.lower() != "content-encoding"
        and k.lower() != "content-length"
    }

    if is_streaming:
        # SSE: stream chunks directly so events arrive in real-time.
        # The client and response are closed in the generator's finally block,
        # since they must outlive this function for streaming to work.
        async def stream_sse():
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            finally:
                await upstream_resp.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_sse(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type="text/event-stream",
        )

    # Non-streaming: buffer the response so we can modify HTML
    try:
        content = await upstream_resp.aread()
    finally:
        await upstream_resp.aclose()
        await client.aclose()

    # The differ returns its SPA HTML (200) for unknown paths as a catch-all.
    # If an API/event/oauth path gets HTML back, it's a false 200 — return 404
    # so the frontend JS gets a proper error instead of trying to JSON.parse HTML.
    if "text/html" in content_type and path and not path.endswith(("/", ".html", ".htm")):
        return StreamingResponse(
            iter([b'{"error": "not found"}']),
            status_code=404,
            media_type="application/json",
        )

    # Rewrite HTML so the differ SPA resolves URLs through this proxy path
    if "text/html" in content_type:
        proxy_prefix = f"/claude/{session_id}/differ"
        content = rewrite_html_for_proxy(content, proxy_prefix)

    return StreamingResponse(
        iter([content]),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=content_type.split(";")[0] if content_type else None,
    )


@router.websocket("/{session_id}/differ/{path:path}")
async def proxy_differ_ws(
    websocket: WebSocket,
    session_id: str,
    path: str,
    token: str = Query(default=""),
):
    """WebSocket proxy to the differ server inside a container.

    Relays frames bidirectionally between the client and the differ server
    through the orchestrator's WebSocket proxy.
    """
    if not is_valid_session_id(session_id):
        await websocket.close(code=4004, reason="Invalid session ID format")
        return

    # Authenticate
    with make_session() as db:
        user = get_user_from_token(token, db) if token else None
        if not user or not user_owns_session(user, session_id):
            await websocket.close(code=4004, reason="Session not found")
            return

    await websocket.accept()

    # Extract subpath from raw_path to preserve percent-encoding (see proxy_differ)
    prefix = f"/claude/{session_id}/differ/".encode()
    raw_path = websocket.scope.get("raw_path", b"")
    ws_subpath = raw_path.split(prefix, 1)[1].decode("ascii") if prefix in raw_path else path

    # Connect to orchestrator's WebSocket proxy via Unix socket
    orch_url = f"ws://orchestrator/containers/{session_id}/differ/{ws_subpath}"
    # Forward query params (excluding the auth token) to upstream
    qs_parts = [
        (k, v) for k, v in websocket.query_params.multi_items()
        if k != "token"
    ]
    if qs_parts:
        orch_url += "?" + urlencode(qs_parts)
    try:
        async with websockets.unix_connect(
            ORCHESTRATOR_SOCKET, orch_url
        ) as upstream:

            async def client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def upstream_to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception:
                    pass

            _, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    except Exception as e:
        logger.warning(f"Differ WebSocket proxy error for {session_id}: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


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
        pane_event = asyncio.Event()
        activity_state = {
            "last_input_time": 0.0,
            "input_event": input_event,
            "pane_event": pane_event,
        }

        # Run screen capture, input handling, and pane polling concurrently
        screen_task = asyncio.create_task(
            screen_capture_loop(websocket, session_id, client, activity_state, relay)
        )
        input_task = asyncio.create_task(
            input_handler_loop(websocket, session_id, relay, activity_state, client)
        )
        pane_task = asyncio.create_task(
            pane_poll_loop(websocket, session_id, client, activity_state)
        )

        # Wait for either main task to complete (usually screen_task on container exit,
        # or input_task on disconnect). Pane polling is auxiliary.
        _, pending = await asyncio.wait(
            [screen_task, input_task, pane_task],
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
