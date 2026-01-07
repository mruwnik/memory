"""Path validation utilities for secure file access."""

import pathlib


def validate_path_within_directory(
    base_dir: pathlib.Path,
    requested_path: str,
    require_exists: bool = False,
) -> pathlib.Path:
    """Validate that a requested path resolves within the base directory.

    Prevents path traversal attacks using ../, symlinks, or similar techniques.
    Uses pathlib's is_relative_to() for robust containment checking.

    Args:
        base_dir: The allowed base directory
        requested_path: The user-provided path (leading slashes are stripped)
        require_exists: If True, raise ValueError if the path doesn't exist

    Returns:
        The resolved absolute path if valid

    Raises:
        ValueError: If the path escapes the base directory or doesn't exist
                   when require_exists=True
    """
    # Resolve base directory first, then build target from resolved base
    # This prevents TOCTOU race if base_dir is a symlink that changes
    base_resolved = base_dir.resolve()

    # Build target from resolved base, stripping leading slashes to prevent absolute paths
    target = base_resolved / requested_path.lstrip("/")

    # Resolve the target path
    if require_exists:
        try:
            resolved = target.resolve(strict=True)
        except (OSError, ValueError):
            # Generic message to avoid leaking path info in logs/responses
            raise ValueError("Path does not exist")
    else:
        resolved = target.resolve()

    # Use pathlib's is_relative_to for proper path containment check
    # This is safer than string comparison as it handles edge cases
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        # Generic message to avoid leaking path info in logs/responses
        raise ValueError("Path escapes base directory")

    return resolved
