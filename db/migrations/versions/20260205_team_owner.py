"""Add owner_id field to teams table.

This migration adds:
1. owner_id foreign key column referencing people table
2. Index on owner_id for efficient lookups

The owner field allows assigning a person responsible for the team,
similar to how projects have owners.

Revision ID: 20260205_team_owner
Revises: 20260204_scheduled_tasks
Create Date: 2026-02-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260205_team_owner"
down_revision: Union[str, None] = "20260204_scheduled_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add owner_id column with foreign key to people table
    op.add_column(
        "teams",
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Add index for efficient owner lookups
    op.create_index("teams_owner_idx", "teams", ["owner_id"])


def downgrade() -> None:
    op.drop_index("teams_owner_idx", table_name="teams")
    op.drop_column("teams", "owner_id")
