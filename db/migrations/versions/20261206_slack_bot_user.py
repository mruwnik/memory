"""Slack credentials record their app's bot user.

Filled in when a user next connects Slack; until then, posts made with that
app's bot token can re-trigger the user's agents.

Revision ID: 20261206_slack_bot_user
Revises: 20261205_embed_retry
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "20261206_slack_bot_user"
down_revision = "20261205_embed_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("slack_user_credentials", sa.Column("bot_user_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("slack_user_credentials", "bot_user_id")
