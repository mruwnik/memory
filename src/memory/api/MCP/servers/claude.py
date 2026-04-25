"""MCP subserver for cloud-claude session file transfer.

Tools here orchestrate file transfer between the user's local machine and a
remote claude-cloud session container. Bytes don't flow through MCP; instead
these tools mint short-lived signed URLs that a bundled bash script (in the
session-files skill) can curl directly. This sidesteps MCP payload caps and
gives natural streaming for arbitrary file types and folders.
"""

import logging
from typing import Any

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.api.cloud_claude import (
    get_user_id_from_session,
    is_valid_session_id,
)
from memory.api.orchestrator_client import (
    OrchestratorError,
    get_orchestrator_client,
)
from memory.api.transfer_tokens import (
    mint_transfer_url,
    normalize_abs_path,
    validate_transfer_path,
)
from memory.common import settings
from memory.common.scopes import SCOPE_READ, SCOPE_WRITE

logger = logging.getLogger(__name__)

claude_mcp = FastMCP("memory-claude")


def user_owns(user_id: int, session_id: str) -> bool:
    return get_user_id_from_session(session_id) == user_id


async def list_user_sessions(user_id: int) -> list[dict[str, Any]]:
    """Return all orchestrator-tracked sessions belonging to this user."""
    client = get_orchestrator_client()
    try:
        containers = await client.list_containers()
    except OrchestratorError as e:
        logger.warning(f"orchestrator list_containers failed: {e}")
        return []
    return [
        {
            "session_id": c.session_id,
            "container_name": c.container_name,
            "status": c.status,
            "image": c.image,
        }
        for c in containers
        if user_owns(user_id, c.session_id)
    ]


async def resolve_session_id(user_id: int, session_id: str) -> str:
    """Resolve the "latest" sentinel to a concrete session_id.

    Prefers running sessions; falls back to recently listed ones if none are
    running. Raises ValueError if the user has no sessions.

    NOTE: The orchestrator's ``GET /containers`` response does not currently
    expose ``created_at``/``last_used_at`` timestamps, so true "most recent"
    semantics (per the plan doc) aren't available. Until the orchestrator
    grows a timestamp, we sort by the trailing ``random_hex`` suffix only.
    Session IDs have the shape ``u<user>-<src>-<random_hex>`` where ``<src>``
    is ``e<env>``, ``s<snap>``, or ``x``. Sorting on the random suffix (not
    the full session_id) avoids a hard cross-source bias — sorting the full
    string would always prefer ``s`` over ``e`` over ``x`` lexically, so a
    user with both an env-source and a snapshot-source session would never
    have the env one win. The random suffix is the only chronologically
    meaningful component anyway: two sessions started a minute apart can
    still resolve in either direction (the suffix is random hex), but at
    least the result doesn't depend on which source the session came from.

    TODO(orchestrator): expose ``created_at``/``last_used_at`` on
    ``SessionInfo`` so we can do a real most-recent sort here.
    """
    if session_id != "latest":
        return session_id

    sessions = await list_user_sessions(user_id)
    if not sessions:
        raise ValueError("No active session found for this user")

    running = [s for s in sessions if s.get("status") == "running"]
    pool = running or sessions
    # Sort by the trailing random_hex suffix so source letter (e/s/x) doesn't
    # bias the result. See docstring for the full rationale.
    pool_sorted = sorted(
        pool, key=lambda s: s["session_id"].rsplit("-", 1)[-1], reverse=True
    )
    return pool_sorted[0]["session_id"]


def mint_for(
    action: str, user_id: int, session_id: str, path: str
) -> dict[str, Any]:
    """Mint a transfer URL using the configured public ``SERVER_URL``.

    Wraps the shared :func:`mint_transfer_url` helper. ``ValueError`` from
    path validation is surfaced to the caller (the MCP tool); FastMCP turns
    it into a tool error visible to the model.
    """
    return mint_transfer_url(
        base_url=settings.SERVER_URL,
        user_id=user_id,
        session_id=session_id,
        path=path,
        action=action,  # type: ignore[arg-type]
    )


@claude_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def session_list() -> list[dict[str, Any]]:
    """List the current user's claude-cloud sessions (running and recent).

    Returns one entry per session containing session_id, container_name,
    status (running/exited/...), and image. Empty list if the user has no
    sessions or isn't authenticated.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        return []
    return await list_user_sessions(user.id)


@claude_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def session_list_dir(
    session_id: str,
    path: str = "/workspace",
    recursive: bool = False,
    max_entries: int = 1000,
) -> dict[str, Any]:
    """List entries in a directory inside a claude-cloud session container.

    Args:
        session_id: Session ID, or "latest" for the user's most-recent session.
        path: Absolute path inside the container (defaults to /workspace).
        recursive: If True, walk subdirectories. Defaults to false (top-level only).
        max_entries: Cap on entries returned (server-side safety limit).

    Returns:
        {"path", "entries": [{name, type, size, mtime, ...}, ...], "truncated"}
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")

    sid = await resolve_session_id(user.id, session_id)
    if not is_valid_session_id(sid) or not user_owns(user.id, sid):
        raise ValueError("Session not found")

    validate_transfer_path(path)
    abs_path = normalize_abs_path(path)
    client = get_orchestrator_client()
    try:
        return await client.list_dir(
            sid, abs_path, recursive=recursive, max_entries=max_entries
        )
    except OrchestratorError as e:
        raise ValueError(f"Orchestrator error: {e}")


@claude_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def session_pull_url(
    session_id: str,
    path: str,
) -> dict[str, Any]:
    """Mint a short-lived URL for downloading a file or directory from a session.

    The URL returns a tar stream (single file = one-entry tar; directory =
    full tree tar). The bundled session-files skill curls the URL and untars
    locally.

    Args:
        session_id: Session ID, or "latest" for the user's most-recent session.
        path: Absolute path to the file or directory inside the container.

    Returns:
        {"url": "...?token=...", "expires_in": 60}
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")

    sid = await resolve_session_id(user.id, session_id)
    if not is_valid_session_id(sid) or not user_owns(user.id, sid):
        raise ValueError("Session not found")

    return mint_for("read", user.id, sid, path)


@claude_mcp.tool()
@visible_when(require_scopes(SCOPE_WRITE))
async def session_push_url(
    session_id: str,
    path: str,
) -> dict[str, Any]:
    """Mint a short-lived URL+token for uploading files/directories into a session.

    The skill tars the local source and PUTs to the URL with the token in
    the Authorization header. Path must be a directory inside the container
    (the tar is extracted at that path).

    Args:
        session_id: Session ID, or "latest" for the user's most-recent session.
        path: Destination directory inside the container.

    Returns:
        {"url": "...", "token": "...", "expires_in": 60}
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")

    sid = await resolve_session_id(user.id, session_id)
    if not is_valid_session_id(sid) or not user_owns(user.id, sid):
        raise ValueError("Session not found")

    return mint_for("write", user.id, sid, path)
