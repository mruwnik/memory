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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ORCHESTRATOR_SOCKET = os.environ.get(
    "ORCHESTRATOR_SOCKET", "/var/run/claude-sessions/orchestrator.sock"
)

# Timeout for HTTP operations in seconds
HTTP_TIMEOUT = 30

# Log directory on host where orchestrator writes session logs
LOG_DIR = Path("/var/log/claude-sessions")


class OrchestratorError(Exception):
    """Error communicating with the orchestrator."""


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


@dataclass
class HealthInfo:
    """Orchestrator health and resource usage."""

    status: str
    containers: dict[str, int] = field(default_factory=dict)
    memory: dict[str, int] = field(default_factory=dict)
    cpus: dict[str, int] = field(default_factory=dict)


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
    ) -> SessionInfo:
        """Create a new session container.

        Idempotent: returns existing container if already running.
        Raises OrchestratorError on resource limit exceeded (409) or other errors.
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
        """
        log_file = LOG_DIR / f"{session_id}.log"
        if not log_file.exists():
            return None

        try:
            content = await asyncio.to_thread(log_file.read_text)
            if tail > 0:
                lines = content.splitlines()
                content = "\n".join(lines[-tail:])
            return {
                "session_id": session_id,
                "source": "file",
                "logs": content,
            }
        except OSError as e:
            logger.warning(f"Failed to read log file {log_file}: {e}")
            return None


# Singleton client instance
_client: OrchestratorClient | None = None


def get_orchestrator_client() -> OrchestratorClient:
    """Get the shared orchestrator client instance."""
    global _client
    if _client is None:
        _client = OrchestratorClient()
    return _client
