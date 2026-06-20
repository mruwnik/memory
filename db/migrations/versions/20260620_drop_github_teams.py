"""drop unused github_teams table

The github_teams table was designed as a local mirror of GitHub org teams but
never got a writer — nothing in the codebase populates it, and its only reader
(an account-to-org affinity lookup in get_github_client_for_org) has been
removed. Dropping the dead table. github_users is intentionally kept: it has
live readers and is populated on demand.

Downgrade recreates the (empty) table and its indexes.

Revision ID: 20260620_drop_github_teams
Revises: 20260605_hidden_sensitivity
Create Date: 2026-06-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260620_drop_github_teams"
down_revision: Union[str, None] = "20260605_hidden_sensitivity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("github_teams")


def downgrade() -> None:
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
        sa.Column(
            "members_count", sa.Integer(), server_default="0", nullable=False
        ),
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
