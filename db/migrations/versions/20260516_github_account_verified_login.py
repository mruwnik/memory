"""Add verified_login column to github_accounts.

``GithubAccount.name`` is a self-attested display name supplied in the
request body. ``meta_get_user`` exposed it to downstream MCP consumers,
which let an attacker register their own valid PAT under someone else's
claimed identity. ``verified_login`` holds the login GitHub itself
reports for the stored credentials, so consumers have a field they can
trust for identity decisions. It is NULL until verification succeeds.

Revision ID: 20260516_github_account_verified_login
Revises: 20260516_source_updated_at_index
Create Date: 2026-05-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260516_github_account_verified_login"
down_revision: Union[str, None] = "20260516_source_updated_at_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "github_accounts",
        sa.Column("verified_login", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("github_accounts", "verified_login")
