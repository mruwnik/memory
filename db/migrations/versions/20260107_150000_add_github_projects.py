"""Add github_projects table.

Revision ID: 20260107_150000
Revises: 20260107_120000
Create Date: 2026-01-07

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260107_150000"
down_revision = "20260107_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create github_projects table
    op.create_table(
        "github_projects",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("owner_type", sa.Text(), nullable=False),
        sa.Column("owner_login", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("short_description", sa.Text(), nullable=True),
        sa.Column("readme", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("public", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("closed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("fields", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("items_total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("github_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("github_updated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["account_id"], ["github_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "owner_login", "number", name="unique_project_per_account"
        ),
    )
    op.create_index(
        "github_projects_owner_idx", "github_projects", ["owner_login", "number"]
    )
    op.create_index("github_projects_title_idx", "github_projects", ["title"])


def downgrade() -> None:
    op.drop_index("github_projects_title_idx", table_name="github_projects")
    op.drop_index("github_projects_owner_idx", table_name="github_projects")
    op.drop_table("github_projects")
