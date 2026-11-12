"""Record the identities users proved: which User owns a Discord account, the
GitHub user id a PAT belongs to, and whether Google verified an account's email.

Revision ID: 20270101_discord_user_verified
Revises: 20261231_slack_channel_members
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20270101_discord_user_verified"
down_revision: Union[str, None] = "20261231_slack_channel_members"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_users",
        sa.Column(
            "verified_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("discord_users_verified_user_idx", "discord_users", ["verified_user_id"])
    op.add_column("github_accounts", sa.Column("verified_github_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "google_accounts",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("google_accounts", "email_verified")
    op.drop_column("github_accounts", "verified_github_id")
    op.drop_index("discord_users_verified_user_idx", table_name="discord_users")
    op.drop_column("discord_users", "verified_user_id")
