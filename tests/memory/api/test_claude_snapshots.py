"""Tests for Claude Code config snapshots API."""

import io
import json
import tarfile

import pytest

from memory.api.claude_snapshots import (
    MAX_TAR_MEMBERS,
    extract_account_info,
    extract_snapshot_summary,
    is_safe_tar_member,
    slugify,
)


# Helper functions for creating test tarballs


def create_test_tarball(files: dict[str, bytes | str]) -> bytes:
    """Create a gzipped tarball with the given files.

    Args:
        files: Mapping of filename to content (str will be encoded as UTF-8)

    Returns:
        Gzipped tarball as bytes
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def create_malicious_tarball_with_path_traversal() -> bytes:
    """Create a tarball with path traversal attack."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        content = b"malicious"
        info = tarfile.TarInfo(name="../../../etc/passwd")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def create_tarball_with_many_members(count: int) -> bytes:
    """Create a tarball with many files (for zip bomb testing)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for i in range(count):
            content = f"file{i}".encode()
            info = tarfile.TarInfo(name=f"file{i}.txt")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# Tests for slugify


@pytest.mark.parametrize(
    "input_text,expected",
    [
        ("Hello World", "hello-world"),
        ("Test 123", "test-123"),
        ("UPPERCASE", "uppercase"),
        ("special!@#$chars", "specialchars"),
        ("  spaces  ", "spaces"),
        ("multiple---dashes", "multiple-dashes"),
        ("a" * 100, "a" * 50),  # Truncates to 50 chars
        ("", ""),
    ],
)
def test_slugify(input_text, expected):
    """Test slugify function converts text to URL-safe slug."""
    assert slugify(input_text) == expected


# Tests for is_safe_tar_member


def test_is_safe_tar_member_normal_file():
    """Test that normal files are considered safe."""
    info = tarfile.TarInfo(name="normal/file.txt")
    assert is_safe_tar_member(info) is True


def test_is_safe_tar_member_rejects_absolute_path():
    """Test that absolute paths are rejected."""
    info = tarfile.TarInfo(name="/etc/passwd")
    assert is_safe_tar_member(info) is False


@pytest.mark.parametrize(
    "path",
    [
        "../etc/passwd",
        "foo/../../../etc/passwd",
        "normal/../../../evil",
    ],
)
def test_is_safe_tar_member_rejects_path_traversal(path):
    """Test that path traversal is rejected."""
    info = tarfile.TarInfo(name=path)
    assert is_safe_tar_member(info) is False


def test_is_safe_tar_member_allows_safe_symlink():
    """Test that symlinks to safe targets are allowed."""
    info = tarfile.TarInfo(name="link")
    info.type = tarfile.SYMTYPE
    info.linkname = "relative/target"
    assert is_safe_tar_member(info) is True


def test_is_safe_tar_member_rejects_unsafe_symlink():
    """Test that symlinks to absolute paths are rejected."""
    info = tarfile.TarInfo(name="link")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    assert is_safe_tar_member(info) is False


def test_is_safe_tar_member_rejects_symlink_with_traversal():
    """Test that symlinks with path traversal are rejected."""
    info = tarfile.TarInfo(name="link")
    info.type = tarfile.SYMTYPE
    info.linkname = "../../../etc/passwd"
    assert is_safe_tar_member(info) is False


# Tests for extract_snapshot_summary


def test_extract_snapshot_summary_empty_tarball():
    """Test extracting summary from empty tarball."""
    content = create_test_tarball({})
    summary = extract_snapshot_summary(content)

    assert summary == {
        "skills": [],
        "agents": [],
        "plugins": [],
        "hooks": [],
        "commands": [],
        "mcp_servers": [],
        "has_happy": False,
    }


def test_extract_snapshot_summary_with_skills():
    """Test extracting skills from snapshot."""
    content = create_test_tarball(
        {
            ".claude/skills/skill1/config.json": "{}",
            ".claude/skills/skill2/config.json": "{}",
            ".claude/skills/.hidden/config.json": "{}",  # Should be ignored
        }
    )
    summary = extract_snapshot_summary(content)

    assert sorted(summary["skills"]) == ["skill1", "skill2"]


def test_extract_snapshot_summary_with_mcp_servers():
    """Test extracting MCP servers from .claude.json."""
    claude_config = {
        "mcpServers": {
            "memory": {"command": "memory-server"},
            "github": {"command": "github-server"},
        }
    }
    content = create_test_tarball({".claude.json": json.dumps(claude_config)})
    summary = extract_snapshot_summary(content)

    assert sorted(summary["mcp_servers"]) == ["github", "memory"]


def test_extract_snapshot_summary_rejects_path_traversal():
    """Test that path traversal members are ignored in summary."""
    content = create_malicious_tarball_with_path_traversal()
    # Should not raise, just skip the malicious member
    summary = extract_snapshot_summary(content)
    assert summary["skills"] == []


def test_extract_snapshot_summary_limits_members():
    """Test that member count is limited to prevent zip bombs."""
    # Create tarball with more members than allowed
    content = create_tarball_with_many_members(MAX_TAR_MEMBERS + 100)
    # Should not hang or crash, just process up to limit
    summary = extract_snapshot_summary(content)
    # The summary should still be a valid dict
    assert isinstance(summary, dict)


def test_extract_snapshot_summary_handles_invalid_tarball():
    """Test that invalid tarball returns empty summary."""
    summary = extract_snapshot_summary(b"not a valid tarball")

    assert summary == {
        "skills": [],
        "agents": [],
        "plugins": [],
        "hooks": [],
        "commands": [],
        "mcp_servers": [],
        "has_happy": False,
    }


# Tests for extract_account_info


def test_extract_account_info_with_credentials():
    """Test extracting account info from credentials file."""
    credentials = {
        "claudeAiOauth": {
            "email": "user@example.com",
            "subscription_type": "pro",
        }
    }
    content = create_test_tarball({".claude/.credentials.json": json.dumps(credentials)})
    info = extract_account_info(content)

    assert info["claude_account_email"] == "user@example.com"
    assert info["subscription_type"] == "pro"


def test_extract_account_info_no_credentials():
    """Test extracting account info when credentials missing."""
    content = create_test_tarball({"other.txt": "content"})
    info = extract_account_info(content)

    assert info["claude_account_email"] is None
    assert info["subscription_type"] is None


def test_extract_account_info_invalid_json():
    """Test extracting account info with invalid JSON."""
    content = create_test_tarball({".claude/.credentials.json": "not valid json"})
    info = extract_account_info(content)

    assert info["claude_account_email"] is None
    assert info["subscription_type"] is None


def test_extract_account_info_handles_invalid_tarball():
    """Test that invalid tarball returns empty info."""
    info = extract_account_info(b"not a valid tarball")

    assert info["claude_account_email"] is None
    assert info["subscription_type"] is None


# Integration tests requiring database


@pytest.mark.skip("Not implemented - needs FastAPI TestClient setup")
def test_upload_snapshot_size_validation():
    """Test that upload rejects files larger than MAX_SNAPSHOT_SIZE."""


@pytest.mark.skip("Not implemented - needs FastAPI TestClient setup")
def test_upload_snapshot_invalid_tarball():
    """Test that upload rejects invalid tarballs."""


@pytest.mark.skip("Not implemented - needs FastAPI TestClient setup")
def test_snapshot_deduplication():
    """Test that uploading the same content returns existing snapshot."""


@pytest.mark.skip("Not implemented - needs FastAPI TestClient setup")
def test_user_isolation():
    """Test that users cannot see each other's snapshots."""


@pytest.mark.skip("Not implemented - needs FastAPI TestClient setup")
def test_delete_removes_file():
    """Test that deleting a snapshot removes the file from disk."""
