"""Add support for standalone projects (not GitHub-backed).

This migration makes repo_id, github_id, and number nullable on github_milestones
to allow creating projects that aren't backed by GitHub milestones.

Revision ID: 20260126_120000
Revises: 20260124_120000
Create Date: 2026-01-26 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260126_120000"
down_revision: Union[str, None] = "20260124_120000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make repo_id nullable to allow standalone projects
    op.alter_column(
        "github_milestones",
        "repo_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    # Make github_id nullable
    op.alter_column(
        "github_milestones",
        "github_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    # Make number nullable
    op.alter_column(
        "github_milestones",
        "number",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # Drop the unique constraint that requires repo_id + number
    # (it will fail for standalone projects with null repo_id)
    op.drop_constraint("unique_milestone_per_repo", "github_milestones", type_="unique")

    # Create a partial unique constraint that only applies when repo_id is not null
    op.create_index(
        "unique_github_milestone_per_repo",
        "github_milestones",
        ["repo_id", "number"],
        unique=True,
        postgresql_where=sa.text("repo_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Remove the partial index
    op.drop_index("unique_github_milestone_per_repo", "github_milestones")

    # Recreate the original unique constraint
    # Note: This will fail if there are standalone projects
    op.create_unique_constraint(
        "unique_milestone_per_repo", "github_milestones", ["repo_id", "number"]
    )

    # Make columns non-nullable again
    # Note: This will fail if there are standalone projects with null values
    op.alter_column(
        "github_milestones",
        "number",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "github_milestones",
        "github_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.alter_column(
        "github_milestones",
        "repo_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
