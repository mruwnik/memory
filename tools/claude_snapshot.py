#!/usr/bin/env python3
"""
Get or create Claude Code config snapshot.

Idempotent tool that returns the snapshot ID for the current config.
If the config hasn't changed, returns the existing snapshot ID.
If the config is new, creates a snapshot and returns the new ID.

Usage:
    # Upload to server (returns snapshot ID)
    python tools/claude_snapshot.py --host https://memory.example.com --api-key $API_KEY
    # Output: 42

    python tools/claude_snapshot.py --host ... --api-key ... --name "work-config"
    python tools/claude_snapshot.py --host ... --api-key ... --json

    # Local only (for testing without server)
    python tools/claude_snapshot.py --output /tmp/snapshot.tar.gz
    # Output: /tmp/snapshot.tar.gz

Environment variables:
    MEMORY_HOST: Default host URL
    MEMORY_API_KEY: Default API key
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import sys
import tarfile
import requests
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_JSON = Path.home() / ".claude.json"
HAPPY_DIR = Path.home() / ".happy"

# Items to include in the snapshot (from ~/.claude/)
SNAPSHOT_ITEMS = [
    ".credentials.json",
    "settings.json",
    "skills",
    "agents",
    "plugins",
    "hooks",
    "commands",
    "CLAUDE.md",
]

# Happy config items (from ~/.happy/)
HAPPY_ITEMS = [
    "access.key",
    "settings.json",
]


def create_minimal_claude_json() -> tuple[bytes, list[str]]:
    """Create a minimal .claude.json for container use.

    Keeps oauthAccount, remote HTTP MCP servers (non-localhost), and
    onboarding/setup flags to skip first-run setup.

    Returns:
        Tuple of (json bytes, list of included MCP server names)
    """
    # Flags that skip onboarding/setup prompts
    ONBOARDING_FLAGS = [
        "hasCompletedOnboarding",
        "lastOnboardingVersion",
        "installMethod",
        "hasAcknowledgedCostThreshold",
        "hasSeenTasksHint",
        "hasSeenStashHint",
        "shiftEnterKeyBindingInstalled",
        "optionAsMetaKeyInstalled",
    ]

    minimal_config: dict = {"mcpServers": {}}
    mcp_servers: list[str] = []

    if CLAUDE_JSON.exists():
        try:
            config = json.loads(CLAUDE_JSON.read_text())

            # Copy oauthAccount if present
            if "oauthAccount" in config:
                minimal_config["oauthAccount"] = config["oauthAccount"]

            # Copy onboarding/setup flags
            for flag in ONBOARDING_FLAGS:
                if flag in config:
                    minimal_config[flag] = config[flag]

            # Create a minimal projects entry with trust accepted
            # This prevents the "trust this project?" dialog
            minimal_config["projects"] = {
                "/workspace": {
                    "allowedTools": [],
                    "hasTrustDialogAccepted": True,
                    "hasClaudeMdExternalIncludesApproved": False,
                }
            }

            # Filter MCP servers: keep HTTP ones that aren't localhost
            for name, server in config.get("mcpServers", {}).items():
                if server.get("type") == "http":
                    url = server.get("url", "")
                    if "localhost" not in url and "127.0.0.1" not in url:
                        minimal_config["mcpServers"][name] = server
                        mcp_servers.append(name)
        except json.JSONDecodeError:
            pass

    return json.dumps(minimal_config, indent=2).encode(), mcp_servers


def create_snapshot() -> tuple[bytes, dict]:
    """Create a snapshot tarball of Claude config.

    Returns:
        Tuple of (tarball bytes, summary dict)
    """
    buf = io.BytesIO()
    summary: dict = {
        "skills": [],
        "agents": [],
        "plugins": [],
        "hooks": [],
        "commands": [],
        "mcp_servers": [],
        "has_happy": False,
    }

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in SNAPSHOT_ITEMS:
            path = CLAUDE_DIR / item
            if path.exists():
                tar.add(path, arcname=item)
                if path.is_dir():
                    summary[item] = [
                        f.name for f in path.iterdir() if not f.name.startswith(".")
                    ]

        # Include minimal claude.json (remote MCP servers only)
        minimal_json, mcp_servers = create_minimal_claude_json()
        tarinfo = tarfile.TarInfo(name="claude.json")
        tarinfo.size = len(minimal_json)
        tar.addfile(tarinfo, io.BytesIO(minimal_json))
        summary["mcp_servers"] = mcp_servers

        # Include Happy config if present
        happy_items_found = []
        for item in HAPPY_ITEMS:
            path = HAPPY_DIR / item
            if path.exists():
                # Store under .happy/ prefix in the tarball
                tar.add(path, arcname=f".happy/{item}")
                happy_items_found.append(item)

        # Mark as having Happy config if access.key is present
        summary["has_happy"] = "access.key" in happy_items_found

    return buf.getvalue(), summary


def upload_snapshot(args, data, content_hash):
    # Server upload mode: requires host and api-key
    if not args.host:
        print(
            "Error: --host or MEMORY_HOST required (or use --output for local mode)",
            file=sys.stderr,
        )
        sys.exit(1)
    if not args.api_key:
        print(
            "Error: --api-key or MEMORY_API_KEY required (or use --output for local mode)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Normalize host URL - strip /ui suffix if present
    host = args.host.rstrip("/")
    if host.endswith("/ui"):
        host = host[:-3]

    headers = {"Authorization": f"Bearer {args.api_key}"}

    # Check if snapshot already exists (using unified endpoint that accepts hash)
    resp = requests.get(
        f"{host}/claude/snapshots/{content_hash}",
        headers=headers,
    )
    if resp.status_code == 200:
        try:
            data_json = resp.json()
            result = {"id": data_json["id"], "hash": content_hash, "created": False}
            if args.output_json:
                print(json.dumps(result))
            else:
                print(result["id"])
            return
        except (json.JSONDecodeError, KeyError):
            pass  # Fall through to create new snapshot
    elif resp.status_code != 404:
        # Unexpected error
        print(f"Error checking existing snapshot: {resp.status_code}", file=sys.stderr)
        print(f"Response: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    # Create new snapshot
    resp = requests.post(
        f"{host}/claude/snapshots/upload",
        headers=headers,
        files={"file": ("snapshot.tar.gz", data, "application/gzip")},
        data={"name": args.name},
    )
    if resp.status_code != 200:
        print(f"Error uploading snapshot: {resp.status_code}", file=sys.stderr)
        print(f"Response: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    try:
        result = {"id": resp.json()["id"], "hash": content_hash, "created": True}
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        print(f"Response: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps(result))
    else:
        print(result["id"])


def main():
    parser = argparse.ArgumentParser(
        description="Get or create Claude Code config snapshot"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MEMORY_HOST"),
        help="Memory server URL (or set MEMORY_HOST env var)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("MEMORY_API_KEY"),
        help="API key (or set MEMORY_API_KEY env var)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Name for new snapshots (default: current date and time)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output JSON with id, hash, and created flag",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Write snapshot to file instead of uploading (local testing mode)",
    )
    args = parser.parse_args()

    if args.name is None:
        args.name = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d_%H-%M-%S"
        )

    # Create snapshot from current config
    data, summary = create_snapshot()
    content_hash = hashlib.sha256(data).hexdigest()

    if not args.output:
        return upload_snapshot(args, data, content_hash)

    # Local-only mode: just write to file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)

    if args.output_json:
        print(
            json.dumps(
                {
                    "path": str(output_path),
                    "hash": content_hash,
                    "size": len(data),
                    "summary": summary,
                }
            )
        )
    else:
        print(output_path)


if __name__ == "__main__":
    main()
