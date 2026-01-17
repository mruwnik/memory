#!/usr/bin/env python3
"""
Create Claude Code config snapshots for containerized deployment.

Packages ~/.claude/, ~/.claude.json, ~/.happy/, and optionally git credentials
into a tarball that extracts directly to the target home directory.

Usage:
    # Upload to server (returns snapshot ID)
    python tools/claude_snapshot.py --host https://memory.example.com --api-key $API_KEY

    # With git credentials for private marketplace repos
    python tools/claude_snapshot.py --host ... --git-token github.com=github_pat_xxx

    # Local only (for testing)
    python tools/claude_snapshot.py --output /tmp/snapshot.tar.gz --json

Environment variables:
    MEMORY_HOST: Default server URL
    MEMORY_API_KEY: Default API key
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import requests

# =============================================================================
# Configuration
# =============================================================================

CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_JSON = Path.home() / ".claude.json"
HAPPY_DIR = Path.home() / ".happy"
HOME_DIR = str(Path.home())
TARGET_HOME = "/home/claude"

# Items from ~/.claude/ to include
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

# Items from ~/.happy/ to include
HAPPY_ITEMS = ["access.key", "settings.json"]

# Extensions that need path rewriting
TEXT_EXTENSIONS = {".json", ".md", ".txt", ".yaml", ".yml", ".toml"}

# Default git usernames by host
DEFAULT_GIT_USERS = {
    "github.com": "x-access-token",  # Works for all GitHub token types
    "gitlab.com": "oauth2",
}

# Flags to copy from .claude.json (skip onboarding prompts)
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


# =============================================================================
# Path rewriting
# =============================================================================


def rewrite_paths(content: bytes, encoding: str = "utf-8") -> bytes:
    """Replace local home directory paths with container paths."""
    try:
        text = content.decode(encoding)
        return text.replace(HOME_DIR, TARGET_HOME).encode(encoding)
    except UnicodeDecodeError:
        return content  # Binary file, unchanged


def add_to_tar(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    """Add file/directory to tarball, rewriting paths in text files."""
    if path.is_dir():
        for child in path.iterdir():
            add_to_tar(tar, child, f"{arcname}/{child.name}")
        return

    if path.suffix.lower() in TEXT_EXTENSIONS:
        content = rewrite_paths(path.read_bytes())
        info = tarfile.TarInfo(name=arcname)
        info.size = len(content)
        info.mtime = int(path.stat().st_mtime)
        info.mode = path.stat().st_mode & 0o777
        tar.addfile(info, io.BytesIO(content))
    else:
        tar.add(path, arcname=arcname)


# =============================================================================
# Git credentials
# =============================================================================


def get_marketplace_repos() -> dict[str, list[str]]:
    """Extract git repos from known marketplaces, grouped by host."""
    repos: dict[str, list[str]] = {}
    path = CLAUDE_DIR / "plugins" / "known_marketplaces.json"

    if not path.exists():
        return repos

    try:
        for marketplace in json.loads(path.read_text()).values():
            source = marketplace.get("source", {})
            source_type = source.get("source", "")
            repo = source.get("repo", "")

            if source_type == "github" and repo:
                repos.setdefault("github.com", []).append(repo)
            elif source_type == "gitlab" and repo:
                repos.setdefault("gitlab.com", []).append(repo)
            elif source_type == "git":
                url = source.get("url", "")
                if "://" in url:
                    parts = url.split("://")[1].split("/", 1)
                    host = parts[0].split(":")[0]
                    if len(parts) > 1 and host:
                        repos.setdefault(host, []).append(parts[1].removesuffix(".git"))
    except (json.JSONDecodeError, KeyError):
        pass

    return repos


def make_git_credentials(
    creds: dict[str, tuple[str, str]],
    repos: dict[str, list[str]],
) -> bytes:
    """Create .git-credentials content with repo-specific entries.

    For hosts with known repos, creates repo-specific credential lines.
    For hosts without known repos, creates a host-only credential (works for any repo).
    """
    lines = []
    for host, (user, token) in creds.items():
        host_repos = repos.get(host, [])
        if host_repos:
            for repo in host_repos:
                lines.append(f"https://{user}:{token}@{host}/{repo}")
        else:
            # Host-only credential as fallback (works for any repo on this host)
            lines.append(f"https://{user}:{token}@{host}")
    # Ensure trailing newline for proper appending in entrypoint
    return ("\n".join(lines) + "\n").encode() if lines else b""


def make_gitconfig() -> bytes:
    """Create .gitconfig with credential helper."""
    return b"[credential]\n\thelper = store\n[safe]\n\tdirectory = *\n"


# =============================================================================
# Claude config
# =============================================================================


def make_minimal_claude_json() -> tuple[bytes, list[str]]:
    """Create minimal .claude.json for container use.

    Keeps OAuth, remote MCP servers, and onboarding flags.
    """
    config: dict = {"mcpServers": {}}
    servers: list[str] = []

    if not CLAUDE_JSON.exists():
        return json.dumps(config, indent=2).encode(), servers

    try:
        source = json.loads(CLAUDE_JSON.read_text())

        # Copy OAuth account
        if "oauthAccount" in source:
            config["oauthAccount"] = source["oauthAccount"]

        # Copy onboarding flags
        for flag in ONBOARDING_FLAGS:
            if flag in source:
                config[flag] = source[flag]

        # Pre-trust /workspace
        config["projects"] = {
            "/workspace": {
                "allowedTools": [],
                "hasTrustDialogAccepted": True,
                "hasClaudeMdExternalIncludesApproved": False,
            }
        }

        # Keep remote HTTP MCP servers only
        for name, server in source.get("mcpServers", {}).items():
            if server.get("type") == "http":
                url = server.get("url", "")
                if "localhost" not in url and "127.0.0.1" not in url:
                    config["mcpServers"][name] = server
                    servers.append(name)

    except json.JSONDecodeError:
        pass

    return json.dumps(config, indent=2).encode(), servers


# =============================================================================
# Snapshot creation
# =============================================================================


def create_snapshot(
    git_creds: dict[str, tuple[str, str]] | None = None,
) -> tuple[bytes, dict]:
    """Create snapshot tarball of Claude config."""
    buf = io.BytesIO()
    summary: dict = {
        "skills": [],
        "agents": [],
        "plugins": [],
        "hooks": [],
        "commands": [],
        "mcp_servers": [],
        "has_happy": False,
        "git_repos": {},
    }

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # ~/.claude/* -> .claude/*
        for item in SNAPSHOT_ITEMS:
            path = CLAUDE_DIR / item
            if path.exists():
                add_to_tar(tar, path, f".claude/{item}")
                if path.is_dir():
                    summary[item] = [f.name for f in path.iterdir() if not f.name.startswith(".")]

        # ~/.claude.json -> .claude.json (minimal)
        content, servers = make_minimal_claude_json()
        content = rewrite_paths(content)
        info = tarfile.TarInfo(name=".claude.json")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
        summary["mcp_servers"] = servers

        # ~/.happy/* -> .happy/*
        happy_found = []
        for item in HAPPY_ITEMS:
            path = HAPPY_DIR / item
            if path.exists():
                add_to_tar(tar, path, f".happy/{item}")
                happy_found.append(item)
        summary["has_happy"] = "access.key" in happy_found

        # Git credentials
        repos = get_marketplace_repos()
        summary["git_repos"] = {h: sorted(r) for h, r in repos.items()}

        if git_creds:
            relevant = {h: c for h, c in git_creds.items() if h in repos}
            if relevant:
                # .git-credentials
                content = make_git_credentials(relevant, repos)
                info = tarfile.TarInfo(name=".git-credentials")
                info.size = len(content)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(content))

                # .gitconfig
                content = make_gitconfig()
                info = tarfile.TarInfo(name=".gitconfig")
                info.size = len(content)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))

                summary["git_credentials_repos"] = {h: sorted(repos[h]) for h in relevant}

    return buf.getvalue(), summary


# =============================================================================
# Server upload
# =============================================================================


def upload_snapshot(host: str, api_key: str, name: str, data: bytes, hash_: str, as_json: bool):
    """Upload snapshot to server, reusing existing if hash matches."""
    host = host.rstrip("/").removesuffix("/ui")
    headers = {"Authorization": f"Bearer {api_key}"}

    # Check if already exists
    resp = requests.get(f"{host}/claude/snapshots/{hash_}", headers=headers)
    if resp.status_code == 200:
        try:
            result = {"id": resp.json()["id"], "hash": hash_, "created": False}
            print(json.dumps(result) if as_json else result["id"])
            return
        except (json.JSONDecodeError, KeyError):
            pass
    elif resp.status_code != 404:
        print(f"Error checking snapshot: {resp.status_code}", file=sys.stderr)
        print(f"Response: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    # Upload new
    resp = requests.post(
        f"{host}/claude/snapshots/upload",
        headers=headers,
        files={"file": ("snapshot.tar.gz", data, "application/gzip")},
        data={"name": name},
    )
    if resp.status_code != 200:
        print(f"Error uploading: {resp.status_code}", file=sys.stderr)
        print(f"Response: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    try:
        result = {"id": resp.json()["id"], "hash": hash_, "created": True}
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result) if as_json else result["id"])


# =============================================================================
# CLI
# =============================================================================


def parse_git_tokens(tokens: list[str] | None) -> dict[str, tuple[str, str]]:
    """Parse --git-token arguments into credentials dict."""
    if not tokens:
        return {}

    creds = {}
    for spec in tokens:
        if "=" not in spec:
            print(f"Error: Invalid format '{spec}'. Use HOST=TOKEN.", file=sys.stderr)
            sys.exit(1)

        host, value = spec.split("=", 1)
        if ":" in value:
            user, token = value.split(":", 1)
        else:
            user = DEFAULT_GIT_USERS.get(host)
            if not user:
                print(f"Error: No default user for '{host}'. Use HOST=USER:TOKEN.", file=sys.stderr)
                sys.exit(1)
            token = value

        creds[host] = (user, token)

    return creds


def main():
    parser = argparse.ArgumentParser(description="Create Claude Code config snapshot")
    parser.add_argument("--host", default=os.environ.get("MEMORY_HOST"), help="Server URL")
    parser.add_argument("--api-key", default=os.environ.get("MEMORY_API_KEY"), help="API key")
    parser.add_argument("--name", help="Snapshot name (default: timestamp)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    parser.add_argument("-o", "--output", help="Write to file instead of uploading")
    parser.add_argument(
        "--git-token",
        action="append",
        metavar="HOST=TOKEN",
        dest="git_tokens",
        help="Git credentials (HOST=TOKEN or HOST=USER:TOKEN). Repeatable.",
    )
    args = parser.parse_args()

    name = args.name or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    git_creds = parse_git_tokens(args.git_tokens)

    data, summary = create_snapshot(git_creds or None)
    hash_ = hashlib.sha256(data).hexdigest()

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if args.as_json:
            print(json.dumps({"path": str(path), "hash": hash_, "size": len(data), "summary": summary}))
        else:
            print(path)
    else:
        if not args.host:
            print("Error: --host or MEMORY_HOST required", file=sys.stderr)
            sys.exit(1)
        if not args.api_key:
            print("Error: --api-key or MEMORY_API_KEY required", file=sys.stderr)
            sys.exit(1)
        upload_snapshot(args.host, args.api_key, name, data, hash_, args.as_json)


if __name__ == "__main__":
    main()
