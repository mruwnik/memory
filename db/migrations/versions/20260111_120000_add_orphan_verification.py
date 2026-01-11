"""Add orphan verification columns to source_item.

Adds columns for tracking when items were last verified to exist at their
remote source, and how many consecutive verification failures they've had.
This enables efficient, gradual orphan detection across all source types.

Revision ID: 20260111_120000
Revises: 20260110_120000
Create Date: 2026-01-11

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260111_120000"
down_revision = "20260110_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add verification tracking columns
    op.add_column(
        "source_item",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_item",
        sa.Column(
            "verification_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # Ensure verification_failures can't go negative
    op.create_check_constraint(
        "verification_failures_non_negative",
        "source_item",
        "verification_failures >= 0",
    )

    # Index for efficient batch selection of items needing verification
    # Orders by type first (to group by source), then by last_verified_at (NULLS FIRST)
    op.create_index(
        "source_verified_at_idx",
        "source_item",
        ["type", "last_verified_at"],
    )


def downgrade() -> None:
    op.drop_index("source_verified_at_idx", table_name="source_item")
    op.drop_constraint(
        "verification_failures_non_negative", "source_item", type_="check"
    )
    op.drop_column("source_item", "verification_failures")
    op.drop_column("source_item", "last_verified_at")
