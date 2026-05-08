"""Tests for the path validation helpers in ``memory.common.paths``."""

from __future__ import annotations

import pathlib

import pytest

from memory.common import paths, settings


def test_to_db_filename_happy_path_inside_storage(tmp_path):
    # mock_file_storage autouse fixture has set FILE_STORAGE_DIR=tmp_path.
    target = settings.FILE_STORAGE_DIR / "notes" / "foo.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hi")

    assert paths.to_db_filename(target) == "notes/foo.md"


def test_to_db_filename_with_base_dir_relative_input():
    notes_dir = settings.NOTES_STORAGE_DIR
    # path is given relative to base_dir.
    assert paths.to_db_filename("foo.md", base_dir=notes_dir) == "notes/foo.md"


def test_to_db_filename_with_base_dir_relative_subdir():
    notes_dir = settings.NOTES_STORAGE_DIR
    # Subdirectory paths under base_dir round-trip cleanly.
    assert (
        paths.to_db_filename("subdir/bar.md", base_dir=notes_dir)
        == "notes/subdir/bar.md"
    )


def test_to_db_filename_rejects_relative_path_without_base_dir():
    # Bare filenames must not be silently resolved against CWD — that's a
    # footgun. Require absolute paths when base_dir is None.
    with pytest.raises(ValueError, match="must be absolute"):
        paths.to_db_filename("foo.md")


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.md",
        "../emails/msg.txt",
    ],
)
def test_to_db_filename_rejects_traversal_with_base_dir(bad_path):
    # `..` segments that try to escape base_dir must be rejected.
    with pytest.raises(ValueError):
        paths.to_db_filename(bad_path, base_dir=settings.NOTES_STORAGE_DIR)


def test_to_db_filename_rejects_path_under_base_dir_outside_file_storage(tmp_path):
    # If base_dir itself sits outside FILE_STORAGE_DIR, a target under it
    # must also fall outside FILE_STORAGE_DIR — and the final
    # `relative_to(FILE_STORAGE_DIR)` step in `to_db_filename` raises. We're
    # not asserting an explicit upfront "base_dir inside FILE_STORAGE_DIR"
    # check (the production callers are pinned by the import-time invariant
    # in settings.py); this test only pins the indirect rejection.
    outside_base = tmp_path.parent / "outside_storage"
    outside_base.mkdir(parents=True, exist_ok=True)
    target = outside_base / "x.md"
    target.write_text("hi")

    with pytest.raises(ValueError):
        paths.to_db_filename(target, base_dir=outside_base)


def test_to_db_filename_rejects_path_outside_file_storage(tmp_path):
    # No base_dir, absolute path, but outside FILE_STORAGE_DIR — should raise
    # the relative_to ValueError.
    outside = tmp_path.parent / "elsewhere" / "thing.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("hi")

    with pytest.raises(ValueError):
        paths.to_db_filename(outside)


def test_to_db_filename_require_exists_happy_path():
    notes_dir = settings.NOTES_STORAGE_DIR
    real_file = notes_dir / "exists.md"
    real_file.write_text("hi")

    result = paths.to_db_filename(
        "exists.md", base_dir=notes_dir, require_exists=True
    )
    assert result == "notes/exists.md"


def test_to_db_filename_require_exists_missing_file():
    with pytest.raises(ValueError):
        paths.to_db_filename(
            "missing.md", base_dir=settings.NOTES_STORAGE_DIR, require_exists=True
        )


def test_validate_path_within_directory_rejects_traversal():
    with pytest.raises(ValueError, match="escapes"):
        paths.validate_path_within_directory(
            settings.NOTES_STORAGE_DIR, "../../escape.md"
        )


def test_validate_path_within_directory_strips_leading_slash():
    notes_dir = settings.NOTES_STORAGE_DIR
    # Leading slash is stripped, not treated as absolute.
    resolved = paths.validate_path_within_directory(notes_dir, "/foo.md")
    assert resolved == (notes_dir / "foo.md").resolve()


def test_validate_path_within_directory_require_exists_missing():
    with pytest.raises(ValueError, match="does not exist"):
        paths.validate_path_within_directory(
            settings.NOTES_STORAGE_DIR, "missing.md", require_exists=True
        )
