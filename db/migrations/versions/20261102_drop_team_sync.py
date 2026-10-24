"""Drop the Discord role and GitHub team links from teams.

Teams exist only in Memory; downgrade restores the columns empty.

Revision ID: 20261102_drop_team_sync
Revises: 20261101_claude_github_tokens
Create Date: 2026-11-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261102_drop_team_sync"
down_revision: Union[str, None] = "20261101_claude_github_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def columns() -> list[sa.Column]:
    return [
        sa.Column("discord_role_id", sa.BigInteger(), nullable=True),
        sa.Column("discord_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("auto_sync_discord", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("github_team_id", sa.BigInteger(), nullable=True),
        sa.Column("github_team_slug", sa.Text(), nullable=True),
        sa.Column("github_org", sa.Text(), nullable=True),
        sa.Column("auto_sync_github", sa.Boolean(), nullable=False, server_default="true"),
    ]


def upgrade() -> None:
    for column in columns():
        op.drop_column("teams", column.name)


def downgrade() -> None:
    for column in columns():
        op.add_column("teams", column)
