"""Celery entry points for agent runs (queue `agents`, consumed by agents-worker).

Each task imports its implementation inside the function, not at module top: the
implementations need the API package (fastmcp, the MCP server, enqueue_run), which
only the agents-worker image installs, while every worker autodiscovers this module.
"""

import asyncio

from memory.common import module_settings, settings
from memory.common.celery_app import (
    FIRE_SUBSCRIPTION,
    RUN_AGENT,
    START_TIME_RUN,
    SWEEP_WORKER_RUNS,
    app,
)


def follow_module_flags() -> None:
    """Catch up with the modules the api runs, once per task: this worker mounts the MCP
    servers in-process and would otherwise keep the set it first saw.

    The set holds for the whole task, so a module switched off mid-run stays visible to
    that run.
    """
    # Before the MCP server is first imported, whose boot checks are the api's.
    module_settings.follow_modules()
    from memory.api.MCP.base import mount_followed_modules

    mount_followed_modules()


@app.task(
    name=RUN_AGENT,
    time_limit=settings.AGENT_MAX_TIMEOUT_SEC + 600,
    soft_time_limit=settings.AGENT_MAX_TIMEOUT_SEC + 300,
)
def run_agent(run_id: str) -> None:
    """Run one agent. A redelivery finds the run no longer pending and does nothing."""
    follow_module_flags()
    from memory.agents.runner import execute_run

    asyncio.run(execute_run(run_id))


@app.task(name=FIRE_SUBSCRIPTION)
def fire_subscription(subscription_id: int, deliveries: list[str]) -> str | None:
    """Start a run of a subscription's definition for the items it matched."""
    follow_module_flags()
    from memory.agents.fire import fire_subscription as fire

    return fire(subscription_id, deliveries)


@app.task(name=START_TIME_RUN)
def start_time_run(event_id: int) -> str | None:
    """Start the agent run of a due tick or timer."""
    follow_module_flags()
    from memory.agents.cron import start_time_run as start

    return start(event_id)


@app.task(name=SWEEP_WORKER_RUNS)
def sweep_worker_runs() -> dict[str, int]:
    """Release lapsed worker leases, move *_first runs past fallback_after to their
    other side, and end worker runs nobody claimed within retention."""
    follow_module_flags()
    from memory.agents.work import sweep_worker_runs as sweep

    return sweep()
