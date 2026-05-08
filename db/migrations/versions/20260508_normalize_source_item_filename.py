"""Normalize legacy SourceItem.filename values to FILE_STORAGE_DIR-relative paths.

Background
----------
Earlier code paths persisted host-absolute paths into ``source_item.filename``
(e.g. ``Users/dan/code/memory/memory_files/notes/foo.md`` — a leading-slashed
path that had been ``lstrip("/")``-ed somewhere along the line). The current
``core_fetch_file`` flow looks up ``SourceItem.filename`` by the
``FILE_STORAGE_DIR``-relative path it received from the caller, so legacy rows
can never be matched and the fetch fails with ``FileNotFoundError`` even
though the file is on disk.

This migration strips any host-path prefix that ends in ``memory_files/`` so
those rows end up storing the same relative form the application uses today
(``notes/foo.md``, ``emails/.../foo.pdf``, etc.).

Rows whose ``filename`` is already relative (no ``memory_files/`` segment) are
left untouched. The migration does not touch the on-disk files.

Revision ID: 20260508_normalize_filename
Revises: 20260507_access_logs
Create Date: 2026-05-08
"""

from typing import Sequence, Union

from alembic import op


# alembic_version.version_num is varchar(32); keep this id ≤32 chars.
revision: str = "20260508_normalize_filename"
down_revision: Union[str, None] = "20260507_access_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE source_item
           SET filename = regexp_replace(filename, '^.*memory_files/', '')
         WHERE filename ~ '^.+memory_files/'
        """
    )


def downgrade() -> None:
    # Original prefixes are not recoverable; this is a one-way data fix.
    pass
