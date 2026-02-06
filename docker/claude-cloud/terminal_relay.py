"""Lightweight TCP server for fast tmux interaction inside Claude containers.

Eliminates docker exec overhead (~200-500ms per call) by running inside the
container and using subprocess calls (~1-5ms each) to talk to tmux directly.

Protocol: newline-delimited JSON over TCP.
  Request:  {"action": "capture"|"send_keys"|"resize"|"ping", ...}\n
  Response: {"status": "ok"|"error", ...}\n
"""

import asyncio
import json
import logging
import subprocess
import sys

HOST = "0.0.0.0"
PORT = 9100
TMUX_SESSION = "claude"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s relay %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("relay")

# Error substrings that indicate tmux server/session isn't ready yet
TMUX_NOT_READY_MARKERS = [
    "no server running",
    "session not found",
    "error connecting to",
    "no such file or directory",
    "no current",
    "can't find",
]


def tmux_run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        timeout=5,
    )


def is_tmux_not_ready(stderr: str) -> bool:
    lower = stderr.lower()
    return any(marker in lower for marker in TMUX_NOT_READY_MARKERS)


def handle_capture() -> dict:
    # Capture visible pane content
    result = tmux_run("capture-pane", "-t", TMUX_SESSION, "-p", "-e")
    if result.returncode != 0:
        if is_tmux_not_ready(result.stderr):
            return {"status": "tmux_not_ready"}
        return {"status": "error", "error": result.stderr.strip()}

    screen = result.stdout

    # Get terminal dimensions and scrollback history size
    dim = tmux_run(
        "display-message", "-t", TMUX_SESSION, "-p",
        "#{window_width} #{window_height} #{history_size}",
    )
    cols, rows, history_size = 80, 24, 0
    if dim.returncode == 0 and dim.stdout.strip():
        parts = dim.stdout.strip().split()
        if len(parts) >= 2:
            try:
                cols, rows = int(parts[0]), int(parts[1])
            except ValueError:
                pass
        if len(parts) >= 3:
            try:
                history_size = int(parts[2])
            except ValueError:
                pass

    return {
        "status": "ok",
        "screen": screen,
        "cols": cols,
        "rows": rows,
        "history_size": history_size,
    }


def handle_capture_history(start: int, end: int) -> dict:
    """Capture scrollback lines from tmux history buffer.

    Args:
        start: negative offset from visible area (e.g. -100 = 100 lines back)
        end: negative offset, must be < start (e.g. -1 = line just above visible)
    """
    # No -e flag: plain text only. Escape sequences from the TUI (cursor
    # positioning, color resets) would corrupt xterm.js rendering when replayed.
    result = tmux_run(
        "capture-pane", "-t", TMUX_SESSION, "-p",
        "-S", str(start), "-E", str(end),
    )
    if result.returncode != 0:
        if is_tmux_not_ready(result.stderr):
            return {"status": "tmux_not_ready"}
        return {"status": "error", "error": result.stderr.strip()}
    return {"status": "ok", "content": result.stdout}


def handle_send_keys(keys: str, literal: bool = True) -> dict:
    args = ["send-keys", "-t", TMUX_SESSION]
    if literal:
        args.append("-l")
    args.append(keys)

    result = tmux_run(*args)
    if result.returncode != 0:
        if is_tmux_not_ready(result.stderr):
            return {"status": "tmux_not_ready"}
        return {"status": "error", "error": result.stderr.strip()}
    return {"status": "ok"}


def handle_resize(cols: int, rows: int) -> dict:
    sc, sr = str(cols), str(rows)

    # With no attached client (entrypoint uses wait loop, not attach),
    # resize-window freely controls the terminal dimensions.
    result = tmux_run("resize-window", "-t", f"{TMUX_SESSION}:0", "-x", sc, "-y", sr)
    if result.returncode != 0:
        if is_tmux_not_ready(result.stderr):
            return {"status": "tmux_not_ready"}
        log.warning("resize-window failed: %s", result.stderr.strip())
        return {"status": "error", "error": result.stderr.strip()}

    # Resize pane explicitly in case window resize didn't cascade
    tmux_run("resize-pane", "-t", f"{TMUX_SESSION}:0", "-x", sc, "-y", sr)

    log.info("Resized to %sx%s", sc, sr)
    return {"status": "ok"}


def dispatch(request: dict) -> dict:
    action = request.get("action")
    if action == "ping":
        return {"status": "ok"}
    if action == "capture":
        return handle_capture()
    if action == "send_keys":
        return handle_send_keys(
            request.get("keys", ""),
            request.get("literal", True),
        )
    if action == "resize":
        return handle_resize(
            request.get("cols", 80),
            request.get("rows", 24),
        )
    if action == "capture_history":
        return handle_capture_history(
            request.get("start", -1000),
            request.get("end", -1),
        )
    return {"status": "error", "error": f"unknown action: {action}"}


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    log.info("Client connected: %s", peer)
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                response = {"status": "error", "error": f"bad json: {e}"}
            else:
                try:
                    response = await asyncio.get_running_loop().run_in_executor(None, dispatch, request)
                except Exception as e:
                    response = {"status": "error", "error": str(e)}

            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
        pass
    finally:
        log.info("Client disconnected: %s", peer)
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    server = await asyncio.start_server(handle_client, HOST, PORT)
    log.info("Terminal relay listening on %s:%d", HOST, PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
