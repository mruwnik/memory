"""Celery entry point for agent runs (queue `agents`, consumed by agents-worker)."""

import asyncio

from memory.common import settings
from memory.common.celery_app import RUN_AGENT, app


@app.task(
    name=RUN_AGENT,
    time_limit=settings.AGENT_MAX_TIMEOUT_SEC + 600,
    soft_time_limit=settings.AGENT_MAX_TIMEOUT_SEC + 300,
)
def run_agent(run_id: str) -> None:
    """Run one agent. A redelivery finds the run no longer pending and does nothing."""
    # Imported here, not at module top: the runner needs the API package
    # (fastmcp and the MCP server), which only the agents-worker image installs,
    # while every worker autodiscovers this module.
    from memory.agents.runner import execute_run

    asyncio.run(execute_run(run_id))
