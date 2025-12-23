"""add github tracking

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2025-12-23 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create github_accounts table
    op.create_table(
        "github_accounts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("app_id", sa.BigInteger(), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("private_key", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("auth_type IN ('pat', 'app')"),
    )
    op.create_index(
        "github_accounts_active_idx", "github_accounts", ["active", "last_sync_at"]
    )

    # Create github_repos table
    op.create_table(
        "github_repos",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("track_issues", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("track_prs", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "track_comments", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "track_project_fields", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "labels_filter", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("state_filter", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("check_interval", sa.Integer(), server_default="60", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "full_sync_interval", sa.Integer(), server_default="1440", nullable=False
        ),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
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
            ["account_id"], ["github_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "owner", "name", name="unique_repo_per_account"
        ),
    )
    op.create_index(
        "github_repos_active_idx", "github_repos", ["active", "last_sync_at"]
    )
    op.create_index("github_repos_owner_name_idx", "github_repos", ["owner", "name"])

    # Add new columns to github_item table
    op.add_column(
        "github_item",
        sa.Column("github_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("content_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("repo_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("project_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("project_priority", sa.Text(), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("project_fields", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("assignees", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("milestone", sa.Text(), nullable=True),
    )
    op.add_column(
        "github_item",
        sa.Column("comment_count", sa.Integer(), nullable=True),
    )

    # Add foreign key and indexes for github_item
    op.create_foreign_key(
        "fk_github_item_repo",
        "github_item",
        "github_repos",
        ["repo_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("gh_github_updated_at_idx", "github_item", ["github_updated_at"])
    op.create_index("gh_repo_id_idx", "github_item", ["repo_id"])


def downgrade() -> None:
    # Drop indexes and foreign key from github_item
    op.drop_index("gh_repo_id_idx", table_name="github_item")
    op.drop_index("gh_github_updated_at_idx", table_name="github_item")
    op.drop_constraint("fk_github_item_repo", "github_item", type_="foreignkey")

    # Drop new columns from github_item
    op.drop_column("github_item", "comment_count")
    op.drop_column("github_item", "milestone")
    op.drop_column("github_item", "assignees")
    op.drop_column("github_item", "project_fields")
    op.drop_column("github_item", "project_priority")
    op.drop_column("github_item", "project_status")
    op.drop_column("github_item", "repo_id")
    op.drop_column("github_item", "content_hash")
    op.drop_column("github_item", "github_updated_at")

    # Drop github_repos table
    op.drop_index("github_repos_owner_name_idx", table_name="github_repos")
    op.drop_index("github_repos_active_idx", table_name="github_repos")
    op.drop_table("github_repos")

    # Drop github_accounts table
    op.drop_index("github_accounts_active_idx", table_name="github_accounts")
    op.drop_table("github_accounts")
