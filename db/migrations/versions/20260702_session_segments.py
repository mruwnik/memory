"""Session transcript search: session_segment table + indexing watermark.

SessionSegments are SourceItem subtypes holding embedding-sized runs of
conversational messages from Claude Code session transcripts, so archived
sessions become searchable (issue #100). The sessions table gains the
indexing watermark columns the indexer uses to process only new lines.

Revision ID: 20260702_session_segments
Revises: 20260620_github_author_person
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# alembic_version.version_num is varchar(32); keep this id ≤32 chars.
revision: str = "20260702_session_segments"
down_revision: Union[str, None] = "20260620_github_author_person"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_segment",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_index", sa.Integer(), nullable=False),
        sa.Column("end_index", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "roles", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "models", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
    )
    op.create_index(
        "session_segment_session_idx",
        "session_segment",
        ["session_id", "start_index"],
        unique=True,
    )
    op.create_index("session_segment_time_idx", "session_segment", ["start_time"])

    op.add_column(
        "sessions",
        sa.Column("indexed_up_to", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sessions",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "indexed_at")
    op.drop_column("sessions", "indexed_up_to")
    op.drop_index("session_segment_time_idx", table_name="session_segment")
    op.drop_index("session_segment_session_idx", table_name="session_segment")
    op.drop_table("session_segment")
