"""Clear SourceItem.filename on Google Drive docs.

A Drive doc's title was stored as its ``filename``, which /files,
core_fetch_file and email attachments read as a path under FILE_STORAGE_DIR.
Drive docs keep no file on disk; the title stays in ``google_doc.title``.

Revision ID: 20261225_null_gdoc_filename
Revises: 20261223_meeting_api_ext_ids
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261225_null_gdoc_filename"
down_revision: Union[str, None] = "20261223_meeting_api_ext_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE source_item SET filename = NULL "
        "WHERE type = 'google_doc' AND filename IS NOT NULL"
    )


def downgrade() -> None:
    # The titles are still in google_doc.title; the old value was never a path.
    pass
