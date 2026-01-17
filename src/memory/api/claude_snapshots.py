"""API endpoints for Claude Code config snapshots.

Manages config snapshots for running Claude Code in containers.
"""

import hashlib
import io
import json
import re
import tarfile
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import ClaudeConfigSnapshot, User

# Maximum snapshot file size (100 MB)
MAX_SNAPSHOT_SIZE = 100 * 1024 * 1024

# Maximum number of tar members to iterate (prevents zip bombs)
MAX_TAR_MEMBERS = 10000

# Maximum size of individual file to extract from tarball for reading
MAX_EXTRACT_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

router = APIRouter(prefix="/claude/snapshots", tags=["claude-snapshots"])


class SnapshotResponse(BaseModel):
    """Response model for snapshot operations."""

    id: int
    name: str
    content_hash: str
    claude_account_email: str | None
    subscription_type: str | None
    summary: str | None
    filename: str
    size: int
    created_at: str | None

    @classmethod
    def from_model(cls, snapshot: ClaudeConfigSnapshot) -> "SnapshotResponse":
        return cls(
            id=snapshot.id,
            name=snapshot.name,
            content_hash=snapshot.content_hash,
            claude_account_email=snapshot.claude_account_email,
            subscription_type=snapshot.subscription_type,
            summary=snapshot.summary,
            filename=snapshot.filename,
            size=snapshot.size,
            created_at=snapshot.created_at.isoformat() if snapshot.created_at else None,
        )


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:50]


def is_safe_tar_member(member: tarfile.TarInfo) -> bool:
    """Check if a tar member is safe to process.

    Validates against path traversal attacks and unsafe file types.
    """
    if member.name.startswith("/"):
        return False

    if ".." in member.name.split("/"):
        return False

    if member.islnk() or member.issym():
        if member.linkname.startswith("/") or ".." in member.linkname.split("/"):
            return False

    if member.isdev():
        return False

    return True


def extract_folders_from_tar(tar: tarfile.TarFile) -> dict[str, list[str]]:
    """Extract folder contents from tarball.

    Returns dict mapping folder names to list of unique top-level items in each folder.
    Stops after MAX_TAR_MEMBERS to prevent zip bomb DoS.

    Expects new format: .claude/skills/, .claude/agents/, etc.
    """
    folders: dict[str, set[str]] = {
        "skills": set(),
        "agents": set(),
        "plugins": set(),
        "hooks": set(),
        "commands": set(),
    }

    for count, member in enumerate(tar.getmembers()):
        if count >= MAX_TAR_MEMBERS:
            break

        if not is_safe_tar_member(member):
            continue

        parts = member.name.split("/")
        # Expect .claude/folder/name format
        if len(parts) < 3 or parts[0] != ".claude":
            continue

        folder, name = parts[1], parts[2]
        if folder in folders and name and not name.startswith("."):
            folders[folder].add(name)

    return {k: sorted(v) for k, v in folders.items()}


def extract_mcp_servers_from_tar(tar: tarfile.TarFile) -> list[str]:
    """Extract MCP server names from .claude.json in tarball."""
    try:
        member = tar.getmember(".claude.json")
    except KeyError:
        return []

    if not is_safe_tar_member(member) or member.size >= MAX_EXTRACT_FILE_SIZE:
        return []

    claude_json = tar.extractfile(member)
    if not claude_json:
        return []

    try:
        config = json.loads(claude_json.read().decode())
        return list(config.get("mcpServers", {}).keys())
    except json.JSONDecodeError:
        return []


def extract_happy_config_from_tar(tar: tarfile.TarFile) -> bool:
    """Check if tarball contains Happy config (access.key)."""
    try:
        member = tar.getmember(".happy/access.key")
        return is_safe_tar_member(member)
    except KeyError:
        return False


def extract_credentials_from_tar(tar: tarfile.TarFile) -> dict[str, str | None]:
    """Extract Claude account info from .claude/.credentials.json in tarball."""
    info: dict[str, str | None] = {
        "claude_account_email": None,
        "subscription_type": None,
    }

    try:
        member = tar.getmember(".claude/.credentials.json")
    except KeyError:
        return info

    if not is_safe_tar_member(member) or member.size >= MAX_EXTRACT_FILE_SIZE:
        return info

    creds_file = tar.extractfile(member)
    if not creds_file:
        return info

    try:
        creds = json.loads(creds_file.read().decode())
    except json.JSONDecodeError:
        return info

    claude_oauth = creds.get("claudeAiOauth", {})
    if not isinstance(claude_oauth, dict):
        return info

    info["claude_account_email"] = claude_oauth.get("email")
    info["subscription_type"] = claude_oauth.get("subscription_type")
    return info


def extract_snapshot_summary(content: bytes) -> dict[str, Any]:
    """Extract summary info from a snapshot tarball."""
    summary: dict[str, Any] = {
        "skills": [],
        "agents": [],
        "plugins": [],
        "hooks": [],
        "commands": [],
        "mcp_servers": [],
        "has_happy": False,
    }

    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            folders = extract_folders_from_tar(tar)
            summary.update(folders)
            summary["mcp_servers"] = extract_mcp_servers_from_tar(tar)
            summary["has_happy"] = extract_happy_config_from_tar(tar)
    except tarfile.TarError:
        pass

    return summary


def extract_account_info(content: bytes) -> dict[str, str | None]:
    """Extract Claude account info from snapshot credentials."""
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            return extract_credentials_from_tar(tar)
    except tarfile.TarError:
        return {"claude_account_email": None, "subscription_type": None}


def is_content_hash(identifier: str) -> bool:
    """Check if identifier looks like a SHA256 hash (64 hex chars)."""
    return len(identifier) == 64 and all(c in "0123456789abcdef" for c in identifier.lower())


def get_snapshot_by_identifier(
    identifier: str, user_id: int, db: DBSession
) -> ClaudeConfigSnapshot | None:
    """Get snapshot by ID or content hash."""
    if is_content_hash(identifier):
        return (
            db.query(ClaudeConfigSnapshot)
            .filter(
                ClaudeConfigSnapshot.content_hash == identifier,
                ClaudeConfigSnapshot.user_id == user_id,
            )
            .first()
        )

    try:
        snapshot_id = int(identifier)
    except ValueError:
        return None

    return (
        db.query(ClaudeConfigSnapshot)
        .filter(
            ClaudeConfigSnapshot.id == snapshot_id,
            ClaudeConfigSnapshot.user_id == user_id,
        )
        .first()
    )


@router.post("/upload")
async def upload_snapshot(
    file: UploadFile = File(...),
    name: str = Form(...),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SnapshotResponse:
    """Upload a new config snapshot. Deduplicates by content hash."""
    content = await file.read()

    if len(content) > MAX_SNAPSHOT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Snapshot too large. Maximum size is {MAX_SNAPSHOT_SIZE // (1024 * 1024)} MB",
        )

    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz"):
            pass
    except tarfile.TarError as e:
        raise HTTPException(status_code=400, detail=f"Invalid tarball: {e}")

    content_hash = hashlib.sha256(content).hexdigest()

    existing = (
        db.query(ClaudeConfigSnapshot)
        .filter(ClaudeConfigSnapshot.content_hash == content_hash)
        .first()
    )
    if existing:
        return SnapshotResponse.from_model(existing)

    filename = f"{user.id}/{content_hash[:12]}_{slugify(name)}.tar.gz"
    path = settings.SNAPSHOT_STORAGE_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    summary = extract_snapshot_summary(content)
    account_info = extract_account_info(content)

    snapshot = ClaudeConfigSnapshot(
        user_id=user.id,
        name=name,
        content_hash=content_hash,
        filename=filename,
        size=len(content),
        summary=json.dumps(summary),
        **account_info,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return SnapshotResponse.from_model(snapshot)


@router.get("/list")
def list_snapshots(
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[SnapshotResponse]:
    """List all snapshots for the current user."""
    snapshots = (
        db.query(ClaudeConfigSnapshot)
        .filter(ClaudeConfigSnapshot.user_id == user.id)
        .order_by(ClaudeConfigSnapshot.created_at.desc())
        .all()
    )
    return [SnapshotResponse.from_model(s) for s in snapshots]


@router.get("/{identifier}")
def get_snapshot(
    identifier: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SnapshotResponse:
    """Get a specific snapshot by ID or content hash.

    Accepts either:
    - Numeric ID (e.g., "42")
    - SHA256 content hash (64 hex characters)
    """
    snapshot = get_snapshot_by_identifier(identifier, user.id, db)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return SnapshotResponse.from_model(snapshot)


@router.delete("/{identifier}")
def delete_snapshot(
    identifier: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict:
    """Delete a snapshot by ID or content hash."""
    snapshot = get_snapshot_by_identifier(identifier, user.id, db)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    path = settings.SNAPSHOT_STORAGE_DIR / snapshot.filename
    path.unlink(missing_ok=True)

    db.delete(snapshot)
    db.commit()

    return {"deleted": True}
