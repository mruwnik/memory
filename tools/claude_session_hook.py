#!/usr/bin/env python3
"""
Claude Code Stop hook for sending session events to the memory system.

Finds the current turn (from last user message) and uploads those events.
Server deduplicates by event UUID.

Usage:
    Configure in ~/.claude/settings.json:

    {
      "hooks": {
        "Stop": [
          {
            "hooks": [{"type": "command", "command": "python /path/to/claude_session_hook.py"}]
          }
        ]
      }
    }

Environment variables:
    MEMORY_API_URL: Base URL
    MEMORY_API_TOKEN: Bearer token for authentication
"""

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = os.environ.get("MEMORY_API_URL", "")
API_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")


def send_batch(session_id, cwd, events):
    """Send events to the batch ingest API."""
    endpoint = f"{API_URL.rstrip('/')}/sessions/ingest/batch"

    payload = {
        "session_id": session_id,
        "cwd": cwd,
        "source": socket.gethostname(),
        "events": events,
    }

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


def normalize_event(raw):
    """Normalize a transcript event for the API."""
    message = raw.get("message")
    if isinstance(message, str):
        message = {"text": message}

    return {
        "uuid": raw.get("uuid", ""),
        "parent_uuid": raw.get("parent_uuid"),
        "timestamp": raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "type": raw.get("type", "unknown"),
        "user_type": raw.get("user_type"),
        "message": message,
        "is_meta": raw.get("is_meta", False),
        "is_sidechain": raw.get("is_sidechain", False),
        "cwd": raw.get("cwd"),
        "session_id": raw.get("session_id"),
        "version": raw.get("version"),
        "git_branch": raw.get("git_branch"),
    }


def find_current_turn(events):
    """Find events from the last user message onwards (the current turn)."""
    last_user_idx = -1
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("type") == "user":
            last_user_idx = i
            break

    if last_user_idx == -1:
        return events

    return events[last_user_idx:]


def upload_current_turn(session_id, cwd, transcript_path):
    """Read transcript and upload the current turn."""
    if not API_URL or not API_TOKEN:
        return

    transcript = Path(transcript_path).expanduser()
    if not transcript.exists():
        return

    # Parse all events
    events = []
    for line in transcript.read_text().strip().split("\n"):
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not events:
        return

    # Get current turn and normalize
    current_turn = find_current_turn(events)
    normalized = [normalize_event(e) for e in current_turn]

    # Send as batch
    try:
        send_batch(session_id, cwd, normalized)
    except (HTTPError, URLError):
        pass


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    session_id = hook_input.get("session_id")
    transcript_path = hook_input.get("transcript_path")
    cwd = hook_input.get("cwd", "")

    if session_id and transcript_path:
        upload_current_turn(session_id, cwd, transcript_path)

    sys.exit(0)


if __name__ == "__main__":
    main()
