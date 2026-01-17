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
from typing import Any, Literal

import hashlib

import docker
from docker.errors import APIError, ImageNotFound, NotFound

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
    claude_prompt: str | None = None
    snapshot_path: str | None = None


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
            img = self.docker.images.get(image)
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

        _, logs = self.docker.images.build(
            path=str(INSTALL_DIR),
            dockerfile=dockerfile,
            tag=image,
            rm=True,
            labels=labels,
        )

        # Log build output
        for chunk in logs:
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
            if config.claude_prompt:
                environment["CLAUDE_PROMPT"] = config.claude_prompt

            # Build volume mounts
            volumes = {}
            if config.snapshot_path:
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
            container = self.docker.containers.run(
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
            )
            logger.info(f"Created container: {container_name} ({container.short_id})")

            # Connect to memory API network if specified
            if config.memory_stack:
                memory_network_name = f"memory-api-{config.memory_stack}"
                try:
                    memory_network = self.docker.networks.get(memory_network_name)
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
        containers = self.docker.containers.list(
            all=True, filters={"label": "claude-session"}
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
            container = self.docker.containers.get(container_name)
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
            container = self.docker.containers.get(container_name)
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

    def _stop_container(self, container_name: str) -> bool:
        """Stop and remove a container. Returns True if successful."""
        try:
            container = self.docker.containers.get(container_name)
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
        cleaned_containers = []

        try:
            containers = self.docker.containers.list(
                all=True, filters={"label": "claude-session", "status": "exited"}
            )
            for container in containers:
                try:
                    container.remove()
                    cleaned_containers.append(container.name)
                    logger.info(f"Cleaned up dead container: {container.name}")
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
                claude_prompt=data.get("claude_prompt"),
                snapshot_path=data.get("snapshot_path"),
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

        elif action == "ping":
            return {"status": "pong"}

        elif action == "cleanup":
            return self.cleanup_dead_containers()

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
