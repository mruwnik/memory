"""Record which connected users' tokens list each non-public Slack channel.

The channel sync fills the table, so a member who isn't the workspace's owner
can see and switch off collection of their own DMs and private channels. It
starts empty and fills on each workspace's next sync.

Revision ID: 20261229_slack_channel_members
Revises: 20261226_recurrence_cost
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261229_slack_channel_members"
down_revision: Union[str, None] = "20261226_recurrence_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_channel_members",
        sa.Column(
            "channel_id",
            sa.Text(),
            sa.ForeignKey("slack_channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("slack_channel_members_user_idx", "slack_channel_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("slack_channel_members_user_idx", table_name="slack_channel_members")
    op.drop_table("slack_channel_members")
