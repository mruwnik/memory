"""Tests for path validation security functions."""

import pytest
from fastapi import HTTPException

from memory.api.app import validate_path_within_directory


def test_validate_path_allows_valid_file(tmp_path):
    """Allows access to a file within the base directory."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    result = validate_path_within_directory(tmp_path, "test.txt")
    assert result == test_file.resolve()


def test_validate_path_allows_nested_file(tmp_path):
    """Allows access to a file in a subdirectory."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    test_file = subdir / "test.txt"
    test_file.write_text("content")

    result = validate_path_within_directory(tmp_path, "subdir/test.txt")
    assert result == test_file.resolve()


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../etc/passwd",  # Path traversal
        "/etc/passwd",  # Absolute path
        "..%2F..%2Fetc%2Fpasswd",  # URL-encoded traversal
    ],
)
def test_validate_path_blocks_traversal_attacks(tmp_path, malicious_path):
    """Blocks various path traversal attack patterns."""
    with pytest.raises(HTTPException) as exc_info:
        validate_path_within_directory(tmp_path, malicious_path)

    assert exc_info.value.status_code in (403, 404)


def test_validate_path_blocks_traversal_in_middle(tmp_path):
    """Blocks traversal in middle of path."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    with pytest.raises(HTTPException) as exc_info:
        validate_path_within_directory(tmp_path, "subdir/../../etc/passwd")

    assert exc_info.value.status_code in (403, 404)


def test_validate_path_handles_nonexistent_file(tmp_path):
    """Returns 404 for nonexistent files."""
    with pytest.raises(HTTPException) as exc_info:
        validate_path_within_directory(tmp_path, "nonexistent.txt")

    assert exc_info.value.status_code == 404


def test_validate_path_blocks_symlink_escape(tmp_path):
    """Blocks symlinks that point outside the directory."""
    symlink = tmp_path / "escape"
    try:
        symlink.symlink_to("/etc")
    except OSError:
        pytest.skip("Cannot create symlinks on this system")

    with pytest.raises(HTTPException) as exc_info:
        validate_path_within_directory(tmp_path, "escape/passwd")

    assert exc_info.value.status_code == 403


def test_validate_path_allows_internal_symlink(tmp_path):
    """Allows symlinks that stay within the directory."""
    subdir = tmp_path / "real"
    subdir.mkdir()
    test_file = subdir / "file.txt"
    test_file.write_text("content")

    symlink = tmp_path / "link"
    try:
        symlink.symlink_to(subdir)
    except OSError:
        pytest.skip("Cannot create symlinks on this system")

    result = validate_path_within_directory(tmp_path, "link/file.txt")
    assert result.is_file()
