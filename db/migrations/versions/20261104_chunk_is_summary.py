"""Mark the chunks that hold an item's LLM summary.

Revision ID: 20261104_chunk_is_summary
Revises: 20261103_summary_batch_pacing
Create Date: 2026-11-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_chunk_is_summary"
down_revision: Union[str, None] = "20261103_summary_batch_pacing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chunk",
        sa.Column("is_summary", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("chunk", "is_summary")
