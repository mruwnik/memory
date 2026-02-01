"""Add session summary columns.

This migration adds:
1. summary - AI-generated description of what was done in the session
2. summary_updated_at - timestamp of when the summary was last generated

Revision ID: 20260201_session_summary
Revises: 20260201_teams
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_session_summary"
down_revision: Union[str, None] = "20260201_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "summary_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "summary_updated_at")
    op.drop_column("sessions", "summary")
