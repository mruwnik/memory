"""API endpoints for Cloud Claude Code session management.

Spawn, list, and kill Claude Code containers.

Sessions are managed by the Claude Session Orchestrator, which provides a
REST API over Unix socket for container and volume lifecycle management.

Session IDs are prefixed with user_id to enable authorization filtering:
  session_id = f"u{user_id}-{source}-{random_hex}"
"""

import asyncio
import contextlib
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlparse

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
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user, get_user_from_token, require_scope
from memory.api.transfer_tokens import (
    TransferTokenError,
    TransferTokenExpiredError,
    TransferTokenPayload,
    validate_transfer_path,
    verify_token,
)
from memory.common.access_control import has_admin_scope
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
from memory.common import paths, settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import ClaudeConfigSnapshot, ClaudeEnvironment, ScheduledTask, User
from memory.common.db.models.scheduled_tasks import TaskType, compute_next_cron
from memory.api.claude_environments import mark_environment_used
from memory.common.db.models.secrets import (
    extract as extract_secret,
    find_secret,
)

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


# --- Git remote URL validation ---------------------------------------------
#
# ``SpawnRequest.repo_url`` flows through to the spawned container as the
# ``GIT_REPO_URL`` env var, which the entrypoint feeds to ``git clone``.
# An unvalidated string here is a documented git-side attack surface:
#
# * Leading ``-`` makes git treat the URL as an option flag.
#   CVE-2017-1000117 / CVE-2018-17456 turned ``git clone --upload-pack=...``
#   into arbitrary command execution by getting the URL parsed as
#   ``--upload-pack=<cmd>``. Modern git refuses leading hyphens, but
#   reproducing that defense at the API boundary is cheap insurance and
#   blocks the same shape from reaching scheduled tasks.
#
# * ``ext::sh -c <cmd>`` invokes the ``ext`` git remote helper which
#   executes the rest as a shell command — no PATH lookup, no clone,
#   just ``sh -c``. ``transport-helper::`` and other custom-helper
#   schemes are similarly dangerous.
#
# * Inside the spawned container the SSH key + GITHUB_TOKEN are already in
#   the env, so a malicious clone can also exfiltrate via outbound HTTP/SSH.
#
# * ``git://`` (bare git wire protocol) provides no TLS, no integrity, and
#   no authentication, so an on-path attacker can swap the repo content
#   between this side and the operator's git server. GitHub disabled
#   ``git://`` access in 2022; most other forges followed. Symmetric risk
#   with ``http://`` (also rejected). Anonymous read-only access still
#   works via the scp-like ``user@host:path`` form over SSH.
#
# Defense: allowlist schemes to {https, ssh} (urlparse-able), accept
# the common scp-like ``user@host:path`` form for ssh, and reject leading
# hyphens / control characters / over-length values.
#
# The schedule path (``ScheduleRequest.spawn_config: SpawnRequest``) reuses
# this validator transitively because Pydantic validates nested models on
# construction. So a malicious ``repo_url`` is rejected at the same boundary
# whether it arrives via ``POST /spawn`` or via a stored ``POST /schedule``.

GIT_REPO_URL_SCHEMES: frozenset[str] = frozenset({"https", "ssh"})
GIT_REPO_URL_MAX_LEN = 2000  # generous; attacker is the one paying the cost
GIT_REPO_URL_FORBIDDEN_CHARS: frozenset[str] = frozenset({"\x00", "\r", "\n"})

# scp-like SSH form that git understands natively: ``user@host:path``.
# Username is alphanumeric+._-, host is alphanumeric+.-, path is anything
# without control chars (already pre-screened above). This regex is
# intentionally narrow: no whitespace, no scheme separator beyond the
# single ``:``, no ``--`` smuggling because the user/host segments don't
# allow ``-`` as a leading char (the path can but the leading-``-`` check
# upstream would have already caught a ``-foo@host:bar`` shape).
_SCP_LIKE_GIT_URL = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*:[^\s\x00\r\n]+$"
)


def validate_git_repo_url(url: str) -> str:
    """Validate a git remote URL before it flows to ``$GIT_REPO_URL``.

    Returns the URL unchanged on success; raises ``ValueError`` on failure
    (Pydantic will surface that as 422; calling code can also catch and
    re-raise as 400 if it prefers).

    Acceptance criteria (any one path passes):

    * URL with explicit scheme in ``GIT_REPO_URL_SCHEMES`` AND a hostname
    * scp-like form ``user@host:path``

    Common-rejection criteria (apply in either branch):

    * leading ``-`` (CVE-style flag injection)
    * ``\\x00`` / CR / LF anywhere (smuggling)
    * length above ``GIT_REPO_URL_MAX_LEN``
    * empty string
    * any other ``ext::``, ``transport-helper::``, ``http://``,
      ``git://``, ``file://`` URL (non-allowlisted scheme)
    """
    if not url:
        raise ValueError("repo_url must not be empty")
    if len(url) > GIT_REPO_URL_MAX_LEN:
        raise ValueError(
            f"repo_url is too long ({len(url)} chars; max {GIT_REPO_URL_MAX_LEN})"
        )
    for ch in GIT_REPO_URL_FORBIDDEN_CHARS:
        if ch in url:
            raise ValueError(
                "repo_url must not contain NUL or CR/LF (control-character smuggling)"
            )
    if url.startswith("-"):
        # Defense against ``git clone <flag>`` interpretation. Git's own
        # safety net catches this in newer versions, but reproducing the
        # check at the API boundary is cheap and blocks the same shape
        # from being stored via the schedule path and replayed later.
        raise ValueError(
            "repo_url must not start with '-' (looks like a CLI flag, not a URL)"
        )

    parsed = urlparse(url)
    if parsed.scheme:
        if parsed.scheme not in GIT_REPO_URL_SCHEMES:
            raise ValueError(
                f"repo_url scheme must be one of {sorted(GIT_REPO_URL_SCHEMES)} "
                f"(got {parsed.scheme!r}); ext::, transport-helper::, "
                f"git:// (no TLS / MITM-able) and file:// are deliberately "
                f"excluded"
            )
        if not parsed.hostname:
            raise ValueError("repo_url must include a hostname")
        return url

    # No scheme parsed: try the scp-like ``user@host:path`` form.
    if _SCP_LIKE_GIT_URL.match(url):
        return url

    raise ValueError(
        "repo_url must be https://, ssh://, git://, or scp-like "
        "(user@host:path) — got an unrecognised shape"
    )


def make_session_id(
    user_id: int,
    *,
    environment_id: int | None = None,
    snapshot_id: int | None = None,
) -> str:
    """Generate a session ID that includes user ownership and source info.

    Format: u{user_id}-{source}-{random_hex}
    Where source is e{env_id} for environments or s{snap_id} for snapshots.

    The random suffix is 16 bytes / 32 hex chars (128 bits). The previous
    6-byte (48-bit) suffix was below the modern bar for unguessable
    session tokens — the suffix doubles as a Docker container hostname
    and as a key in orchestrator URLs, so a sufficiently determined
    attacker enumerating active container suffixes was a credible
    threat. 128 bits matches the standard ``secrets.token_hex(16)``
    recommendation. The matching regexes in ``auth.py`` and below
    enforce a floor of 32 hex chars in lockstep.
    """
    if environment_id is not None:
        source = f"e{environment_id}"
    elif snapshot_id is not None:
        source = f"s{snapshot_id}"
    else:
        source = "x"  # Unknown source (shouldn't happen)
    return f"u{user_id}-{source}-{secrets.token_hex(16)}"


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


async def open_orchestrator_uds_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    timeout: float = 600.0,
    stream: bool = False,
    cant_reach_detail: str = "Cannot reach orchestrator",
) -> tuple[httpx.Response, httpx.AsyncClient]:
    """Open an httpx-over-Unix-socket request to the orchestrator and
    return the response + client.

    The orchestrator is reachable only via a Unix-domain socket, so
    every transfer/proxy endpoint had to hand-roll the same
    transport+client+send+ConnectError→502 boilerplate. The three
    historical copies (transfer_pull, transfer_push, proxy_differ) had
    drifted slightly in their aclose() orderings — fertile ground for
    leaking httpx clients on error.

    Caller MUST close the returned ``client`` (and the ``upstream_resp``
    if not streaming). The helper handles the connect-error path
    itself, so on a successful return the caller has both objects to
    own; on a 502 the helper has already cleaned up.

    Args:
        method, url, headers, content, timeout: passed through to
            ``client.build_request``.
        stream: forwarded to ``client.send`` so streaming endpoints
            can return without buffering the whole body.
        cant_reach_detail: HTTP detail to use on ``httpx.ConnectError``.
            Differs across endpoints (e.g. "Cannot reach differ
            server" for the differ proxy).
    """
    transport = httpx.AsyncHTTPTransport(uds=ORCHESTRATOR_SOCKET)
    client = httpx.AsyncClient(transport=transport, timeout=timeout)
    try:
        upstream_resp = await client.send(
            client.build_request(
                method=method,
                url=url,
                headers=headers,
                content=content,
            ),
            stream=stream,
        )
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(status_code=502, detail=cant_reach_detail)
    except Exception:
        # Mirrors the historical behaviour: any failure during send
        # must not leak the httpx client.
        await client.aclose()
        raise
    return upstream_resp, client


@contextlib.asynccontextmanager
async def map_orchestrator_errors(
    *,
    log_msg: str | None = None,
    log_level: int = logging.ERROR,
    status_code: int | None = None,
    detail_template: str | None = None,
) -> AsyncIterator[None]:
    """Translate ``OrchestratorError`` raised inside the block into ``HTTPException``.

    Args:
        log_msg: If set, log this prefix at ``log_level`` along with the
            error before re-raising.
        log_level: Log level for ``log_msg`` (default ERROR).
        status_code: HTTP status to use. ``None`` (the default) means use
            ``e.status_code`` if set, otherwise 502 — i.e. pass through
            whatever the orchestrator told us.
        detail_template: Template string for the HTTP detail; ``{e}`` is
            substituted with the OrchestratorError instance. ``None``
            means use ``str(e)``.

    Use the ``with`` form at every previous copy of the
    "try/except OrchestratorError -> HTTPException" pattern; the only
    site this helper is *not* suitable for is the snapshot-cleanup
    case in ``spawn_session``, which has bespoke teardown logic
    interleaved with the re-raise.
    """
    try:
        yield
    except OrchestratorError as e:
        if log_msg is not None:
            logger.log(log_level, "%s: %s", log_msg, e)
        final_status = (
            status_code
            if status_code is not None
            else (e.status_code or 502)
        )
        final_detail = (
            detail_template.format(e=e) if detail_template else str(e)
        )
        raise HTTPException(status_code=final_status, detail=final_detail) from e


# Strict session-id regex used by both is_valid_session_id and the
# require_session_access defense-in-depth check below.
_SESSION_ID_RE = re.compile(r"^u\d+-(e\d+|s\d+|x)-[a-fA-F0-9]{32,}$")


def is_valid_session_id(session_id: str) -> bool:
    """Validate session_id format (defense in depth).

    Format: ``u{user_id}-{source}-{hex}`` where source is one of:
      - ``e{env_id}``  for environment-based sessions   (e.g. u123-e456-abc123)
      - ``s{snap_id}`` for snapshot-based sessions      (e.g. u123-s789-abc123)
      - ``x``          for sessions without snapshot/environment (e.g. u123-x-abc123)
    """
    return bool(_SESSION_ID_RE.match(session_id))


class UnsafeSubpathError(Exception):
    """Internal sentinel: differ subpath contains a traversal or empty segment."""


def check_no_traversal(differ_path: str) -> None:
    """Raise ``UnsafeSubpathError`` if ``differ_path`` is unsafe to forward.

    The subpath comes from the raw request bytes and may still contain
    percent-encoded characters.  An attacker can smuggle traversal sequences
    as ``%2e%2e``, ``..`` plain, or doubly-encoded as ``%252e%252e``.  Decode
    in a fixed-point loop so all forms are caught: each iteration unwraps
    one layer of percent-encoding, and we stop once the string stops
    changing (which is the only safe way to know there's no more decoding
    a downstream proxy could apply).

    Empty segments (``a//b``) are also rejected because httpx (or a
    downstream proxy) may normalise them into a different orchestrator
    endpoint than what the user typed.

    Shared between the HTTP and WebSocket differ proxies so both surfaces
    stay in lock-step on what counts as "safe to forward".
    """
    prev = differ_path
    while True:
        decoded = unquote(prev)
        if decoded == prev:
            break
        prev = decoded
    for segment in decoded.split("/"):
        if segment in ("", ".", ".."):
            raise UnsafeSubpathError(segment)


def validate_differ_subpath(differ_path: str) -> None:
    """HTTP-shaped wrapper around :func:`check_no_traversal`.

    Raises HTTPException 400 if the path contains traversal or empty segments.
    """
    try:
        check_no_traversal(differ_path)
    except UnsafeSubpathError as exc:
        raise HTTPException(
            status_code=400, detail="Path traversal not allowed"
        ) from exc


def require_session_access(user: User, session_id: str) -> None:
    """Raise 404 unless ``session_id`` is well-formed AND owned by ``user``.

    Combines format validation (defense-in-depth against path traversal
    when the id flows into orchestrator URL paths or host filesystem
    operations like ``OrchestratorClient.get_logs``) with ownership.
    Both failures map to 404 so we don't leak which sessions exist.
    """
    if not _SESSION_ID_RE.match(session_id) or not user_owns_session(
        user, session_id
    ):
        raise HTTPException(status_code=404, detail="Session not found")


# WebSocket-specific session-access close code. Distinct from the HTTP
# 404 because WebSockets don't have status codes — they have close
# frames. 4004 is in the application-defined range (4000-4999) and the
# message stays uniform across format-failure and ownership-failure so
# the client can't distinguish "this session ID is invalid syntax" from
# "this session exists but isn't yours" — same info-disclosure rule as
# the HTTP path.
_WEBSOCKET_SESSION_NOT_FOUND_CODE = 4004
_WEBSOCKET_SESSION_NOT_FOUND_REASON = "Session not found"


async def require_session_access_websocket(
    websocket: WebSocket, user: User | None, session_id: str
) -> bool:
    """WebSocket analog of ``require_session_access``.

    Closes ``websocket`` with a uniform 4004 ``Session not found`` frame
    and returns ``False`` if any of these fails:
      * ``session_id`` doesn't match ``_SESSION_ID_RE`` (path-traversal
        defense-in-depth — the id flows into orchestrator URL paths)
      * ``user`` is ``None`` (token didn't authenticate to a user)
      * ``user_owns_session(user, session_id)`` is False

    Returns ``True`` if the caller should proceed (the session is
    well-formed, owned by ``user``, and the websocket has NOT been
    closed). The caller is then responsible for ``await
    websocket.accept()`` and the rest of the handshake.

    Why this exists: three open-coded copies of the same checks drifted
    in ``proxy_differ`` (HTTP), ``proxy_differ_ws``, and the terminal
    WebSocket — each with slightly different close codes / messages /
    failure ordering. The HTTP variant additionally returned distinct
    400/403 status codes pre-collapse, which leaked "session exists vs.
    syntactically invalid" to unauthenticated callers. Centralising
    here pins the "single 4004 + uniform reason" property for every WS
    route, so a future contributor can't accidentally split the failure
    modes apart by editing one site.
    """
    if (
        user is None
        or not _SESSION_ID_RE.match(session_id)
        or not user_owns_session(user, session_id)
    ):
        await websocket.close(
            code=_WEBSOCKET_SESSION_NOT_FOUND_CODE,
            reason=_WEBSOCKET_SESSION_NOT_FOUND_REASON,
        )
        return False
    return True


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

    @field_validator("repo_url", mode="before")
    @classmethod
    def _validate_repo_url(cls, v: object) -> object:
        """Reject git-flag-injection / non-allowlisted scheme shapes in
        ``repo_url`` before the value flows to ``$GIT_REPO_URL`` in the
        spawned container.

        ``mode="before"`` so we see the raw client value (and short-circuit
        non-string types into Pydantic's normal type-error path). The
        helper is also called by the ``/schedule`` endpoint via this same
        Pydantic validation chain (``ScheduleRequest.spawn_config:
        SpawnRequest``), so a malicious ``repo_url`` cannot be stored as a
        scheduled task and replayed past the boundary.
        """
        if v is None:
            return None
        if not isinstance(v, str):
            # Defer the type error to Pydantic's normal coercion path so
            # callers see a clean ``Input should be a valid string`` rather
            # than a confusing assertion from the URL parser.
            return v
        return validate_git_repo_url(v)

    @model_validator(mode="after")
    def check_source_mutual_exclusivity(self) -> "SpawnRequest":
        """Ensure exactly one of snapshot_id or environment_id is provided.

        NOTE: explicit ``is None`` checks rather than truthy checks. Integer
        ``0`` is falsy in Python, so ``if self.snapshot_id`` would treat
        ``snapshot_id=0`` as unset and silently pass through to the
        environment branch. DB autoincrement starts at 1 in practice, but
        the validator's contract is "exactly one set" — enforce it.
        """
        if self.snapshot_id is not None and self.environment_id is not None:
            raise ValueError("Cannot specify both snapshot_id and environment_id")
        if self.snapshot_id is None and self.environment_id is None:
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


@router.get("/stats")
async def session_stats(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Live container resource snapshot.

    Admins get the full orchestrator payload. Non-admins get only their own
    containers and no `global` block (orchestrator-wide capacity is an
    operator concern, not a user-facing one).
    """
    client = get_orchestrator_client()
    async with map_orchestrator_errors():
        snapshot = await client.stats()

    if has_admin_scope(user):
        return snapshot

    own = [
        c for c in snapshot.get("containers", [])
        if user_owns_session(user, c.get("id", ""))
    ]
    return {"ts": snapshot.get("ts"), "containers": own}


@router.get("/stats/history")
async def session_stats_history(
    session_id: str | None = Query(None),
    since: str | None = Query(None),
    max_points: int = Query(1000, ge=1, le=10000, alias="max"),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Time-series ring buffer for the sampler.

    Admins see all sessions (or filter to one with `session_id`).
    Non-admins must specify `session_id` — letting them call without one
    would force the orchestrator to materialize every user's history
    just to filter it back down here, which is a soft-DoS vector.
    """
    is_admin = has_admin_scope(user)
    if not is_admin and not session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id required for non-admin callers",
        )
    if session_id:
        if is_admin:
            # Admin: any well-formed id is permitted; format check still
            # required because session_id is forwarded into orchestrator URLs.
            if not _SESSION_ID_RE.match(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
        else:
            require_session_access(user, session_id)

    client = get_orchestrator_client()
    async with map_orchestrator_errors():
        result = await client.stats_history(
            session_id=session_id, since=since, max_points=max_points
        )

    return result


@router.get("/{session_id}/stats")
async def container_stats(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Live stats for a single container.

    Returns 404 both for unknown sessions and for sessions the caller
    doesn't own — same shape as kill_session and friends.
    """
    if has_admin_scope(user):
        # Admins can fetch any session's stats, but the id still has to
        # parse — orchestrator URL paths are derived from it.
        if not _SESSION_ID_RE.match(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        require_session_access(user, session_id)

    client = get_orchestrator_client()
    async with map_orchestrator_errors():
        data = await client.container_stats(session_id)

    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if data.get("id") != session_id:
        raise HTTPException(
            status_code=502, detail="orchestrator returned mismatched session"
        )
    return data


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

    if request.snapshot_id is not None:
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
        relative_path = paths.to_db_filename(snapshot_path)
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

    # Set run ID for branch naming (used by entrypoint to create
    # claude/<run_id> branch). Sanitise the user-supplied value so a
    # caller can't smuggle shell metacharacters or a leading hyphen
    # past the entrypoint's `git checkout -b`. Falls back to session_id
    # (already format-validated upstream) if the slug strips to empty.
    if request.run_id:
        slug = re.sub(r"[^a-z0-9-]", "-", request.run_id.lower())
        slug = re.sub(r"-{2,}", "-", slug).strip("-")[:50]
        env["CLAUDE_RUN_ID"] = slug or session_id
    else:
        env["CLAUDE_RUN_ID"] = session_id

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

    # Track a freshly-created snapshot volume so we can roll back if container
    # creation fails. Only the snapshot path is owned by this call; the
    # environment-mode volume_name points at the user's persistent volume and
    # MUST NOT be deleted on failure.
    created_snapshot_volume: str | None = None
    try:
        # For snapshot-based sessions, create a temporary volume and init from snapshot
        if host_snapshot_path:
            volume_name = f"claude-snap-{session_id}"
            await client.create_initialized_volume(volume_name, host_snapshot_path)
            created_snapshot_volume = volume_name

        result = await client.create_container(
            session_id,
            image=image,
            volume=volume_name,
            env=env,
            networks=networks,
        )
    except OrchestratorError as e:
        logger.error(f"Failed to create session: {e}")
        if created_snapshot_volume is not None:
            # Best-effort cleanup of the orphan snapshot volume. The volume
            # was created exclusively for the container we just failed to
            # spawn — leaving it behind accumulates `claude-snap-*` volumes
            # on the host with no way to attribute them later.
            try:
                await client.delete_volume(created_snapshot_volume)
            except Exception:
                logger.exception(
                    "Failed to clean up orphan snapshot volume %s after "
                    "create_container failure",
                    created_snapshot_volume,
                )
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


def validate_scheduled_secret_refs(
    db: DBSession, user_id: int, spawn_config: "SpawnRequest"
) -> None:
    """Reject scheduled-session requests whose token fields don't resolve
    to a stored Secret row.

    Unlike the spawn-time path (which uses ``extract_secret``'s silent
    literal-fallback by design), scheduled-session token values are
    persisted in ``ScheduledTask.data``. Accepting literals here means a
    typo ("gihub-token") gets stored verbatim and shipped to the
    container later as a literal — turning a misspelled secret name into
    a plaintext credential leak in the DB.

    The error message intentionally does NOT echo ``token_value``. The
    field accepts either a secret-name OR a literal token at runtime; if
    a future contributor relaxes that contract, an unintended echo would
    become a credential-reflection vector via 4xx response bodies.
    """
    for token_field in ("github_token", "github_token_write"):
        token_value = getattr(spawn_config, token_field, None)
        if token_value and find_secret(db, user_id, token_value) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Secret reference for '{token_field}' not found. "
                    "Scheduled sessions require a stored-secret name "
                    "(create one via /secrets); literal tokens are not "
                    "accepted because they would be persisted in "
                    "plaintext in the schedule's data column."
                ),
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

    # Validate token fields against stored secrets (raises 400 on miss).
    validate_scheduled_secret_refs(db, user.id, request.spawn_config)

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

    async with map_orchestrator_errors(
        log_msg="Failed to list sessions",
        status_code=503,
        detail_template="Orchestrator error: {e}",
    ):
        containers = await client.list_containers()

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
    require_session_access(user, session_id)

    client = get_orchestrator_client()

    async with map_orchestrator_errors(
        log_msg="Failed to get session",
        status_code=503,
        detail_template="Orchestrator error: {e}",
    ):
        container = await client.get_container(session_id)

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
    require_session_access(user, session_id)

    client = get_orchestrator_client()

    async with map_orchestrator_errors(
        log_msg="Failed to stop session",
        status_code=503,
        detail_template="Orchestrator error: {e}",
    ):
        success = await client.delete_container(session_id)

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
    require_session_access(user, session_id)

    client = get_orchestrator_client()
    async with map_orchestrator_errors(
        log_msg=f"Failed to list panes for {session_id}",
        log_level=logging.WARNING,
        status_code=502,
        detail_template="Failed to list panes: {e}",
    ):
        return await client.relay_list_panes(session_id)


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
    require_session_access(user, session_id)

    # Verify the container exists
    client = get_orchestrator_client()
    async with map_orchestrator_errors(
        log_msg="Failed to get session for attach",
        status_code=503,
        detail_template="Orchestrator error: {e}",
    ):
        container = await client.get_container(session_id)

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
    already enforces these, but if ``TRANSFER_TOKEN_SECRET`` ever leaks
    (or a future code path skips the mint helpers), this is the only
    line of defense before the orchestrator URL is constructed.

    Note: the previous comment claimed ``TRANSFER_TOKEN_SECRET`` "falls
    back to ``SECRETS_ENCRYPTION_KEY`` in dev" — that fallback was the
    raw key, which exposed the at-rest AES-GCM secret to HMAC-tag-based
    side channels. ``settings.py`` now derives the transfer secret from
    ``SECRETS_ENCRYPTION_KEY`` via HKDF-SHA256 with a domain-separating
    ``info`` string, so leaking the transfer secret no longer compromises
    the at-rest encryption key (and vice versa).
    """
    try:
        payload = verify_token(token)
    except TransferTokenExpiredError:
        # Distinct exception type means we can't accidentally classify a
        # tampered/malformed token as expired (the previous substring sniff
        # over `str(exc)` was vulnerable to "expired" appearing in unrelated
        # error text — and it leaked the inner reason via the response).
        raise HTTPException(status_code=401, detail="Token expired")
    except TransferTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

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

    The orchestrator's GET/PUT file endpoints take the in-container path as
    a ``?path=…`` **query string**, not as part of the URL path —
    ``…/files/{path}`` is not a registered route and returns 404. (The
    list endpoint uses the same query-string convention; see
    ``orchestrator_client.list_dir``.) ``quote(path, safe="")`` percent-
    encodes the leading slash and any other URL-meaningful chars so the
    full absolute path round-trips through the orchestrator.

    ``validate_transfer_path`` already rejects URL-meaningful characters
    (``?``/``#``/``%``/``;``/``&``/``\\``/space/CRLF/quote) at mint time,
    so the encoding here is belt-and-suspenders for non-ASCII filenames.
    """
    return (
        f"{settings.ORCHESTRATOR_BASE_URL}/containers/{session_id}/files"
        f"?path={quote(path, safe='')}"
    )


@router.get("/transfer/pull")
async def transfer_pull(token: str = Query(...)) -> StreamingResponse:
    """Stream a tar of the file/directory referenced by a presigned token."""
    payload = verify_transfer_token(token, "read")

    upstream_url = container_files_url(payload.session_id, payload.path)
    upstream_resp, client = await open_orchestrator_uds_request(
        method="GET",
        url=upstream_url,
        stream=True,
    )

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

    # Cheap pre-check: if the client honestly declared a body larger than
    # the cap, refuse before reading anything.
    cap = settings.MAX_TRANSFER_PUSH_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large. Maximum size is {cap // (1024 * 1024)} MB",
        )

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
    #
    # Streamed read with running cap so a chunked upload (no Content-Length)
    # or a Content-Length-lying client can't drive an unbounded allocation.
    body_chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large. Maximum size is {cap // (1024 * 1024)} MB",
            )
        body_chunks.append(chunk)
    body = b"".join(body_chunks)

    upstream_url = container_files_url(payload.session_id, payload.path)

    forward_headers = {
        "content-type": "application/x-tar",
        "content-length": str(len(body)),
    }

    upstream_resp, client = await open_orchestrator_uds_request(
        method="PUT",
        url=upstream_url,
        headers=forward_headers,
        content=body,
    )

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
    # Format check is critical here: get_logs constructs LOG_DIR/{session_id}.log
    # on the host, so an unvalidated session_id is a path-traversal sink.
    require_session_access(user, session_id)

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
    # Centralised: format check + ownership + uniform 404 reason so we
    # don't leak "session exists but isn't yours" vs. "session ID is
    # syntactically invalid" to unauthorised callers.
    require_session_access(user, session_id)

    # Extract the differ subpath from raw_path (bytes) to preserve
    # percent-encoding — scope["path"] decodes %2F to /, which breaks
    # differ endpoints that embed filesystem paths in URL segments.
    prefix = f"/claude/{session_id}/differ/".encode()
    raw_path = request.scope.get("raw_path", b"")
    differ_path = raw_path.split(prefix, 1)[1].decode("ascii") if prefix in raw_path else path

    validate_differ_subpath(differ_path)
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

    upstream_resp, client = await open_orchestrator_uds_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=body if body else None,
        timeout=120.0,
        stream=True,
        cant_reach_detail="Cannot reach differ server",
    )

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
    # Authenticate first, then route through the centralised helper so
    # format-failure / no-user / wrong-owner all surface as the same
    # 4004 close frame ("Session not found"). Distinguishing them would
    # leak existence to unauthenticated callers.
    with make_session() as db:
        user = get_user_from_token(token, db) if token else None
    if not await require_session_access_websocket(websocket, user, session_id):
        return

    # Extract subpath from raw_path to preserve percent-encoding (see proxy_differ)
    # Must happen BEFORE accept() so we can close with an error code on bad input.
    prefix = f"/claude/{session_id}/differ/".encode()
    raw_path = websocket.scope.get("raw_path", b"")
    ws_subpath = raw_path.split(prefix, 1)[1].decode("ascii") if prefix in raw_path else path

    # Reject path traversal attempts (same logic as HTTP differ proxy).
    try:
        check_no_traversal(ws_subpath)
    except UnsafeSubpathError:
        await websocket.close(code=4000, reason="Path traversal not allowed")
        return

    await websocket.accept()

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
    # Authenticate first, then route through the centralised helper.
    #
    # Note the deliberate distinction here: an *invalid token* still uses
    # a separate 4001 close code (clients need to distinguish "your
    # token is expired, get a new one" from "the session you asked for
    # doesn't exist") — but everything else (bad session-id format,
    # token-without-user, ownership mismatch) collapses to a uniform
    # 4004 ``Session not found`` so we don't leak existence to callers
    # who hold a valid token but for the wrong session.
    with make_session() as db:
        user = get_user_from_token(token, db)
    if user is None:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return
    if not await require_session_access_websocket(websocket, user, session_id):
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
