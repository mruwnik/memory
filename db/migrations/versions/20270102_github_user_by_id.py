"""Record whether a GitHub account's Person link came from the account itself.

Ingest links a GitHub account either to the Person it made for that account or
to a Person whose contact_info lists the login. Only the first proves anything:
a login typed into contact_info is unverified, and logins are reused. Existing
links count as made for the account when the Person carries ingest's
``github:<login>`` identifier.

Revision ID: 20270102_github_user_by_id
Revises: 20270101_discord_user_verified
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20270102_github_user_by_id"
down_revision: Union[str, None] = "20270101_discord_user_verified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "github_users",
        sa.Column("linked_by_id", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE github_users AS gu SET linked_by_id = true
        FROM people AS p
        WHERE p.id = gu.person_id
          AND p.creator_id IS NULL
          AND p.identifier IN ('github:' || gu.username, 'github:' || gu.username || ':' || gu.id)
        """
    )


def downgrade() -> None:
    op.drop_column("github_users", "linked_by_id")
