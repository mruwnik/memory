"""Celery entry points for agent runs (queue `agents`, consumed by agents-worker).

Each task imports its implementation inside the function, not at module top: the
implementations need the API package (fastmcp, the MCP server, enqueue_run), which
only the agents-worker image installs, while every worker autodiscovers this module.
"""

import asyncio

from memory.common import settings
from memory.common.celery_app import FIRE_TRIGGER, RUN_AGENT, START_CRON_RUN, app


@app.task(
    name=RUN_AGENT,
    time_limit=settings.AGENT_MAX_TIMEOUT_SEC + 600,
    soft_time_limit=settings.AGENT_MAX_TIMEOUT_SEC + 300,
)
def run_agent(run_id: str) -> None:
    """Run one agent. A redelivery finds the run no longer pending and does nothing."""
    from memory.agents.runner import execute_run

    asyncio.run(execute_run(run_id))


@app.task(name=FIRE_TRIGGER)
def fire_trigger(trigger_id: int, deliveries: list[str]) -> str | None:
    """Start a run of a trigger's definition for the items it matched."""
    from memory.agents.fire import fire_trigger as fire

    return fire(trigger_id, deliveries)


@app.task(name=START_CRON_RUN)
def start_cron_run(execution_id: str) -> str | None:
    """Start the run for a due agent_run ScheduledTask execution."""
    from memory.agents.cron import start_cron_run as start

    return start(execution_id)
