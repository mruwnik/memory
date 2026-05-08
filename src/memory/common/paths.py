"""Path validation utilities for secure file access."""

import pathlib

from memory.common import settings


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


def to_db_filename(
    path: str | pathlib.Path,
    *,
    base_dir: pathlib.Path | None = None,
    require_exists: bool = False,
) -> str:
    """Compute the ``SourceItem.filename`` for a path on disk.

    The unified convention across SourceItem subtypes is to store filenames
    relative to ``FILE_STORAGE_DIR`` so that ``core_fetch_file`` and
    ``serve_file`` can look up rows by the same path they receive from
    callers.

    Args:
        path: The path to convert. When ``base_dir`` is given, may be
            absolute or relative to ``base_dir``. When ``base_dir`` is
            ``None``, ``path`` **must** be absolute — relative inputs are
            rejected to avoid silent CWD-resolution surprises.
        base_dir: Caller-supplied storage root (e.g. ``NOTES_STORAGE_DIR``,
            ``REPORT_STORAGE_DIR``). When provided, ``path`` is validated to
            lie inside it before the relative-to-``FILE_STORAGE_DIR`` form is
            computed — defense against ``..`` traversal in user-supplied
            paths. ``base_dir`` is expected to live inside
            ``FILE_STORAGE_DIR``; this is **not** checked up front, but the
            final ``relative_to(FILE_STORAGE_DIR)`` step will raise if it
            doesn't. Production callers (``NOTES_STORAGE_DIR``,
            ``REPORT_STORAGE_DIR``) are pinned by a startup-time invariant
            in ``settings.py``.
        require_exists: Forwarded to ``validate_path_within_directory`` when
            ``base_dir`` is given. Ignored otherwise.

    Returns:
        POSIX-style relative path suitable for ``SourceItem.filename``.

    Raises:
        ValueError: if ``path`` is relative and ``base_dir`` is ``None``, if
            the resolved path escapes ``base_dir`` (when given), or if it
            does not lie inside ``FILE_STORAGE_DIR``.
    """
    if base_dir is not None:
        resolved = validate_path_within_directory(
            base_dir, str(path), require_exists=require_exists
        )
    else:
        if not pathlib.Path(path).is_absolute():
            raise ValueError(
                f"path must be absolute when base_dir is None: {path!r}"
            )
        resolved = pathlib.Path(path).resolve()

    file_storage_root = settings.FILE_STORAGE_DIR.resolve()
    return resolved.relative_to(file_storage_root).as_posix()
