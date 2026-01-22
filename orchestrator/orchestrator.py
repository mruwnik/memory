#!/usr/bin/env python3
"""
Claude Session Orchestrator

A lightweight daemon that manages Claude containers via Unix socket.
Shared between multiple memory system instances (dev/prod).

Communication: Unix socket at /var/run/claude-sessions/orchestrator.sock
Protocol: JSON over socket - send request, receive response, close connection
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import hashlib
import re
import time

import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.models.images import Image
from docker.models.networks import Network
from docker.models.volumes import Volume


# Docker volume name validation pattern
# Docker volume names: [a-zA-Z0-9][a-zA-Z0-9_.-]* and max 255 chars
VOLUME_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
VOLUME_NAME_MAX_LENGTH = 255

# Allowed base directories for snapshot paths (defense-in-depth)
# The orchestrator runs on the host, so these are host paths
ALLOWED_SNAPSHOT_DIRS = [
    Path("/home/ec2-user/memory/memory_files/snapshots"),
    Path("/home/ec2-user/chris/memory_files/snapshots"),
    Path(
        "/app/memory_files/snapshots"
    ),  # Docker path (shouldn't be used but just in case)
]


def validate_snapshot_path(path: str) -> tuple[bool, str]:
    """Validate a snapshot path for security.

    Checks that:
    1. Path exists and is a regular file
    2. Path is within allowed directories (no path traversal)
    3. Path has expected extension

    Returns:
        Tuple of (is_valid, error_message). error_message is empty if valid.
    """
    if not path:
        return False, "Snapshot path cannot be empty"

    snapshot_path = Path(path)

    # Check file exists
    if not snapshot_path.exists():
        return False, f"Snapshot file does not exist: {path}"

    # Check it's a regular file (not symlink, device, directory, etc.)
    if not snapshot_path.is_file():
        return False, f"Snapshot path is not a regular file: {path}"

    # Resolve to absolute path to detect path traversal
    resolved = snapshot_path.resolve()

    # Check path is within allowed directories
    in_allowed_dir = any(
        resolved.is_relative_to(allowed_dir)
        for allowed_dir in ALLOWED_SNAPSHOT_DIRS
        if allowed_dir.exists()
    )
    if not in_allowed_dir:
        return False, f"Snapshot path not in allowed directory: {path}"

    # Check extension (should be .tar.gz or .tgz)
    if not (path.endswith(".tar.gz") or path.endswith(".tgz")):
        return False, f"Snapshot must be a .tar.gz or .tgz file: {path}"

    return True, ""


def validate_volume_name(name: str) -> tuple[bool, str]:
    """Validate a Docker volume name.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty if valid.
    """
    if not name:
        return False, "Volume name cannot be empty"
    if len(name) > VOLUME_NAME_MAX_LENGTH:
        return False, f"Volume name exceeds {VOLUME_NAME_MAX_LENGTH} characters"
    if not VOLUME_NAME_PATTERN.match(name):
        return (
            False,
            "Volume name must start with alphanumeric and contain only alphanumeric, underscore, dot, or dash",
        )
    return True, ""


# Configuration
SOCKET_PATH = Path(
    os.environ.get("ORCHESTRATOR_SOCKET", "/var/run/claude-sessions/orchestrator.sock")
)
SOCKET_PERMISSIONS = 0o660
DEFAULT_IMAGE = os.environ.get("CLAUDE_IMAGE", "claude-cloud:latest")
CONTAINER_MEMORY_LIMIT = os.environ.get("CLAUDE_MEMORY_LIMIT", "4g")
CONTAINER_CPU_LIMIT = int(os.environ.get("CLAUDE_CPU_LIMIT", "2"))
LOG_DIR = Path(os.environ.get("CLAUDE_LOG_DIR", "/var/log/claude-sessions"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Tmux session name used inside containers (must match entrypoint.sh)
# IMPORTANT: Must be alphanumeric/underscore/dash only - used in shell scripts
TMUX_SESSION_NAME = "claude"
assert re.match(r"^[a-zA-Z0-9_-]+$", TMUX_SESSION_NAME), (
    f"TMUX_SESSION_NAME must be alphanumeric/underscore/dash only: {TMUX_SESSION_NAME}"
)


IMAGES = {
    "claude-cloud": {
        "dockerfile": "docker/claude-cloud/Dockerfile",
        "entrypoint": "docker/claude-cloud/entrypoint.sh",
    },
    "claude-cloud-happy": {
        "dockerfile": "docker/claude-cloud/Dockerfile.happy",
        "entrypoint": "docker/claude-cloud/entrypoint.sh",
    },
}

# Install directory (where Dockerfiles are copied by setup.sh)
INSTALL_DIR = Path(
    os.environ.get("ORCHESTRATOR_INSTALL_DIR", "/opt/claude-orchestrator")
)

# Shared network for all Claude sessions (ICC disabled for isolation)
CLAUDE_NETWORK_NAME = "claude-sessions"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    """Configuration for a Claude session container."""

    session_id: str
    image: str = DEFAULT_IMAGE
    memory_stack: Literal["dev", "prod"] | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Git authentication (alternative methods - use one based on URL scheme)
    # - ssh_private_key: For SSH URLs (git@github.com:user/repo.git)
    # - github_token: For HTTPS URLs (https://github.com/user/repo.git)
    # Both can be provided; git uses whichever matches the URL scheme.
    git_repo_url: str | None = None
    ssh_private_key: str | None = None
    github_token: str | None = None
    github_token_write: str | None = None  # Write token for differ (push, PR creation)
    claude_prompt: str | None = None
    snapshot_path: str | None = None
    # Environment volume (for persistent environments)
    # If set, mounts this Docker volume at /home/claude instead of extracting snapshot
    environment_volume: str | None = None


class Orchestrator:
    """Manages Claude session containers."""

    def __init__(self):
        self.docker = docker.from_env()
        logger.info("Connected to Docker daemon")
        # Ensure shared network exists
        self._ensure_network()
        # Ensure default image is built/up-to-date on startup
        for image in IMAGES:
            self._ensure_image(image)
        # Clean up dead containers on startup
        self.cleanup_dead_containers()

    def _ensure_network(self) -> None:
        """Ensure the shared Claude sessions network exists."""
        try:
            self.docker.networks.get(CLAUDE_NETWORK_NAME)
            logger.info(f"Using existing network: {CLAUDE_NETWORK_NAME}")
        except NotFound:
            # Create network with inter-container communication disabled
            self.docker.networks.create(
                CLAUDE_NETWORK_NAME,
                driver="bridge",
                options={"com.docker.network.bridge.enable_icc": "false"},
                labels={"managed-by": "claude-orchestrator"},
            )
            logger.info(f"Created network: {CLAUDE_NETWORK_NAME} (ICC disabled)")

    def _get_source_hash(self, image: str) -> str:
        """Compute hash of Dockerfile + entrypoint for change detection."""
        image_name = image.split(":")[0]

        if image_name not in IMAGES:
            raise ValueError(f"Unknown image: {image_name}")

        dockerfile = IMAGES[image_name]["dockerfile"]
        entrypoint = IMAGES[image_name]["entrypoint"]

        hasher = hashlib.sha256()
        for filename in [dockerfile, entrypoint]:
            filepath = INSTALL_DIR / filename
            if filepath.exists():
                hasher.update(filepath.read_bytes())
        return hasher.hexdigest()[:12]

    def _ensure_image(self, image: str) -> None:
        """Ensure the Docker image exists and is up-to-date, building if necessary."""
        source_hash = self._get_source_hash(image)
        needs_build = False

        try:
            img = cast(Image, self.docker.images.get(image))
            # Check if source files have changed since image was built
            image_hash = img.labels.get("source-hash", "")
            if source_hash and image_hash != source_hash:
                logger.info(
                    f"Image {image} is outdated (hash {image_hash} != {source_hash}), rebuilding..."
                )
                needs_build = True
            else:
                logger.debug(f"Image exists and up-to-date: {image}")
        except ImageNotFound:
            logger.info(f"Image {image} not found, building...")
            needs_build = True

        if needs_build:
            self._build_image(image, source_hash)

    def _build_image(self, image: str, source_hash: str = "") -> None:
        """Build a Docker image from the project's Dockerfile."""
        # Parse image name to determine which Dockerfile to use
        image_name = image.split(":")[0]

        if image_name not in IMAGES:
            raise ValueError(f"Unknown image: {image_name}")

        dockerfile = IMAGES[image_name]["dockerfile"]

        dockerfile_path = INSTALL_DIR / dockerfile
        if not dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile not found: {dockerfile_path}")

        logger.info(f"Building {image} from {dockerfile_path} (hash: {source_hash})...")

        # Build the image with source hash label for change detection
        labels = {"managed-by": "claude-orchestrator"}
        if source_hash:
            labels["source-hash"] = source_hash

        result = self.docker.images.build(
            path=str(INSTALL_DIR),
            dockerfile=dockerfile,
            tag=image,
            rm=True,
            labels=labels,
        )
        # Result is tuple of (Image, logs_generator)
        logs = result[1] if isinstance(result, tuple) else iter([])

        # Log build output
        for chunk in list(logs):
            if isinstance(chunk, dict) and "stream" in chunk:
                line = str(chunk["stream"]).strip()
                if line:
                    logger.debug(f"  {line}")

        logger.info(f"Successfully built image: {image}")

    def create_session(self, config: SessionConfig) -> dict[str, Any]:
        """Create a new Claude session container."""
        container_name = f"claude-{config.session_id}"

        try:
            # Ensure image exists (build if necessary)
            self._ensure_image(config.image)
            # Build environment variables
            environment = dict(config.env)
            if config.git_repo_url:
                environment["GIT_REPO_URL"] = config.git_repo_url
            if config.ssh_private_key:
                environment["SSH_PRIVATE_KEY"] = config.ssh_private_key
            if config.github_token:
                environment["GITHUB_TOKEN"] = config.github_token
            if config.github_token_write:
                environment["GITHUB_TOKEN_WRITE"] = config.github_token_write
            if config.claude_prompt:
                environment["CLAUDE_PROMPT"] = config.claude_prompt

            # Build volume mounts
            volumes = {}

            # Environment volume OR snapshot (mutually exclusive)
            if config.environment_volume:
                # Mount persistent environment volume at /home/claude
                # This is a named volume, not a bind mount
                volumes[config.environment_volume] = {
                    "bind": "/home/claude",
                    "mode": "rw",
                }
            elif config.snapshot_path:
                # Mount snapshot for extraction (existing behavior)
                volumes[config.snapshot_path] = {
                    "bind": "/snapshot/snapshot.tar.gz",
                    "mode": "ro",
                }

            # Mount log directory for persistent logs (owned by UID 10000 = claude user in container)
            session_log_dir = LOG_DIR / config.session_id
            session_log_dir.mkdir(parents=True, exist_ok=True)
            os.chown(session_log_dir, 10000, 10000)
            volumes[session_log_dir] = {"bind": "/var/log/claude", "mode": "rw"}

            # Create container on shared network
            container = cast(
                Container,
                self.docker.containers.run(
                    config.image,
                    name=container_name,
                    detach=True,
                    tty=True,
                    stdin_open=True,
                    network=CLAUDE_NETWORK_NAME,
                    environment=environment,
                    volumes=volumes,
                    labels={
                        "claude-session": config.session_id,
                        "memory-stack": config.memory_stack or "none",
                    },
                    # Security hardening
                    security_opt=["no-new-privileges:true"],
                    mem_limit=CONTAINER_MEMORY_LIMIT,
                    cpu_period=100000,
                    cpu_quota=CONTAINER_CPU_LIMIT * 100000,
                ),
            )
            logger.info(f"Created container: {container_name} ({container.short_id})")

            # Connect to memory API network if specified
            if config.memory_stack:
                memory_network_name = f"memory-api-{config.memory_stack}"
                try:
                    memory_network = cast(Network, self.docker.networks.get(memory_network_name))
                    memory_network.connect(container)
                    logger.info(f"Connected to network: {memory_network_name}")
                except NotFound:
                    logger.warning(
                        f"Memory network {memory_network_name} not found - "
                        "container will not have memory API access"
                    )

            return {
                "status": "created",
                "session_id": config.session_id,
                "container_id": container.id,
                "container_name": container_name,
                "network": CLAUDE_NETWORK_NAME,
            }

        except APIError as e:
            logger.error(f"Failed to create session {config.session_id}: {e}")
            # Cleanup on failure
            self._stop_container(container_name)
            return {"status": "error", "error": str(e)}

    def stop_session(self, session_id: str) -> dict[str, Any]:
        """Stop and remove a Claude session container."""
        container_name = f"claude-{session_id}"
        container_stopped = self._stop_container(container_name)

        return {
            "status": "stopped",
            "session_id": session_id,
            "container_stopped": container_stopped,
        }

    def list_sessions(self) -> dict[str, Any]:
        """List all Claude session containers."""
        containers = cast(
            list[Container],
            self.docker.containers.list(all=True, filters={"label": "claude-session"}),
        )
        return {
            "sessions": [
                {
                    "session_id": c.labels.get("claude-session"),
                    "container_id": c.id,
                    "container_name": c.name,
                    "status": c.status,
                    "memory_stack": c.labels.get("memory-stack"),
                }
                for c in containers
            ]
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Get details of a specific Claude session."""
        container_name = f"claude-{session_id}"
        try:
            container = cast(Container, self.docker.containers.get(container_name))
            return {
                "session_id": session_id,
                "container_id": container.id,
                "container_name": container.name,
                "status": container.status,
                "memory_stack": container.labels.get("memory-stack"),
            }
        except NotFound:
            return {"status": "not_found", "session_id": session_id}

    def attach_info(self, session_id: str) -> dict[str, Any]:
        """Get info needed to attach to a session (for tmux integration)."""
        container_name = f"claude-{session_id}"
        try:
            self.docker.containers.get(container_name)
            return {
                "session_id": session_id,
                "container_name": container_name,
                "attach_cmd": f"docker attach {container_name}",
                "exec_cmd": f"docker exec -it {container_name} bash",
            }
        except NotFound:
            return {"status": "not_found", "session_id": session_id}

    def get_logs(self, session_id: str, tail: int = 100) -> dict[str, Any]:
        """Get logs for a session (from persistent log file or container)."""
        # First try the persistent log file
        log_file = LOG_DIR / session_id / "session.log"
        if log_file.exists():
            try:
                lines = log_file.read_text().splitlines()
                if tail > 0:
                    lines = lines[-tail:]
                return {
                    "session_id": session_id,
                    "source": "file",
                    "logs": "\n".join(lines),
                }
            except Exception as e:
                logger.warning(f"Error reading log file {log_file}: {e}")

        # Fall back to container logs if container exists
        container_name = f"claude-{session_id}"
        try:
            container = cast(Container, self.docker.containers.get(container_name))
            logs = container.logs(tail=tail).decode("utf-8", errors="replace")
            return {
                "session_id": session_id,
                "source": "container",
                "logs": logs,
            }
        except NotFound:
            return {
                "session_id": session_id,
                "status": "not_found",
                "error": "No logs available - container not found and no log file exists",
            }

    def capture_screen(self, session_id: str) -> dict[str, Any]:
        """Capture current tmux screen content for a session."""
        container_name = f"claude-{session_id}"
        try:
            container = cast(Container, self.docker.containers.get(container_name))
            if container.status != "running":
                return {
                    "session_id": session_id,
                    "status": "not_running",
                    "error": f"Container status: {container.status}",
                }

            # Run tmux capture-pane inside the container (as claude user who owns the session)
            # Use -e to preserve ANSI escape sequences for terminal rendering
            exit_code, output = container.exec_run(
                ["tmux", "capture-pane", "-t", TMUX_SESSION_NAME, "-p", "-e"],
                demux=False,
                user="claude",
            )

            if exit_code == 0:
                # Also get tmux window dimensions
                cols, rows = 80, 24  # defaults
                try:
                    size_exit, size_output = container.exec_run(
                        [
                            "tmux",
                            "display-message",
                            "-t",
                            TMUX_SESSION_NAME,
                            "-p",
                            "#{window_width} #{window_height}",
                        ],
                        demux=False,
                        user="claude",
                    )
                    logger.debug(
                        f"tmux size query: exit={size_exit}, output={size_output!r}"
                    )
                    if size_exit == 0:
                        parts = size_output.decode().strip().split()
                        if len(parts) == 2:
                            cols, rows = int(parts[0]), int(parts[1])
                            logger.debug(f"Parsed tmux size: cols={cols}, rows={rows}")
                except Exception as e:
                    logger.debug(f"Failed to get tmux size: {e}")

                logger.info(f"capture_screen returning cols={cols}, rows={rows}")
                return {
                    "session_id": session_id,
                    "status": "ok",
                    "screen": output.decode("utf-8", errors="replace"),
                    "cols": cols,
                    "rows": rows,
                }
            else:
                error_msg = output.decode("utf-8", errors="replace").strip()
                # Distinguish tmux not ready from other errors
                # Various tmux error messages when session/server doesn't exist yet:
                # - "no server running on /tmp/tmux-..."
                # - "session not found: claude"
                # - "error connecting to /tmp/tmux-10000/default (No such file or directory)"
                error_lower = error_msg.lower()
                tmux_not_ready = (
                    "no server running" in error_lower
                    or "session not found" in error_lower
                    or "error connecting to" in error_lower
                    or "no such file or directory" in error_lower
                )
                if tmux_not_ready:
                    return {
                        "session_id": session_id,
                        "status": "tmux_not_ready",
                        "error": error_msg,
                    }
                return {
                    "session_id": session_id,
                    "status": "error",
                    "error": error_msg,
                }

        except NotFound:
            return {
                "session_id": session_id,
                "status": "not_found",
                "error": "Container not found",
            }

    def send_keys(
        self, session_id: str, keys: str, literal: bool = True
    ) -> dict[str, Any]:
        """Send keystrokes to a tmux session.

        Args:
            session_id: The session identifier
            keys: The keys to send - either literal text or tmux key names
            literal: If True, send as literal text (using -l flag).
                     If False, send as tmux key names (e.g., "C-c", "Enter", "Up")
        """
        container_name = f"claude-{session_id}"
        try:
            container = cast(Container, self.docker.containers.get(container_name))
            if container.status != "running":
                return {
                    "session_id": session_id,
                    "status": "not_running",
                    "error": f"Container status: {container.status}",
                }

            # Build tmux command based on literal flag
            # Use debug level to avoid logging sensitive keystrokes (passwords, etc.)
            logger.debug(f"send_keys: keys={keys!r}, literal={literal}")
            if literal:
                # Send as literal text (no key name interpretation)
                cmd = ["tmux", "send-keys", "-t", TMUX_SESSION_NAME, "-l", keys]
            else:
                # Send as tmux key name (e.g., "C-c", "Enter", "Up")
                cmd = ["tmux", "send-keys", "-t", TMUX_SESSION_NAME, keys]
            logger.debug(f"send_keys: cmd={cmd}")

            exit_code, output = container.exec_run(cmd, demux=False, user="claude")

            if exit_code == 0:
                return {"session_id": session_id, "status": "ok"}
            else:
                return {
                    "session_id": session_id,
                    "status": "error",
                    "error": output.decode("utf-8", errors="replace").strip(),
                }

        except NotFound:
            return {
                "session_id": session_id,
                "status": "not_found",
                "error": "Container not found",
            }

    def resize_terminal(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        """Resize the tmux terminal for a session.

        Args:
            session_id: The session identifier
            cols: Number of columns
            rows: Number of rows
        """
        container_name = f"claude-{session_id}"
        try:
            container = cast(Container, self.docker.containers.get(container_name))
            if container.status != "running":
                return {
                    "session_id": session_id,
                    "status": "not_running",
                    "error": f"Container status: {container.status}",
                }

            # Resize tmux window to match terminal size
            # Multiple approaches since tmux resize without attached client is tricky:
            # 1. Set window-size to manual and aggressive-resize
            # 2. Resize the window/pane
            # 3. Set COLUMNS/LINES environment and send SIGWINCH
            resize_script = f"""
                # Enable aggressive resize and manual window sizing
                tmux set-option -g aggressive-resize on 2>/dev/null || true
                tmux set-option -t {TMUX_SESSION_NAME} window-size manual 2>/dev/null || true

                # Try to resize the window and pane
                tmux resize-window -t {TMUX_SESSION_NAME}:0 -x {cols} -y {rows} 2>/dev/null || true
                tmux resize-pane -t {TMUX_SESSION_NAME}:0 -x {cols} -y {rows} 2>/dev/null || true

                # Also try setting size via control mode (attach phantom client with size)
                tmux set-option -t {TMUX_SESSION_NAME} default-size {cols}x{rows} 2>/dev/null || true

                # Force refresh
                tmux refresh-client -t {TMUX_SESSION_NAME} 2>/dev/null || true
            """
            container.exec_run(
                ["bash", "-c", resize_script],
                demux=False,
                user="claude",
            )

            # Don't fail hard on resize errors - it's not critical
            return {"session_id": session_id, "status": "ok"}

        except NotFound:
            return {
                "session_id": session_id,
                "status": "not_found",
                "error": "Container not found",
            }

    # -------------------------------------------------------------------------
    # Environment Volume Management
    # -------------------------------------------------------------------------

    def create_environment_volume(self, volume_name: str) -> dict[str, Any]:
        """Create a Docker named volume for a persistent environment.

        Sets ownership to UID 10000 (claude user in the container).
        """
        # Validate volume name (defense-in-depth)
        is_valid, error_msg = validate_volume_name(volume_name)
        if not is_valid:
            logger.warning(
                f"Invalid volume name rejected: {volume_name!r} - {error_msg}"
            )
            return {"status": "error", "error": f"Invalid volume name: {error_msg}"}

        try:
            volume = cast(
                Volume,
                self.docker.volumes.create(
                    name=volume_name,
                    labels={
                        "managed-by": "claude-orchestrator",
                        "type": "environment",
                    },
                ),
            )
            # Set ownership of the volume root to claude user (UID 10000)
            # This is needed so the container can write to /home/claude
            self.docker.containers.run(
                "alpine:latest",
                command=["chown", "10000:10000", "/home/claude"],
                volumes={volume_name: {"bind": "/home/claude", "mode": "rw"}},
                remove=True,
                detach=False,
            )
            logger.info(f"Created environment volume: {volume_name}")
            return {"status": "created", "volume_name": volume.name}
        except APIError as e:
            logger.error(f"Failed to create volume {volume_name}: {e}")
            return {"status": "error", "error": str(e)}

    def delete_environment_volume(self, volume_name: str) -> dict[str, Any]:
        """Delete an environment volume."""
        # Validate volume name (defense-in-depth)
        is_valid, error_msg = validate_volume_name(volume_name)
        if not is_valid:
            logger.warning(
                f"Invalid volume name rejected: {volume_name!r} - {error_msg}"
            )
            return {"status": "error", "error": f"Invalid volume name: {error_msg}"}

        try:
            volume = cast(Volume, self.docker.volumes.get(volume_name))
            volume.remove(force=True)
            logger.info(f"Deleted environment volume: {volume_name}")
            return {"status": "deleted", "volume_name": volume_name}
        except NotFound:
            logger.debug(f"Volume not found: {volume_name}")
            return {"status": "not_found", "volume_name": volume_name}
        except APIError as e:
            logger.error(f"Failed to delete volume {volume_name}: {e}")
            return {"status": "error", "error": str(e)}

    def initialize_environment(
        self, volume_name: str, snapshot_path: str
    ) -> dict[str, Any]:
        """Create a volume and initialize it from a snapshot.

        Runs a one-shot container to extract the snapshot into the volume.
        """
        # Validate snapshot path (defense-in-depth)
        is_valid, error_msg = validate_snapshot_path(snapshot_path)
        if not is_valid:
            logger.warning(
                f"Invalid snapshot path rejected: {snapshot_path!r} - {error_msg}"
            )
            return {"status": "error", "error": f"Invalid snapshot path: {error_msg}"}

        # First create the volume
        create_result = self.create_environment_volume(volume_name)
        if create_result.get("status") == "error":
            return create_result

        # Run a temporary container to extract snapshot
        # Use --no-same-owner to set ownership during extraction (avoids slow chown -R)
        # The numeric UID/GID 10000:10000 matches the claude user in the claude-cloud container
        # Use timestamp suffix to avoid naming collisions if retried
        # NOTE: Must use Ubuntu (not Alpine) because BusyBox tar lacks --owner/--group
        init_container_name = f"init-{volume_name[:20]}-{int(time.time())}"
        try:
            self.docker.containers.run(
                "ubuntu:24.04",
                command=[
                    "sh",
                    "-c",
                    # Extract with ownership set to claude user (10000:10000)
                    # --no-same-owner ignores tarball UIDs, then we set owner during extraction
                    # This is faster than extracting then running chown -R for large snapshots
                    "tar --no-same-owner --owner=10000 --group=10000 -xzf /snapshot/snapshot.tar.gz -C /home/claude",
                ],
                name=init_container_name,
                volumes={
                    snapshot_path: {"bind": "/snapshot/snapshot.tar.gz", "mode": "ro"},
                    volume_name: {"bind": "/home/claude", "mode": "rw"},
                },
                remove=True,  # Auto-remove after completion
                detach=False,  # Wait for completion
            )
            logger.info(f"Initialized volume {volume_name} from {snapshot_path}")
            return {"status": "initialized", "volume_name": volume_name}
        except APIError as e:
            logger.error(f"Failed to initialize volume {volume_name}: {e}")
            # Clean up the volume we just created
            self.delete_environment_volume(volume_name)
            return {"status": "error", "error": str(e)}

    def reset_environment_volume(
        self, volume_name: str, snapshot_path: str | None = None
    ) -> dict[str, Any]:
        """Reset an environment volume by deleting and recreating it.

        If snapshot_path is provided, reinitializes from that snapshot.
        """
        # Delete existing volume
        delete_result = self.delete_environment_volume(volume_name)
        if delete_result.get("status") == "error":
            return delete_result

        # Recreate (optionally with initialization)
        if snapshot_path:
            return self.initialize_environment(volume_name, snapshot_path)
        else:
            return self.create_environment_volume(volume_name)

    # -------------------------------------------------------------------------
    # Container Management
    # -------------------------------------------------------------------------

    def _stop_container(self, container_name: str) -> bool:
        """Stop and remove a container. Returns True if successful."""
        try:
            container = cast(Container, self.docker.containers.get(container_name))
            container.stop(timeout=10)
            container.remove()
            logger.info(f"Stopped and removed container: {container_name}")
            return True
        except NotFound:
            logger.debug(f"Container not found: {container_name}")
            return False
        except APIError as e:
            logger.error(f"Error stopping container {container_name}: {e}")
            return False

    def cleanup_dead_containers(self) -> dict[str, Any]:
        """Clean up dead/exited Claude containers."""
        cleaned_containers: list[str] = []

        try:
            containers = cast(
                list[Container],
                self.docker.containers.list(
                    all=True, filters={"label": "claude-session", "status": "exited"}
                ),
            )
            for container in containers:
                try:
                    container.remove()
                    name = container.name or "unknown"
                    cleaned_containers.append(name)
                    logger.info(f"Cleaned up dead container: {name}")
                except APIError as e:
                    logger.warning(
                        f"Failed to remove dead container {container.name}: {e}"
                    )
        except APIError as e:
            logger.error(f"Error listing containers for cleanup: {e}")

        if cleaned_containers:
            logger.info(
                f"Cleanup complete: {len(cleaned_containers)} containers removed"
            )
        else:
            logger.info("Cleanup complete: no dead containers found")

        return {
            "status": "ok",
            "cleaned_containers": cleaned_containers,
        }

    def handle_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """Route incoming request to appropriate handler."""
        action = data.get("action")

        if action == "create":
            config = SessionConfig(
                session_id=data["session_id"],
                image=data.get("image", DEFAULT_IMAGE),
                memory_stack=data.get("memory_stack"),
                env=data.get("env", {}),
                git_repo_url=data.get("git_repo_url"),
                ssh_private_key=data.get("ssh_private_key"),
                github_token=data.get("github_token"),
                github_token_write=data.get("github_token_write"),
                claude_prompt=data.get("claude_prompt"),
                snapshot_path=data.get("snapshot_path"),
                environment_volume=data.get("environment_volume"),
            )
            return self.create_session(config)

        elif action == "stop":
            return self.stop_session(data["session_id"])

        elif action == "list":
            return self.list_sessions()

        elif action == "get":
            return self.get_session(data["session_id"])

        elif action == "attach_info":
            return self.attach_info(data["session_id"])

        elif action == "logs":
            return self.get_logs(data["session_id"], tail=data.get("tail", 100))

        elif action == "capture_screen":
            return self.capture_screen(data["session_id"])

        elif action == "send_keys":
            return self.send_keys(
                data["session_id"],
                data.get("keys", ""),
                literal=data.get("literal", True),
            )

        elif action == "resize_terminal":
            return self.resize_terminal(
                data["session_id"],
                data.get("cols", 80),
                data.get("rows", 24),
            )

        elif action == "ping":
            return {"status": "pong"}

        elif action == "cleanup":
            return self.cleanup_dead_containers()

        # Environment volume management
        elif action == "create_environment_volume":
            return self.create_environment_volume(data["volume_name"])

        elif action == "delete_environment_volume":
            return self.delete_environment_volume(data["volume_name"])

        elif action == "initialize_environment":
            return self.initialize_environment(
                data["volume_name"], data["snapshot_path"]
            )

        elif action == "reset_environment_volume":
            return self.reset_environment_volume(
                data["volume_name"], data.get("snapshot_path")
            )

        else:
            return {"status": "error", "error": f"Unknown action: {action}"}


class SocketServer:
    """Unix socket server for orchestrator communication."""

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator
        self.socket: socket.socket | None = None
        self.running = False

    def start(self) -> None:
        """Start the socket server."""
        # Clean up old socket
        if SOCKET_PATH.exists():
            os.unlink(SOCKET_PATH)

        # Ensure directory exists
        socket_dir = SOCKET_PATH.parent
        socket_dir.mkdir(parents=True, exist_ok=True)

        # Create socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, SOCKET_PERMISSIONS)
        self.socket.listen(5)

        self.running = True
        logger.info(f"Orchestrator listening on {SOCKET_PATH}")

        while self.running:
            try:
                conn, _ = self.socket.accept()
                self._handle_connection(conn)
            except OSError:
                if self.running:
                    raise
                break

    def stop(self) -> None:
        """Stop the socket server."""
        self.running = False
        if self.socket:
            self.socket.close()
        if SOCKET_PATH.exists():
            os.unlink(SOCKET_PATH)
        logger.info("Orchestrator stopped")

    def _handle_connection(self, conn: socket.socket) -> None:
        """Handle a single client connection."""
        try:
            # Read request (up to 1MB)
            chunks = []
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                # Simple protocol: assume complete JSON ends connection readable data
                try:
                    json.loads(b"".join(chunks).decode())
                    break
                except json.JSONDecodeError:
                    continue

            if not chunks:
                return

            data = json.loads(b"".join(chunks).decode())
            logger.info(
                f"Request: {data.get('action')} ({data.get('session_id', 'n/a')})"
            )

            response = self.orchestrator.handle_request(data)
            conn.send(json.dumps(response).encode())

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            conn.send(json.dumps({"status": "error", "error": "Invalid JSON"}).encode())
        except Exception as e:
            logger.exception("Error handling request")
            conn.send(json.dumps({"status": "error", "error": str(e)}).encode())
        finally:
            conn.close()


def main() -> None:
    """Main entry point."""
    orchestrator = Orchestrator()
    server = SocketServer(orchestrator)

    # Handle shutdown signals
    def shutdown(signum, _frame):
        logger.info(f"Received signal {signum}, shutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server.start()


if __name__ == "__main__":
    main()
