"""Drop slack_app_users: only a Slack app's owner can use it.

Revision ID: 20261107_drop_slack_app_users
Revises: 20261107_drop_obs_tables
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261107_drop_slack_app_users"
down_revision: Union[str, None] = "20261107_drop_obs_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("slack_app_users")


def downgrade() -> None:
    op.create_table(
        "slack_app_users",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("slack_app_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slack_app_id"], ["slack_apps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "slack_app_id"),
    )
