"""API endpoints for Docker container logs."""

import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from memory.api.auth import get_current_user
from memory.common.db.models import User


router = APIRouter(prefix="/api/docker", tags=["docker"])

# Support both TCP (via docker-socket-proxy) and Unix socket
DOCKER_HOST = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
# Strict regex: only allow memory-api, memory-worker, memory-ingest with optional -N suffix
VALID_CONTAINER_PATTERN = re.compile(r"^memory-(api|worker|ingest)(-[a-z0-9]+)?(-\d+)?$")
# Maximum lines to fetch when filtering (prevents OOM on large containers)
MAX_FILTER_LINES = 50000


class ContainerInfo(BaseModel):
    """Basic container information."""

    name: str
    status: str
    started_at: datetime | None = None


class LogsResponse(BaseModel):
    """Response containing container logs."""

    container: str
    logs: str
    since: datetime | None = None
    until: datetime | None = None
    lines: int


def get_docker_client() -> httpx.Client:
    """Get Docker client connected via TCP or Unix socket."""
    if DOCKER_HOST.startswith("tcp://"):
        # TCP connection to docker-socket-proxy
        return httpx.Client(base_url=DOCKER_HOST.replace("tcp://", "http://"), timeout=30.0)
    elif DOCKER_HOST.startswith("unix://"):
        # Unix socket connection (local development)
        socket_path = DOCKER_HOST.replace("unix://", "")
        transport = httpx.HTTPTransport(uds=socket_path)
        return httpx.Client(transport=transport, base_url="http://localhost", timeout=30.0)
    else:
        # Assume it's a direct HTTP URL
        return httpx.Client(base_url=DOCKER_HOST, timeout=30.0)


def validate_container_name(name: str) -> str:
    """Validate container name using strict regex allowlist."""
    clean_name = name.strip()

    if not VALID_CONTAINER_PATTERN.match(clean_name):
        raise HTTPException(
            status_code=403,
            detail=f"Container '{name}' not allowed. Must match pattern: memory-(api|worker|ingest)[-suffix][-N]",
        )

    return clean_name


def decode_docker_logs(data: bytes) -> str:
    """
    Decode Docker logs, handling both multiplexed and raw (TTY) formats.

    Docker multiplexes stdout/stderr with 8-byte headers when TTY is disabled:
    [stream_type(1), 0, 0, 0, size(4 big-endian)]

    When TTY is enabled, logs are raw text without headers.
    """
    if not data:
        return ""

    # Check if data is multiplexed: first byte should be stream type (0, 1, or 2)
    # and bytes 1-3 should be zero padding
    is_multiplexed = (
        len(data) >= 8
        and data[0] in (0, 1, 2)
        and data[1:4] == b"\x00\x00\x00"
    )

    if not is_multiplexed:
        # TTY-enabled container: raw text
        return data.decode("utf-8", errors="replace")

    # Multiplexed format: parse 8-byte headers
    lines: list[str] = []
    i = 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i + 4 : i + 8], "big")
        i += 8
        if i + size > len(data):
            break
        lines.append(data[i : i + size].decode("utf-8", errors="replace"))
        i += size
    return "".join(lines)


@router.get("/containers")
def list_containers(
    _user: User = Depends(get_current_user),
) -> list[ContainerInfo]:
    """List allowed Docker containers and their status."""
    try:
        with get_docker_client() as client:
            response = client.get("/containers/json", params={"all": True})
            response.raise_for_status()
            all_containers = response.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503, detail="Docker socket not available. Is it mounted?"
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Docker API error: {e}")

    containers = []
    for container in all_containers:
        name = container.get("Names", ["/unknown"])[0].lstrip("/")

        if not VALID_CONTAINER_PATTERN.match(name):
            continue

        started_at = None
        state = container.get("State", "")
        status = container.get("Status", state)

        # Parse created timestamp if available
        created = container.get("Created")
        if created:
            started_at = datetime.fromtimestamp(created, tz=timezone.utc)

        containers.append(
            ContainerInfo(
                name=name,
                status=status,
                started_at=started_at,
            )
        )

    return containers


@router.get("/logs/{container}")
def get_logs(
    container: str,
    since: datetime | None = Query(
        None, description="Start time (default: 1 hour ago)"
    ),
    until: datetime | None = Query(None, description="End time (default: now)"),
    tail: int = Query(1000, ge=1, le=10000, description="Number of lines"),
    filter_text: str | None = Query(None, description="Filter logs containing text"),
    timestamps: bool = Query(True, description="Include timestamps"),
    _user: User = Depends(get_current_user),
) -> LogsResponse:
    """
    Get logs from a Docker container.

    Filters by time range and optionally by text content.
    """
    container_name = validate_container_name(container)

    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=1)

    params = {
        "stdout": True,
        "stderr": True,
        # When filtering, fetch more lines but cap to prevent OOM
        "tail": tail if not filter_text else MAX_FILTER_LINES,
        "timestamps": timestamps,
        "since": int(since.timestamp()),
    }
    if until:
        params["until"] = int(until.timestamp())

    try:
        with get_docker_client() as client:
            response = client.get(f"/containers/{container_name}/logs", params=params)
            response.raise_for_status()
            logs_data = response.content
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503, detail="Docker socket not available. Is it mounted?"
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404, detail=f"Container '{container_name}' not found"
            )
        raise HTTPException(status_code=502, detail=f"Docker API error: {e}")

    logs_text = decode_docker_logs(logs_data)

    # Apply text filter if specified
    if filter_text:
        lines = logs_text.splitlines()
        filtered = [line for line in lines if filter_text.lower() in line.lower()]
        logs_text = "\n".join(filtered[-tail:])

    return LogsResponse(
        container=container_name,
        logs=logs_text,
        since=since,
        until=until,
        lines=len(logs_text.splitlines()),
    )
