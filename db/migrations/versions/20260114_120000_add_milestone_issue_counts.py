"""Add issue counts to milestones.

Adds open_issues and closed_issues columns to github_milestones table
for tracking progress in the Projects view.

Revision ID: 20260114_120000
Revises: 20260112_120000
Create Date: 2026-01-14

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260114_120000"
down_revision = "20260112_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add open_issues count
    op.add_column(
        "github_milestones",
        sa.Column(
            "open_issues",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # Add closed_issues count
    op.add_column(
        "github_milestones",
        sa.Column(
            "closed_issues",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("github_milestones", "closed_issues")
    op.drop_column("github_milestones", "open_issues")
