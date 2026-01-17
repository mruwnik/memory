"""Async client for communicating with the Claude Session Orchestrator.

The orchestrator manages Claude containers via a Unix socket. This client
provides a clean interface for the Memory API to create/list/stop sessions.

Communication is done over Unix socket with JSON messages.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

ORCHESTRATOR_SOCKET = os.environ.get(
    "ORCHESTRATOR_SOCKET", "/var/run/claude-sessions/orchestrator.sock"
)
MEMORY_STACK = os.environ.get("MEMORY_STACK", "dev")

# Timeout for socket operations in seconds
SOCKET_TIMEOUT = 30


class OrchestratorError(Exception):
    """Error communicating with the orchestrator."""


@dataclass
class SessionInfo:
    """Information about a Claude session."""

    session_id: str
    container_id: str | None = None
    container_name: str | None = None
    status: str | None = None
    memory_stack: str | None = None
    network: str | None = None


class OrchestratorClient:
    """Async client for the Claude Session Orchestrator."""

    def __init__(
        self,
        socket_path: str = ORCHESTRATOR_SOCKET,
        memory_stack: str = MEMORY_STACK,
    ):
        self.socket_path = socket_path
        self.memory_stack = memory_stack

    async def _call(self, action: str, **kwargs: Any) -> dict[str, Any]:
        """Make an async request to the orchestrator."""
        if not os.path.exists(self.socket_path):
            raise OrchestratorError(
                f"Orchestrator socket not found: {self.socket_path}. "
                "Is the orchestrator running?"
            )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=SOCKET_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise OrchestratorError("Timeout connecting to orchestrator")
        except OSError as e:
            raise OrchestratorError(f"Failed to connect to orchestrator: {e}")

        try:
            request = {"action": action, **kwargs}
            writer.write(json.dumps(request).encode())
            await writer.drain()

            # Read response
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(65536),
                        timeout=SOCKET_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    raise OrchestratorError("Timeout reading from orchestrator")

                if not chunk:
                    break
                chunks.append(chunk)

                # Try to parse - if successful, we're done
                try:
                    return json.loads(b"".join(chunks).decode())
                except json.JSONDecodeError:
                    continue

            if not chunks:
                raise OrchestratorError("Empty response from orchestrator")

            return json.loads(b"".join(chunks).decode())

        except json.JSONDecodeError as e:
            raise OrchestratorError(f"Invalid JSON response: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def ping(self) -> bool:
        """Check if orchestrator is responsive."""
        try:
            response = await self._call("ping")
            return response.get("status") == "pong"
        except OrchestratorError:
            return False

    async def create_session(
        self,
        session_id: str,
        *,
        image: str = "claude-cloud:latest",
        memory_stack: str | None = None,
        env: dict[str, str] | None = None,
        git_repo_url: str | None = None,
        ssh_private_key: str | None = None,
        github_token: str | None = None,
        snapshot_path: str | None = None,
        happy_access_key: str | None = None,
        happy_machine_id: str | None = None,
    ) -> SessionInfo:
        """Create a new Claude session container."""
        response = await self._call(
            "create",
            session_id=session_id,
            image=image,
            memory_stack=memory_stack or self.memory_stack,
            env=env or {},
            git_repo_url=git_repo_url,
            ssh_private_key=ssh_private_key,
            github_token=github_token,
            snapshot_path=snapshot_path,
            happy_access_key=happy_access_key,
            happy_machine_id=happy_machine_id,
        )

        if response.get("status") == "error":
            raise OrchestratorError(response.get("error", "Unknown error"))

        return SessionInfo(
            session_id=response["session_id"],
            container_id=response.get("container_id"),
            container_name=response.get("container_name"),
            status=response.get("status"),
            network=response.get("network"),
        )

    async def stop_session(self, session_id: str) -> bool:
        """Stop and remove a Claude session."""
        response = await self._call("stop", session_id=session_id)
        return response.get("status") == "stopped"

    async def list_sessions(
        self, memory_stack: Literal["dev", "prod"] | None = None
    ) -> list[SessionInfo]:
        """List all Claude sessions, optionally filtered by memory stack."""
        response = await self._call("list")

        sessions = []
        for s in response.get("sessions", []):
            # Filter by memory stack if specified
            if memory_stack and s.get("memory_stack") != memory_stack:
                continue
            sessions.append(
                SessionInfo(
                    session_id=s["session_id"],
                    container_id=s.get("container_id"),
                    container_name=s.get("container_name"),
                    status=s.get("status"),
                    memory_stack=s.get("memory_stack"),
                )
            )
        return sessions

    async def get_session(self, session_id: str) -> SessionInfo | None:
        """Get details of a specific session."""
        response = await self._call("get", session_id=session_id)

        if response.get("status") == "not_found":
            return None

        return SessionInfo(
            session_id=response["session_id"],
            container_id=response.get("container_id"),
            container_name=response.get("container_name"),
            status=response.get("status"),
            memory_stack=response.get("memory_stack"),
        )

    async def get_attach_info(self, session_id: str) -> dict[str, str] | None:
        """Get commands needed to attach to a session."""
        response = await self._call("attach_info", session_id=session_id)

        if response.get("status") == "not_found":
            return None

        return {
            "attach_cmd": response.get("attach_cmd", ""),
            "exec_cmd": response.get("exec_cmd", ""),
        }

    async def get_logs(self, session_id: str, tail: int = 100) -> dict[str, str] | None:
        """Get logs for a session (from persistent file or container)."""
        response = await self._call("logs", session_id=session_id, tail=tail)

        if response.get("status") == "not_found":
            return None

        return {
            "session_id": response.get("session_id", session_id),
            "source": response.get("source", "unknown"),
            "logs": response.get("logs", ""),
        }


# Singleton client instance
_client: OrchestratorClient | None = None


def get_orchestrator_client() -> OrchestratorClient:
    """Get the shared orchestrator client instance."""
    global _client
    if _client is None:
        _client = OrchestratorClient()
    return _client
