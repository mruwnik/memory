"""Add cloned_from_environment_id to claude_environments.

This migration adds:
1. cloned_from_environment_id foreign key column referencing claude_environments table

This allows tracking when an environment was created by cloning another environment,
in addition to the existing initialized_from_snapshot_id field.

Revision ID: 20260204_env_clone_source
Revises: 20260204_unique_github_repos
Create Date: 2026-02-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260204_env_clone_source"
down_revision: Union[str, None] = "20260204_unique_github_repos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add cloned_from_environment_id column with self-referential foreign key
    op.add_column(
        "claude_environments",
        sa.Column(
            "cloned_from_environment_id",
            sa.BigInteger(),
            sa.ForeignKey("claude_environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("claude_environments", "cloned_from_environment_id")
