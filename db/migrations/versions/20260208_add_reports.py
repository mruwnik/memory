"""Add reports table for uploaded HTML/PDF reports.

Revision ID: 20260208_add_reports
Revises: 20260205_team_owner
Create Date: 2026-02-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260208_add_reports"
down_revision: Union[str, None] = "20260205_team_owner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report_title", sa.Text(), nullable=True),
        sa.Column("report_format", sa.Text(), nullable=False),
        sa.Column("images", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.create_index("report_title_idx", "reports", ["report_title"])
    op.create_index("report_format_idx", "reports", ["report_format"])


def downgrade() -> None:
    op.drop_index("report_format_idx", table_name="reports")
    op.drop_index("report_title_idx", table_name="reports")
    op.drop_table("reports")
