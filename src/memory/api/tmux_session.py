"""Tmux session management for Claude Code WebSocket terminal streaming.

This module handles the bidirectional terminal communication with tmux sessions
running inside Claude Code containers. It provides:

- Screen capture with adaptive polling (fast when active, slow when idle)
- Input forwarding from WebSocket to tmux
- Lifecycle phase management (startup -> running -> exit)

All tmux interaction goes through the in-container terminal relay (TCP).
The orchestrator client is only used for container logs during startup/exit.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:
    from memory.api.orchestrator_client import OrchestratorClient

from memory.api.terminal_relay_client import RelayClient, RelayError

logger = logging.getLogger(__name__)

# Screen capture loop adaptive polling constants
SCREEN_FAST_INTERVAL = 0.05  # 50ms when typing (fast refresh)
SCREEN_NORMAL_INTERVAL = 0.5  # 500ms normal polling
SCREEN_SLOW_INTERVAL = 2.0  # 2s when idle
SCREEN_FAST_DURATION = 3.0  # Stay fast for 3s after last keystroke
SCREEN_BACKOFF_UNCHANGED_THRESHOLD = 4  # Start slowing after this many unchanged polls
SCREEN_BACKOFF_MULTIPLIER = 1.5  # Multiply interval by this on each idle poll

SCROLL_LINES = 3  # Lines per scroll wheel event


async def send_live_screen(
    websocket: WebSocket,
    relay: RelayClient,
    activity_state: dict,
) -> bool:
    """Capture the live screen and send it to the client with scrolled=0.

    Returns True if the screen was successfully captured and sent.
    """
    result = await relay.capture_screen()
    if result["status"] == "ok":
        # Only reset scroll_offset after a successful capture,
        # so the "jump to bottom" button stays visible if capture fails.
        activity_state["scroll_offset"] = 0
        screen = str(result["screen"])
        cols = result.get("cols", 80)
        rows = result.get("rows", 24)
        await send_ws_json(
            websocket, "screen", screen,
            cols=cols, rows=rows, scrolled=0,
        )
        return True
    error = result.get("error", result["status"])
    await send_ws_json(websocket, "error", f"Failed to capture screen: {error}")
    return False


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


async def fetch_and_send_exit_logs(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
) -> None:
    """Fetch final container logs via orchestrator and send to client."""
    try:
        logs_result = await client.get_logs(session_id, tail=500)
        if logs_result and logs_result.get("logs"):
            await send_ws_json(websocket, "logs", logs_result["logs"])
        else:
            await send_ws_json(websocket, "logs", "[No logs available]")
    except Exception as e:
        logger.warning(f"Failed to fetch final logs for {session_id}: {e}")
        await send_ws_json(websocket, "logs", f"[Failed to fetch logs: {e}]")
    await send_ws_json(websocket, "status", "Session ended")


async def fetch_startup_logs(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
    last_log_lines: int,
) -> int:
    """Fetch and send new startup log lines. Returns updated line count."""
    try:
        logs_result = await client.get_logs(session_id, tail=100)
    except Exception as e:
        logger.debug(f"Failed to fetch startup logs: {e}")
        return last_log_lines

    if not logs_result or not logs_result.get("logs"):
        return last_log_lines

    log_lines = logs_result["logs"].split("\n")
    if len(log_lines) <= last_log_lines:
        return last_log_lines

    new_lines = "\n".join(log_lines[last_log_lines:])
    if new_lines.strip():
        await send_ws_json(websocket, "log", new_lines)
    return len(log_lines)


async def screen_capture_loop(
    websocket: WebSocket,
    session_id: str,
    client: "OrchestratorClient",
    activity_state: dict,
    relay: RelayClient,
) -> None:
    """Stream terminal content with lifecycle phases.

    Phases:
    1. STARTUP: Relay not yet reachable, stream container logs
    2. RUNNING: Stream tmux screen captures via relay
    3. EXIT: Relay lost after running, fetch final container logs

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
            result = await relay.capture_screen()
        except RelayError as e:
            # Relay unreachable
            logger.debug("Relay failed, falling back to orchestrator: %s", e)
            if phase == "running":
                # Was running, relay died → container exited
                phase = "exit"
                await send_ws_json(websocket, "phase", "exit")
                await fetch_and_send_exit_logs(websocket, session_id, client)
                break
            # Still in startup — relay not ready yet
            consecutive_errors += 1
            last_log_lines = await fetch_startup_logs(
                websocket, session_id, client, last_log_lines,
            )
            if consecutive_errors >= max_startup_attempts:
                await send_ws_json(
                    websocket, "status", "Terminal relay not available after 30s"
                )
                break
            interval = SCREEN_NORMAL_INTERVAL
            await asyncio.sleep(interval)
            continue

        status = result["status"]
        now = asyncio.get_running_loop().time()
        last_input = float(activity_state.get("last_input_time", 0))
        recently_active = (now - last_input) < SCREEN_FAST_DURATION

        if status == "ok":
            # Tmux is ready - switch to running phase
            if phase == "startup":
                phase = "running"
                await send_ws_json(websocket, "phase", "running")
                # Apply any resize that arrived before the relay was ready
                pending = activity_state.pop("pending_resize", None)
                if pending:
                    try:
                        await relay.resize(*pending)
                    except RelayError:
                        pass

            consecutive_errors = 0
            screen = str(result["screen"])
            cols = result.get("cols", 80)
            rows = result.get("rows", 24)
            history_size = result.get("history_size", 0)
            activity_state["history_size"] = history_size
            activity_state["terminal_rows"] = rows

            # Only send if screen changed and user isn't scrolled back
            if screen and screen != last_screen:
                if activity_state.get("scroll_offset", 0) == 0:
                    await send_ws_json(
                        websocket, "screen", screen,
                        cols=cols, rows=rows, scrolled=0,
                    )
                    # Only update last_screen when actually sent to client,
                    # so the backoff timer doesn't reset while user is scrolled back
                    last_screen = screen
                    consecutive_unchanged = 0
                    interval = (
                        SCREEN_FAST_INTERVAL if recently_active else SCREEN_NORMAL_INTERVAL
                    )
                else:
                    # Scrolled back: screen is changing but we're not sending it.
                    # Use normal interval — no need to poll fast, but don't backoff
                    # since we'll want a fresh screen when the user scrolls back to live.
                    interval = SCREEN_NORMAL_INTERVAL
            else:
                if recently_active:
                    interval = SCREEN_FAST_INTERVAL
                else:
                    consecutive_unchanged += 1
                    if consecutive_unchanged >= SCREEN_BACKOFF_UNCHANGED_THRESHOLD:
                        interval = min(
                            interval * SCREEN_BACKOFF_MULTIPLIER, SCREEN_SLOW_INTERVAL
                        )

        elif status == "tmux_not_ready":
            # Relay is running but tmux session isn't up yet
            if phase == "running":
                # tmux died during running - transition to exit
                logger.info("Tmux became unavailable during running phase, transitioning to exit")
                phase = "exit"
                await send_ws_json(websocket, "phase", "exit")
                await fetch_and_send_exit_logs(websocket, session_id, client)
                break
            consecutive_errors += 1
            if phase == "startup":
                last_log_lines = await fetch_startup_logs(
                    websocket, session_id, client, last_log_lines,
                )
            if consecutive_errors >= max_startup_attempts:
                await send_ws_json(
                    websocket, "status", "Tmux session not available after 30s"
                )
                break
            interval = SCREEN_NORMAL_INTERVAL

        else:
            # Generic error from relay
            await send_ws_json(
                websocket, "error", str(result.get("error", "Unknown error"))
            )
            consecutive_errors += 1
            if consecutive_errors >= 5:
                if phase == "running":
                    phase = "exit"
                    await send_ws_json(websocket, "phase", "exit")
                    await fetch_and_send_exit_logs(websocket, session_id, client)
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
    relay: RelayClient,
    activity_state: dict,
) -> None:
    """Receive input from WebSocket and send to tmux session via relay.

    Updates activity_state["last_input_time"] on each keystroke to enable
    fast polling in the screen capture loop.
    """
    while True:
        try:
            message = await websocket.receive_json()
        except Exception as e:
            logger.debug(
                f"WebSocket receive ended for {session_id}: {type(e).__name__}"
            )
            break

        msg_type = message.get("type")
        if msg_type == "input":
            keys = message.get("keys", "")
            literal = message.get("literal", True)
            if keys:
                # Jump back to live view on any input
                activity_state["scroll_offset"] = 0
                # Record activity and wake up capture loop immediately
                activity_state["last_input_time"] = asyncio.get_running_loop().time()
                input_event = activity_state.get("input_event")
                if input_event:
                    input_event.set()
                try:
                    result = await relay.send_keys(keys, literal=literal)
                    if result["status"] != "ok":
                        await send_ws_json(
                            websocket,
                            "error",
                            result.get("error", "Failed to send input"),
                        )
                except RelayError as e:
                    await send_ws_json(websocket, "error", str(e))
        elif msg_type == "scroll":
            direction = message.get("direction", "down")
            lines = max(1, min(message.get("lines", SCROLL_LINES), 200))
            offset = activity_state.get("scroll_offset", 0)
            max_history = activity_state.get("history_size") or 1000

            if direction == "up":
                offset = min(offset + lines, max_history)
            else:
                offset = max(0, offset - lines)

            activity_state["scroll_offset"] = offset

            if offset > 0:
                rows = activity_state.get("terminal_rows", 24)
                start = -offset
                # end can go negative when offset >= rows, putting the entire
                # window in scrollback.  When offset < rows the window straddles
                # scrollback and the visible area (showing the lines the user
                # would see if they scrolled up by `offset` lines in a real terminal).
                # e.g. rows=24, offset=30 -> start=-30, end=-7 (pure scrollback)
                #      rows=24, offset=3  -> start=-3,  end=20  (3 scrollback + 21 visible)
                end = rows - 1 - offset
                try:
                    result = await relay.capture_range(start, end)
                    if result["status"] == "ok":
                        await send_ws_json(
                            websocket, "screen", result["content"],
                            scrolled=offset,
                        )
                except RelayError as e:
                    await send_ws_json(websocket, "error", str(e))
            else:
                # Back to live view - send current screen immediately
                try:
                    await send_live_screen(websocket, relay, activity_state)
                except RelayError as e:
                    logger.debug("RelayError on scroll-to-live: %s", e)
        elif msg_type == "scroll_to_bottom":
            try:
                await send_live_screen(websocket, relay, activity_state)
            except RelayError as e:
                logger.debug("RelayError on scroll-to-bottom: %s", e)
        elif msg_type == "resize":
            cols = message.get("cols", 80)
            rows = message.get("rows", 24)
            # Always store latest resize so it can be applied when relay becomes ready
            activity_state["pending_resize"] = (cols, rows)
            try:
                result = await relay.resize(cols, rows)
                if result["status"] == "ok":
                    del activity_state["pending_resize"]
                else:
                    logger.debug(f"resize failed: {result.get('error')}")
            except RelayError as e:
                logger.debug(f"resize error (will retry when relay ready): {e}")
