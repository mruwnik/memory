"""One note row per filename.

Revision ID: 20261105_unique_note_filename
Revises: 20261104_chunk_is_summary
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_unique_note_filename"
down_revision: Union[str, None] = "20261104_chunk_is_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def retire_duplicate_notes(conn) -> None:
    """Unlink all but the newest of the notes sharing a filename.

    They share one file, which holds the newest row's content. The older rows
    keep their content, but get a hash no note write produces: sha256 is
    unique, so a later write of that content would otherwise dedupe onto them.
    """
    conn.execute(
        sa.text(
            """
            UPDATE source_item SET filename = NULL, sha256 = sha256(sha256 || int8send(id))
            WHERE type = 'note' AND filename IS NOT NULL AND id NOT IN (
                SELECT max(id) FROM source_item
                WHERE type = 'note' AND filename IS NOT NULL
                GROUP BY filename
            )
            """
        )
    )


def upgrade() -> None:
    retire_duplicate_notes(op.get_bind())
    # Concurrently, so writes to the large source_item table keep flowing. A
    # failed concurrent build leaves an invalid index behind, so drop it first.
    with op.get_context().autocommit_block():
        op.drop_index(
            "source_note_filename_key",
            table_name="source_item",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "source_note_filename_key",
            "source_item",
            ["filename"],
            unique=True,
            postgresql_where=sa.text("type = 'note'"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "source_note_filename_key",
            table_name="source_item",
            postgresql_concurrently=True,
            if_exists=True,
        )
