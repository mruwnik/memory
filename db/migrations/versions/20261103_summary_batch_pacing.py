"""Pace summary batches per key.

Provider keys remember a smaller batch size after a queue-limit refusal, and
summary requests record when they were claimed for a batch.

Revision ID: 20261103_summary_batch_pacing
Revises: 20261103_instance_settings
Create Date: 2026-11-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_summary_batch_pacing"
down_revision: Union[str, None] = "20261103_instance_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_provider_keys", sa.Column("summary_batch_limit", sa.Integer(), nullable=True)
    )
    op.add_column(
        "summary_request",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("summary_request", "submitted_at")
    op.drop_column("llm_provider_keys", "summary_batch_limit")
