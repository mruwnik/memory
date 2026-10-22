"""summary_request table: ingestion summaries resolved via the Message Batches API.

Revision ID: 20261023_summary_requests
Revises: 20260702_session_segments
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261023_summary_requests"
down_revision: Union[str, None] = "20260702_session_segments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "summary_request",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("chunk_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("modality", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("batch_id", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','submitted','failed')",
            name="summary_request_status_check",
        ),
    )
    op.create_index("summary_request_status_idx", "summary_request", ["status"])
    op.create_index("summary_request_batch_idx", "summary_request", ["batch_id"])
    op.create_index("summary_request_source_idx", "summary_request", ["source_id"])


def downgrade() -> None:
    op.drop_table("summary_request")
