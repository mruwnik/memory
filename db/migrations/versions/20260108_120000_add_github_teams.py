"""Add github_teams table.

Revision ID: 20260108_120000
Revises: 20260107_180000
Create Date: 2026-01-08

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260108_120000"
down_revision = "20260107_180000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_teams",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("privacy", sa.Text(), nullable=False),
        sa.Column("permission", sa.Text(), nullable=True),
        sa.Column("org_login", sa.Text(), nullable=False),
        sa.Column("parent_team_id", sa.BigInteger(), nullable=True),
        sa.Column("members_count", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["parent_team_id"], ["github_teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "org_login", "slug", name="unique_team_per_account"
        ),
    )
    op.create_index("github_teams_org_idx", "github_teams", ["org_login"])
    op.create_index("github_teams_slug_idx", "github_teams", ["slug"])


def downgrade() -> None:
    op.drop_index("github_teams_slug_idx", table_name="github_teams")
    op.drop_index("github_teams_org_idx", table_name="github_teams")
    op.drop_table("github_teams")
