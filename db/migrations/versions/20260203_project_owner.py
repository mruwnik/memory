"""Add owner_id field to projects table.

This migration adds:
1. owner_id foreign key column referencing people table
2. Index on owner_id for efficient lookups

The owner field allows assigning a person responsible for the project.
For GitHub-backed projects, this can be set independently of GitHub
since GitHub milestones don't have an owner concept.

Revision ID: 20260203_project_owner
Revises: 20260201_teams
Create Date: 2026-02-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260203_project_owner"
down_revision: Union[str, None] = "20260201_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add owner_id column with foreign key to people table
    op.add_column(
        "projects",
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Add index for efficient owner lookups
    op.create_index("projects_owner_idx", "projects", ["owner_id"])


def downgrade() -> None:
    op.drop_index("projects_owner_idx", table_name="projects")
    op.drop_column("projects", "owner_id")
