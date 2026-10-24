"""Async HTTP client for the Claude Session Orchestrator.

The orchestrator manages Claude containers and volumes via a REST API
served over a Unix socket. This client wraps the HTTP endpoints into
a clean interface for the Memory API.

See: compose/orchestrator/API.md for the full endpoint reference.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

ORCHESTRATOR_SOCKET = os.environ.get(
    "ORCHESTRATOR_SOCKET", "/var/run/claude-sessions/orchestrator.sock"
)

# Timeout for HTTP operations in seconds
HTTP_TIMEOUT = 30

# Log directory on host where orchestrator writes session logs
LOG_DIR = Path("/var/log/claude-sessions")

# Strict session-id format. Duplicated from cloud_claude._SESSION_ID_RE so
# the orchestrator client doesn't have to import the API layer (and so this
# module can self-validate inputs that hit the host filesystem).
_SESSION_ID_RE = re.compile(r"^u\d+-(e\d+|s\d+|x)-[a-fA-F0-9]+$")

# Hard upper bound on bytes returned for a logs request. Even with a
# correct seek-from-end tail, an attacker (or a chatty session) could
# request ``tail=10_000_000`` and exhaust API memory. Truncate the
# returned payload to keep the response bounded regardless of caller.
LOG_TAIL_MAX_BYTES = int(os.getenv("LOG_TAIL_MAX_BYTES", 10 * 1024 * 1024))

# Chunk size for the seek-from-end tail. 64 KiB balances syscall count
# against memory: very small chunks read many times, very large chunks
# undermine the point of avoiding a big read.
_TAIL_READ_CHUNK = 64 * 1024


def tail_log_text(log_file: Path, tail: int) -> str:
    """Return the last ``tail`` lines of ``log_file`` as text.

    Reads the file backwards in :data:`_TAIL_READ_CHUNK`-sized chunks
    until ``tail+1`` newlines have been seen (the +1 lets us drop the
    leading partial line — otherwise we'd return at most ``tail-1``
    full lines plus a partial). For ``tail <= 0`` the function reads
    the file when small, OR returns just the last
    :data:`LOG_TAIL_MAX_BYTES` worth of bytes (with the leading partial
    line dropped) when the file is larger — i.e. ``tail=0`` no longer
    means "return the entire file" the way it used to; the response is
    capped so a single request can't exhaust the API container even
    against a multi-GB log. The final payload is always bounded by
    :data:`LOG_TAIL_MAX_BYTES`, regardless of ``tail`` value, for the
    same reason.
    """
    file_size = log_file.stat().st_size
    if tail <= 0:
        # Caller asked for the whole file. Still cap the payload to
        # keep the response bounded.
        with log_file.open("rb") as f:
            if file_size > LOG_TAIL_MAX_BYTES:
                f.seek(file_size - LOG_TAIL_MAX_BYTES)
                # Drop the partial line at the start so callers get
                # well-formed lines.
                _ = f.readline()
            return f.read().decode("utf-8", errors="replace")

    # Small files: just read everything (seek-loop overhead isn't worth it).
    if file_size <= _TAIL_READ_CHUNK:
        with log_file.open("rb") as f:
            data = f.read()
        return _last_n_lines(data, tail).decode("utf-8", errors="replace")

    # Seek-from-end loop: read backwards until we have ``tail+1`` newlines.
    needed_newlines = tail + 1
    pieces: list[bytes] = []
    bytes_read = 0
    found_newlines = 0
    with log_file.open("rb") as f:
        position = file_size
        while position > 0 and found_newlines < needed_newlines:
            read_size = min(_TAIL_READ_CHUNK, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            pieces.append(chunk)
            bytes_read += read_size
            found_newlines += chunk.count(b"\n")
            # Hard cap: even if newlines are sparse, don't read more than
            # the response cap. Caller asked for "lines" — we truncate
            # rather than try to honour an unbounded read.
            if bytes_read >= LOG_TAIL_MAX_BYTES:
                break

    data = b"".join(reversed(pieces))
    return _last_n_lines(data, tail).decode("utf-8", errors="replace")


def _last_n_lines(data: bytes, tail: int) -> bytes:
    """Return the last ``tail`` complete lines from ``data``.

    Truncates the result to :data:`LOG_TAIL_MAX_BYTES` from the END so
    we always show the most-recent slice rather than an arbitrary head.
    """
    if not data:
        return b""
    # ``splitlines`` followed by ``[-tail:]`` is the same allocation
    # pattern the original code used, but on at most LOG_TAIL_MAX_BYTES
    # of data rather than the whole file. Joining with b"\n" preserves
    # the "no trailing newline" shape the original returned.
    lines = data.splitlines()
    selected = lines[-tail:] if tail > 0 else lines
    out = b"\n".join(selected)
    if len(out) > LOG_TAIL_MAX_BYTES:
        out = out[-LOG_TAIL_MAX_BYTES:]
    return out


# Module-level alias used by :meth:`OrchestratorClient.get_logs` via
# ``asyncio.to_thread``. Underscore-prefixed historically; the helper is
# safe to call from anywhere so the prefix is misleading, but kept as
# an alias for backwards compatibility with existing call sites.
_tail_log_file = tail_log_text


class OrchestratorError(Exception):
    """Error communicating with the orchestrator.

    ``status_code`` is set when raised by methods that explicitly propagate
    upstream status (currently only :meth:`OrchestratorClient.list_dir`,
    consumed by the ``list_session_dir`` API endpoint to forward
    client-actionable errors). It is ``None`` for all other call sites,
    including transport-level failures and the various 4xx/5xx paths in
    other client methods that just wrap the detail string.

    If you need status propagation for another endpoint, populate
    ``status_code`` at the raise site there too — the attribute is opt-in
    rather than guaranteed.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SessionInfo:
    """Information about a Claude session container."""

    session_id: str
    container_name: str | None = None
    status: str | None = None
    image: str | None = None
    memory: str | None = None
    cpus: int | None = None
    relay: dict[str, Any] = field(default_factory=dict)
    differ: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthInfo:
    """Orchestrator health and resource usage.

    `memory` and `cpus` may carry both ``allocated*`` (legacy) and ``used*``
    (added in orchestrator commit 4c45a70) keys. Values are widened to float
    because ``cpus.used`` is fractional.
    """

    status: str
    containers: dict[str, int] = field(default_factory=dict)
    memory: dict[str, float] = field(default_factory=dict)
    cpus: dict[str, float] = field(default_factory=dict)


async def http_request(
    socket_path: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> tuple[int, dict[str, Any]]:
    """Make an HTTP/1.1 request over a Unix socket.

    Returns (status_code, parsed_json_body).
    """
    if not os.path.exists(socket_path):
        raise OrchestratorError(
            f"Orchestrator socket not found: {socket_path}. "
            "Is the orchestrator running?"
        )

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise OrchestratorError("Timeout connecting to orchestrator")
    except OSError as e:
        raise OrchestratorError(f"Failed to connect to orchestrator: {e}")

    body_data = b""
    try:
        body_bytes = json.dumps(body).encode() if body else b""

        request_lines = [
            f"{method} {path} HTTP/1.1",
            "Host: localhost",
            "Connection: close",
        ]
        if body_bytes:
            request_lines.append("Content-Type: application/json")
            request_lines.append(f"Content-Length: {len(body_bytes)}")

        request_str = "\r\n".join(request_lines) + "\r\n\r\n"
        writer.write(request_str.encode() + body_bytes)
        await writer.drain()

        # Read full response (Connection: close means server closes when done)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
            except asyncio.TimeoutError:
                raise OrchestratorError("Timeout reading from orchestrator")
            if not chunk:
                break
            chunks.append(chunk)

        if not chunks:
            raise OrchestratorError("Empty response from orchestrator")

        data = b"".join(chunks)

        # Split headers and body
        separator = data.find(b"\r\n\r\n")
        if separator == -1:
            raise OrchestratorError("Malformed HTTP response: no header/body separator")

        header_bytes = data[:separator]
        body_data = data[separator + 4 :]

        # Parse status code from first line
        status_line = header_bytes.split(b"\r\n", 1)[0].decode()
        parts = status_line.split(" ", 2)
        if len(parts) < 2:
            raise OrchestratorError(f"Malformed status line: {status_line}")
        status_code = int(parts[1])

        parsed_body = json.loads(body_data) if body_data.strip() else {}
        return status_code, parsed_body

    except json.JSONDecodeError as e:
        raw = body_data[:500].decode("utf-8", errors="replace") if body_data else "(empty)"
        logger.error(
            "Orchestrator %s %s returned non-JSON body: %s",
            method, path, raw,
        )
        raise OrchestratorError(f"Invalid JSON response: {e}")
    except OrchestratorError:
        raise
    except Exception as e:
        logger.error("Orchestrator %s %s failed: %s", method, path, e)
        raise OrchestratorError(f"HTTP request failed: {e}")
    finally:
        writer.close()
        await writer.wait_closed()


class OrchestratorClient:
    """Async HTTP client for the Claude Session Orchestrator."""

    def __init__(self, socket_path: str = ORCHESTRATOR_SOCKET):
        self.socket_path = socket_path

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Make an HTTP request, raising OrchestratorError on 5xx."""
        status, data = await http_request(self.socket_path, method, path, body)
        log_detail = data.get("detail", data) if isinstance(data, dict) else data
        if status == 404:
            logger.debug("Orchestrator %s %s -> 404: %s", method, path, log_detail)
        elif 400 <= status < 500:
            logger.warning("Orchestrator %s %s -> %s: %s", method, path, status, log_detail)
        if status >= 500:
            error_detail = data.get("detail", f"Server error {status}") if isinstance(data, dict) else f"Server error {status}"
            logger.error("Orchestrator %s %s -> %s: %s", method, path, status, error_detail)
            raise OrchestratorError(error_detail)
        return status, data

    # -------------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------------

    async def ping(self) -> bool:
        """Check if orchestrator is responsive."""
        try:
            status, data = await self._request("GET", "/health")
            return status == 200 and data.get("status") == "ok"
        except OrchestratorError:
            return False

    async def health(self) -> HealthInfo:
        """Get orchestrator health and resource usage."""
        status, data = await self._request("GET", "/health")
        if status != 200:
            raise OrchestratorError(data.get("detail", "Health check failed"))
        return HealthInfo(
            status=data["status"],
            containers=data.get("containers", {}),
            memory=data.get("memory", {}),
            cpus=data.get("cpus", {}),
        )

    # -------------------------------------------------------------------------
    # Containers
    # -------------------------------------------------------------------------

    async def create_container(
        self,
        session_id: str,
        *,
        image: str | None = None,
        volume: str | None = None,
        env: dict[str, str] | None = None,
        memory: str | None = None,
        cpus: int | None = None,
        networks: list[str] | None = None,
        dev_channels_server: str | None = None,
    ) -> SessionInfo:
        """Create a new session container.

        Idempotent: returns existing container if already running.
        Raises OrchestratorError on resource limit exceeded (409) or other errors.

        When dev_channels_server is set, the orchestrator launches Claude with
        --dangerously-load-development-channels <server>.
        """
        body: dict[str, Any] = {"id": session_id}
        if image is not None:
            body["image"] = image
        if volume is not None:
            body["volume"] = volume
        if env:
            body["env"] = env
        if memory is not None:
            body["memory"] = memory
        if cpus is not None:
            body["cpus"] = cpus
        if networks:
            body["networks"] = networks
        if dev_channels_server is not None:
            body["dev_channels_server"] = dev_channels_server

        status, data = await self._request("POST", "/containers", body)

        if status == 409:
            raise OrchestratorError(
                data.get("detail", "Resource limit exceeded")
            )
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Container creation failed ({status})")
            )

        return SessionInfo(
            session_id=data["id"],
            container_name=data.get("container_name"),
            status=data.get("status"),
            image=data.get("image"),
            memory=data.get("memory"),
            cpus=data.get("cpus"),
            relay=data.get("relay", {}),
            differ=data.get("differ", {}),
        )

    async def list_containers(self) -> list[SessionInfo]:
        """List all managed session containers."""
        status, data = await self._request("GET", "/containers")
        if status != 200:
            raise OrchestratorError(
                data.get("detail", "Failed to list containers")
            )

        return [
            SessionInfo(
                session_id=c["id"],
                container_name=c.get("container_name"),
                status=c.get("status"),
                image=c.get("image"),
                memory=c.get("memory"),
                cpus=c.get("cpus"),
                relay=c.get("relay", {}),
                differ=c.get("differ", {}),
            )
            for c in data
        ]

    async def get_container(self, session_id: str) -> SessionInfo | None:
        """Get details of a specific container. Returns None if not found."""
        status, data = await self._request("GET", f"/containers/{session_id}")
        if status == 404:
            return None
        if status != 200:
            raise OrchestratorError(
                data.get("detail", f"Failed to get container ({status})")
            )

        return SessionInfo(
            session_id=data["id"],
            container_name=data.get("container_name"),
            status=data.get("status"),
            image=data.get("image"),
            memory=data.get("memory"),
            cpus=data.get("cpus"),
            relay=data.get("relay", {}),
            differ=data.get("differ", {}),
        )

    async def delete_container(self, session_id: str) -> bool:
        """Kill and remove a container. Returns True if removed, False if not found."""
        status, data = await self._request(
            "DELETE", f"/containers/{session_id}"
        )
        if status == 404:
            return False
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to delete container ({status})")
            )
        return True

    async def cleanup_dead_containers(self) -> list[str]:
        """Remove all exited/dead containers. Returns list of removed IDs."""
        status, data = await self._request("POST", "/cleanup")
        if status != 200:
            raise OrchestratorError(
                data.get("detail", "Cleanup failed")
            )
        return data.get("removed", [])

    # -------------------------------------------------------------------------
    # Stats (sampler-backed)
    # -------------------------------------------------------------------------

    async def stats(self) -> dict[str, Any]:
        """Return the orchestrator's most-recent stats snapshot.

        Shape: ``{ts, global, containers: [...]}``. See orchestrator docs.
        """
        status, data = await self._request("GET", "/stats")
        if status >= 400:
            detail = data.get("detail", f"Stats failed ({status})") if isinstance(data, dict) else f"Stats failed ({status})"
            raise OrchestratorError(detail, status_code=status)
        return data

    async def container_stats(self, session_id: str) -> dict[str, Any] | None:
        """Return current stats for a single container, or None if not managed."""
        status, data = await self._request(
            "GET", f"/containers/{session_id}/stats"
        )
        if status == 404:
            return None
        if status >= 400:
            detail = data.get("detail", f"Container stats failed ({status})") if isinstance(data, dict) else f"Container stats failed ({status})"
            raise OrchestratorError(detail, status_code=status)
        return data

    async def stats_history(
        self,
        *,
        session_id: str | None = None,
        since: str | None = None,
        max_points: int = 1000,
    ) -> dict[str, Any]:
        """Return ring-buffer points: ``{points, count, truncated}``.

        ``since`` is an ISO 8601 string passed through verbatim; the
        orchestrator does the parsing. ``max_points`` is sent on the wire
        as ``max=`` (the orchestrator's parameter name).
        """
        params: dict[str, str] = {"max": str(max_points)}
        if session_id:
            params["session_id"] = session_id
        if since:
            params["since"] = since
        url = f"/stats/history?{urlencode(params)}"
        status, data = await self._request("GET", url)
        if status >= 400:
            detail = data.get("detail", f"Stats history failed ({status})") if isinstance(data, dict) else f"Stats history failed ({status})"
            raise OrchestratorError(detail, status_code=status)
        return data

    # -------------------------------------------------------------------------
    # Volumes
    # -------------------------------------------------------------------------

    async def list_volumes(self) -> list[dict[str, str]]:
        """List all Docker volumes."""
        status, data = await self._request("GET", "/volumes")
        if status != 200:
            raise OrchestratorError(
                data.get("detail", "Failed to list volumes")
            )
        return data

    async def create_volume(self, name: str) -> dict[str, str]:
        """Create a Docker volume."""
        status, data = await self._request("POST", "/volumes", {"name": name})
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to create volume ({status})")
            )
        return data

    async def delete_volume(self, name: str) -> bool:
        """Delete a Docker volume. Returns True if removed, False if not found."""
        status, data = await self._request("DELETE", f"/volumes/{name}")
        if status == 404:
            return False
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to delete volume ({status})")
            )
        return True

    async def init_volume(
        self, name: str, snapshot_path: str
    ) -> dict[str, str]:
        """Initialize a volume from a tar.gz snapshot."""
        status, data = await self._request(
            "POST",
            f"/volumes/{name}/init",
            {"snapshot_path": snapshot_path},
        )
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to initialize volume ({status})")
            )
        return data

    async def clone_volume(self, name: str, dest: str) -> dict[str, str]:
        """Clone a volume by copying all data to a new destination volume."""
        status, data = await self._request(
            "POST",
            f"/volumes/{name}/clone",
            {"dest": dest},
        )
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to clone volume ({status})")
            )
        return data

    async def reset_volume(
        self, name: str, snapshot_path: str | None = None
    ) -> dict[str, str]:
        """Delete and recreate a volume, optionally reinitializing from snapshot."""
        body: dict[str, str] = {}
        if snapshot_path:
            body["snapshot_path"] = snapshot_path
        status, data = await self._request(
            "POST", f"/volumes/{name}/reset", body
        )
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to reset volume ({status})")
            )
        return data

    # -------------------------------------------------------------------------
    # File operations on container filesystems
    # -------------------------------------------------------------------------

    async def list_dir(
        self,
        session_id: str,
        path: str,
        recursive: bool = False,
        max_entries: int = 1000,
    ) -> dict[str, Any]:
        """List entries in a directory inside a session container.

        Calls orchestrator
        ``GET /containers/{session_id}/files/list?path=<abs_path>``.
        Path is sent as a query parameter (not embedded in the URL) so
        absolute paths with slashes round-trip cleanly.
        """
        params: dict[str, str] = {"path": path}
        if recursive:
            params["recursive"] = "true"
        if max_entries is not None:
            params["max_entries"] = str(max_entries)
        url = f"/containers/{session_id}/files/list?{urlencode(params)}"

        status, data = await self._request("GET", url)
        if status >= 400:
            detail = (
                data.get("detail", f"List failed ({status})")
                if isinstance(data, dict)
                else f"List failed ({status})"
            )
            raise OrchestratorError(detail, status_code=status)
        return data

    # -------------------------------------------------------------------------
    # Terminal relay (tmux pane management)
    # -------------------------------------------------------------------------

    async def relay_list_panes(self, session_id: str) -> dict[str, Any]:
        """List all tmux panes for a session, plus container resource stats.

        Returns a dict with `panes` (list) and `stats` (dict or None).
        Older relays may return a bare list of panes; in that case `stats` is None.
        """
        status, data = await self._request(
            "GET", f"/containers/{session_id}/relay/panes"
        )
        if status == 404:
            return {"panes": [], "stats": None}
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to list panes ({status})")
            )
        if isinstance(data, list):
            return {"panes": data, "stats": None}
        return {"panes": data.get("panes", []), "stats": data.get("stats")}

    async def relay_select_pane(
        self,
        session_id: str,
        pane: str,
        cols: int | None = None,
        rows: int | None = None,
    ) -> dict[str, Any]:
        """Switch the active tmux pane, optionally setting display dimensions."""
        qs = f"pane={pane}"
        if cols is not None:
            qs += f"&cols={cols}"
        if rows is not None:
            qs += f"&rows={rows}"
        status, data = await self._request(
            "POST", f"/containers/{session_id}/relay/select?{qs}"
        )
        if status >= 400:
            raise OrchestratorError(
                data.get("detail", f"Failed to select pane ({status})")
            )
        return data

    # -------------------------------------------------------------------------
    # Convenience: combined operations
    # -------------------------------------------------------------------------

    async def create_initialized_volume(
        self, name: str, snapshot_path: str
    ) -> dict[str, str]:
        """Create a volume and initialize it from a snapshot (two-step)."""
        await self.create_volume(name)
        return await self.init_volume(name, snapshot_path)

    # -------------------------------------------------------------------------
    # Logs (read from host filesystem, not orchestrator API)
    # -------------------------------------------------------------------------

    async def get_logs(
        self, session_id: str, tail: int = 100
    ) -> dict[str, str] | None:
        """Read session logs from the host log directory.

        The orchestrator writes logs to LOG_DIR/{session_id}.log.
        Returns None if no logs are available.

        Uses a seek-from-end tail so a multi-GB log file doesn't OOM the
        API container. ``read_text()`` would buffer the entire file
        (peak ~3× file size after splitlines + join) before discarding
        all but the last N lines — for long-lived debug sessions that
        log verbosely for hours, that's a single-request DoS.

        Validates ``session_id`` format here (defense in depth) — without
        this, a malformed id like ``u1-x-../../../etc/hosts`` would resolve
        to ``/etc/hosts.log`` on the host filesystem.
        """
        if not _SESSION_ID_RE.match(session_id):
            logger.warning("get_logs: rejecting malformed session_id")
            return None

        log_file = LOG_DIR / f"{session_id}.log"
        if not log_file.exists():
            return None

        try:
            content = await asyncio.to_thread(_tail_log_file, log_file, tail)
        except OSError as e:
            logger.warning(f"Failed to read log file {log_file}: {e}")
            return None

        return {
            "session_id": session_id,
            "source": "file",
            "logs": content,
        }


# Singleton client instance
_client: OrchestratorClient | None = None


def get_orchestrator_client() -> OrchestratorClient:
    """Get the shared orchestrator client instance."""
    global _client
    if _client is None:
        _client = OrchestratorClient()
    return _client
