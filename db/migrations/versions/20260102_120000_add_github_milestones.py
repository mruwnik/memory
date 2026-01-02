"""Add github_milestones table and milestone_id FK

Revision ID: h4c5d6e7f8g9
Revises: g3b4c5d6e7f8
Create Date: 2026-01-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h4c5d6e7f8g9"
down_revision: Union[str, None] = "g3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create github_milestones table
    op.create_table(
        "github_milestones",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("due_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("github_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("github_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["github_repos.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "number", name="unique_milestone_per_repo"),
    )
    op.create_index(
        "github_milestones_repo_idx", "github_milestones", ["repo_id"]
    )
    op.create_index(
        "github_milestones_due_idx", "github_milestones", ["due_on"]
    )

    # Add milestone_id column to github_item
    op.add_column(
        "github_item",
        sa.Column("milestone_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_github_item_milestone",
        "github_item",
        "github_milestones",
        ["milestone_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("gh_milestone_id_idx", "github_item", ["milestone_id"])

    # Drop the old milestone text column
    op.drop_column("github_item", "milestone")


def downgrade() -> None:
    # Re-add the milestone text column
    op.add_column(
        "github_item",
        sa.Column("milestone", sa.Text(), nullable=True),
    )

    # Drop milestone_id FK and column
    op.drop_index("gh_milestone_id_idx", table_name="github_item")
    op.drop_constraint("fk_github_item_milestone", "github_item", type_="foreignkey")
    op.drop_column("github_item", "milestone_id")

    # Drop github_milestones table
    op.drop_index("github_milestones_due_idx", table_name="github_milestones")
    op.drop_index("github_milestones_repo_idx", table_name="github_milestones")
    op.drop_table("github_milestones")
