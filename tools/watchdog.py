"""
Docker container health watchdog.

Standalone monitoring script that:
1. Checks all Docker Compose containers for health/running status
2. Sends Discord webhook alerts when containers are unhealthy or stopped
3. Pings an external healthcheck URL as a dead man's switch

Designed to run as its own container with access to the Docker socket,
independent of the Celery worker so it can detect worker failures.
"""

import json
import logging
import os
import pathlib
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.client import HTTPConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("watchdog")

# ── Configuration ────────────────────────────────────────────────────────────

CHECK_INTERVAL = int(os.getenv("WATCHDOG_CHECK_INTERVAL", "60"))
ALERT_COOLDOWN = int(os.getenv("WATCHDOG_ALERT_COOLDOWN", "900"))  # 15 min
STARTUP_GRACE = int(os.getenv("WATCHDOG_STARTUP_GRACE", "120"))  # 2 min
DISCORD_WEBHOOK_URL = os.getenv("WATCHDOG_DISCORD_WEBHOOK_URL", "")
HEALTHCHECK_PING_URL = os.getenv("HEALTHCHECK_PING_URL", "")
COMPOSE_PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "memory")
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
DOCKER_TIMEOUT = int(os.getenv("WATCHDOG_DOCKER_TIMEOUT", "30"))
ALIVE_FILE = "/tmp/watchdog-alive"
MAX_DISCORD_MESSAGE_LENGTH = 1900  # Discord limit is 2000, leave margin

# Services to monitor - these should always be running
EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "qdrant",
    "api",
    "worker",
    "ingest-hub",
}
# Allow overriding via env
if extra := os.getenv("WATCHDOG_SERVICES"):
    EXPECTED_SERVICES = set(extra.split(","))

# Containers to skip (e.g. one-shot migration, backup)
SKIP_SERVICES = {"migrate", "backup"}
if extra_skip := os.getenv("WATCHDOG_SKIP_SERVICES"):
    SKIP_SERVICES = set(extra_skip.split(","))


# ── Docker socket HTTP client ────────────────────────────────────────────────


class DockerSocketConnection(HTTPConnection):
    """HTTP connection over a Unix domain socket."""

    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(DOCKER_TIMEOUT)
        self.sock.connect(self.socket_path)


def docker_get(path: str) -> dict | list:
    """Make a GET request to the Docker daemon via Unix socket."""
    conn = DockerSocketConnection(DOCKER_SOCKET)
    conn.request("GET", path)
    response = conn.getresponse()
    data = response.read().decode()
    conn.close()
    if response.status != 200:
        raise RuntimeError(f"Docker API {path} returned {response.status}: {data[:200]}")
    return json.loads(data)


# ── Container inspection ─────────────────────────────────────────────────────


def get_compose_containers() -> list[dict]:
    """Get all containers belonging to this Compose project."""
    filters = json.dumps({"label": [f"com.docker.compose.project={COMPOSE_PROJECT}"]})
    return docker_get(f"/containers/json?all=true&filters={urllib.parse.quote(filters)}")


def extract_service_name(container: dict) -> str:
    """Extract the Compose service name from container labels."""
    return container.get("Labels", {}).get("com.docker.compose.service", "unknown")


def check_container_health(container: dict) -> tuple[str, str]:
    """Return (status, detail) for a container.

    status is one of: "healthy", "unhealthy", "stopped", "starting"
    """
    state = container.get("State", "").lower()
    status = container.get("Status", "")

    if state == "running":
        # Check Docker healthcheck status if configured
        if "(healthy)" in status.lower():
            return "healthy", status
        if "(unhealthy)" in status.lower():
            return "unhealthy", status
        if "(health: starting)" in status.lower():
            return "starting", status
        # Running but no healthcheck configured
        return "healthy", status

    if state == "exited":
        return "stopped", status
    if state == "restarting":
        return "unhealthy", f"restarting: {status}"
    if state == "dead":
        return "stopped", f"dead: {status}"
    if state == "created":
        return "starting", status

    return "unhealthy", f"unknown state: {state} ({status})"


# ── Alerting ─────────────────────────────────────────────────────────────────

# Track when we last alerted for each service to avoid spam
last_alert_time: dict[str, float] = {}
# Track which services were previously unhealthy so we can send recovery alerts
previously_unhealthy: set[str] = set()


def should_alert(service: str) -> bool:
    """Check if enough time has passed since the last alert for this service."""
    last = last_alert_time.get(service, 0)
    return (time.time() - last) >= ALERT_COOLDOWN


def send_discord_alert(message: str) -> bool:
    """Send an alert via Discord webhook (no dependency on collector)."""
    if not DISCORD_WEBHOOK_URL:
        logger.warning("No WATCHDOG_DISCORD_WEBHOOK_URL configured, alert not sent")
        return False

    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except (urllib.error.URLError, OSError) as e:
        logger.error(f"Failed to send Discord alert: {e}")
        return False


def ping_healthcheck(suffix: str = "") -> bool:
    """Ping external healthcheck URL (dead man's switch)."""
    if not HEALTHCHECK_PING_URL:
        return True  # No URL configured, skip silently

    url = HEALTHCHECK_PING_URL.rstrip("/") + suffix
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as e:
        logger.error(f"Failed to ping healthcheck: {e}")
        return False


# ── Main loop ────────────────────────────────────────────────────────────────


def check_all_services() -> dict[str, tuple[str, str]]:
    """Check all expected services and return {service: (status, detail)}."""
    containers = get_compose_containers()
    found_services: dict[str, tuple[str, str]] = {}

    for container in containers:
        service = extract_service_name(container)
        if service in SKIP_SERVICES:
            continue
        status, detail = check_container_health(container)
        found_services[service] = (status, detail)

    # Check for missing services (expected but not found at all)
    for service in EXPECTED_SERVICES:
        if service not in found_services:
            found_services[service] = ("stopped", "container not found")

    return found_services


def run_check() -> None:
    """Run a single health check cycle."""
    global previously_unhealthy

    try:
        services = check_all_services()
    except Exception as e:
        logger.error(f"Failed to query Docker: {e}")
        ping_healthcheck("/fail")
        return

    problems = []
    recovered = []
    now_unhealthy: set[str] = set()

    for service, (status, detail) in sorted(services.items()):
        if service not in EXPECTED_SERVICES:
            continue

        if status in ("unhealthy", "stopped", "starting"):
            now_unhealthy.add(service)
            if status != "starting" and should_alert(service):
                problems.append((service, status, detail))
        elif service in previously_unhealthy:
            recovered.append(service)

    # Send problem alerts
    if problems:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"**Container Alert** ({timestamp})"]
        for service, status, detail in problems:
            emoji = "🔴" if status == "stopped" else "🟡"
            lines.append(f"{emoji} **{service}**: {status} — {detail}")
        message = "\n".join(lines)[:MAX_DISCORD_MESSAGE_LENGTH]
        logger.warning(message)
        if send_discord_alert(message):
            for service, _, _ in problems:
                last_alert_time[service] = time.time()

    # Send recovery alerts
    if recovered:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"**Container Recovery** ({timestamp})"]
        for service in recovered:
            lines.append(f"🟢 **{service}** is healthy again")
        message = "\n".join(lines)[:MAX_DISCORD_MESSAGE_LENGTH]
        logger.info(message)
        send_discord_alert(message)

    previously_unhealthy = now_unhealthy

    # Ping external healthcheck (dead man's switch)
    if problems:
        ping_healthcheck("/fail")
    else:
        ping_healthcheck()

    # Touch alive file for watchdog's own healthcheck
    try:
        pathlib.Path(ALIVE_FILE).touch()
    except OSError:
        pass

    # Log summary
    healthy_count = sum(
        1 for s, (st, _) in services.items() if st == "healthy" and s in EXPECTED_SERVICES
    )
    logger.info(
        f"Check complete: {healthy_count}/{len(EXPECTED_SERVICES)} healthy"
        + (f", {len(problems)} alerts sent" if problems else "")
    )


def main():
    logger.info(
        f"Watchdog starting: monitoring {sorted(EXPECTED_SERVICES)}, "
        f"interval={CHECK_INTERVAL}s, cooldown={ALERT_COOLDOWN}s"
    )
    if not DISCORD_WEBHOOK_URL:
        logger.warning("WATCHDOG_DISCORD_WEBHOOK_URL not set — alerts will only be logged")
    if not HEALTHCHECK_PING_URL:
        logger.warning("HEALTHCHECK_PING_URL not set — no external dead man's switch")

    # Startup grace period — let other containers come up
    logger.info(f"Waiting {STARTUP_GRACE}s startup grace period...")
    time.sleep(STARTUP_GRACE)

    while True:
        run_check()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
