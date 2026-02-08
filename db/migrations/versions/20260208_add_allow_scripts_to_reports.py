"""Add allow_scripts column to reports table for CSP control.

Revision ID: 20260208_add_allow_scripts_to_reports
Revises: 20260208_add_reports
Create Date: 2026-02-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260208_add_allow_scripts_to_reports"
down_revision: Union[str, None] = "20260208_add_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "allow_scripts",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "allow_scripts")
