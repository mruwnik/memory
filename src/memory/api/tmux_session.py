"""Tmux session management for Claude Code WebSocket terminal streaming.

This module handles the bidirectional terminal communication with tmux sessions
running inside Claude Code containers. It provides:

- Screen capture with adaptive polling (fast when active, slow when idle)
- Input forwarding from WebSocket to tmux
- Lifecycle phase management (startup -> running -> exit)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:
    from memory.api.orchestrator_client import OrchestratorClient

from memory.api.orchestrator_client import OrchestratorError

logger = logging.getLogger(__name__)

# Screen capture loop adaptive polling constants
SCREEN_FAST_INTERVAL = 0.05  # 50ms when typing (fast refresh)
SCREEN_NORMAL_INTERVAL = 0.5  # 500ms normal polling
SCREEN_SLOW_INTERVAL = 2.0  # 2s when idle
SCREEN_FAST_DURATION = 3.0  # Stay fast for 3s after last keystroke
SCREEN_BACKOFF_UNCHANGED_THRESHOLD = 4  # Start slowing after this many unchanged polls
SCREEN_BACKOFF_MULTIPLIER = 1.5  # Multiply interval by this on each idle poll


async def send_ws_json(
    websocket: WebSocket,
    msg_type: str,
    data: str | None = None,
    **extra: int | str | bool,
) -> None:
    """Send a JSON message with timestamp over WebSocket."""
    msg: dict[str, str | int | bool] = {
        "type": msg_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if data is not None:
        msg["data"] = data
    msg.update(extra)
    await websocket.send_json(msg)


async def screen_capture_loop(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
    activity_state: dict,
) -> None:
    """Stream terminal content with lifecycle phases.

    Phases:
    1. STARTUP: Stream container logs while waiting for tmux to be ready
    2. RUNNING: Stream tmux screen captures (interactive terminal)
    3. EXIT: Fetch and display final container logs

    Uses adaptive polling during RUNNING phase:
    - Fast (50ms) for 3 seconds after any keystroke
    - Normal (500ms) when screen is changing
    - Slow (up to 2s) when idle
    """
    last_screen = ""
    last_log_lines = 0
    consecutive_errors = 0
    max_startup_attempts = 60  # 30 seconds at 0.5s interval
    consecutive_unchanged = 0
    interval = SCREEN_NORMAL_INTERVAL
    phase = "startup"  # startup -> running -> exit

    while True:
        try:
            result = await client.capture_screen(session_id)
        except OrchestratorError as e:
            await send_ws_json(websocket, "error", str(e))
            break

        status = result["status"]
        now = asyncio.get_event_loop().time()
        last_input = float(activity_state.get("last_input_time", 0))
        recently_active = (now - last_input) < SCREEN_FAST_DURATION

        if status == "ok":
            # Tmux is ready - switch to running phase
            if phase == "startup":
                phase = "running"
                await send_ws_json(websocket, "phase", "running")

            consecutive_errors = 0
            screen = result["screen"]
            cols = result.get("cols", 80)
            rows = result.get("rows", 24)
            logger.debug(
                f"screen_capture_loop: result has cols={result.get('cols')}, rows={result.get('rows')}, using cols={cols}, rows={rows}"
            )
            # Only send if screen changed (avoid noise)
            if screen and screen != last_screen:
                logger.info(f"Sending screen message with cols={cols}, rows={rows}")
                await send_ws_json(websocket, "screen", screen, cols=cols, rows=rows)
                last_screen = screen
                consecutive_unchanged = 0
                interval = (
                    SCREEN_FAST_INTERVAL if recently_active else SCREEN_NORMAL_INTERVAL
                )
            else:
                if recently_active:
                    interval = SCREEN_FAST_INTERVAL
                else:
                    consecutive_unchanged += 1
                    if consecutive_unchanged >= SCREEN_BACKOFF_UNCHANGED_THRESHOLD:
                        interval = min(
                            interval * SCREEN_BACKOFF_MULTIPLIER, SCREEN_SLOW_INTERVAL
                        )

        elif status in ("not_found", "not_running"):
            # Container exited - fetch final logs
            phase = "exit"
            await send_ws_json(websocket, "phase", "exit")
            try:
                logs_result = await client.get_logs(session_id, tail=500)
                logger.info(
                    f"Exit logs result for {session_id}: {logs_result is not None}, has logs: {bool(logs_result and logs_result.get('logs'))}"
                )
                if logs_result and logs_result.get("logs"):
                    await send_ws_json(websocket, "logs", logs_result["logs"])
                else:
                    # No logs available - send a message explaining why
                    await send_ws_json(
                        websocket,
                        "logs",
                        f"[No logs available - container status: {status}]",
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch final logs for {session_id}: {e}")
                await send_ws_json(websocket, "logs", f"[Failed to fetch logs: {e}]")
            await send_ws_json(websocket, "status", "Session ended")
            break

        elif status == "tmux_not_ready":
            # Still in startup phase - stream container logs
            consecutive_errors += 1
            if phase == "startup":
                try:
                    logs_result = await client.get_logs(session_id, tail=100)
                    if logs_result and logs_result.get("logs"):
                        log_lines = logs_result["logs"].split("\n")
                        # Only send new lines
                        if len(log_lines) > last_log_lines:
                            new_lines = "\n".join(log_lines[last_log_lines:])
                            if new_lines.strip():
                                await send_ws_json(websocket, "log", new_lines)
                            last_log_lines = len(log_lines)
                except Exception as e:
                    logger.debug(f"Failed to fetch startup logs: {e}")

            if consecutive_errors >= max_startup_attempts:
                await send_ws_json(
                    websocket, "status", "Tmux session not available after 30s"
                )
                break
            interval = SCREEN_NORMAL_INTERVAL

        else:
            # Generic error
            await send_ws_json(
                websocket, "error", str(result.get("error", "Unknown error"))
            )
            consecutive_errors += 1
            if consecutive_errors >= 5:
                break

        # Wait for either the interval OR an input event (whichever comes first)
        input_event = activity_state.get("input_event")
        if input_event:
            try:
                await asyncio.wait_for(input_event.wait(), timeout=interval)
                input_event.clear()  # Reset for next input
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue polling
        else:
            await asyncio.sleep(interval)


async def input_handler_loop(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
    activity_state: dict,
) -> None:
    """Receive input from WebSocket and send to tmux session.

    Updates activity_state["last_input_time"] on each keystroke to enable
    fast polling in the screen capture loop.
    """
    while True:
        try:
            message = await websocket.receive_json()
        except Exception as e:
            # Connection closed or error - log the exception type for debugging
            logger.debug(
                f"WebSocket receive ended for {session_id}: {type(e).__name__}"
            )
            break

        logger.debug(f"Received WebSocket message type: {message.get('type')}")
        msg_type = message.get("type")
        if msg_type == "input":
            keys = message.get("keys", "")
            # literal=True sends as literal text, literal=False sends as tmux key name
            literal = message.get("literal", True)
            if keys:
                # Record activity and wake up capture loop immediately
                activity_state["last_input_time"] = asyncio.get_event_loop().time()
                input_event = activity_state.get("input_event")
                if input_event:
                    input_event.set()
                try:
                    result = await client.send_keys(session_id, keys, literal=literal)
                    logger.debug(f"send_keys result: {result.get('status')}")
                    if result["status"] != "ok":
                        await send_ws_json(
                            websocket,
                            "error",
                            result.get("error", "Failed to send input"),
                        )
                except OrchestratorError as e:
                    await send_ws_json(websocket, "error", str(e))
        elif msg_type == "resize":
            cols = message.get("cols", 80)
            rows = message.get("rows", 24)
            try:
                result = await client.resize_terminal(session_id, cols, rows)
                if result["status"] != "ok":
                    logger.debug(f"resize_terminal failed: {result.get('error')}")
            except OrchestratorError as e:
                logger.debug(f"resize_terminal error: {e}")
