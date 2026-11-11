"""Store each Slack workspace's owner.

The owner was the user of the workspace's oldest remaining credential, so it
passed to the next member whenever the owner disconnected or was deleted. It is
now a column, filled here from that same oldest credential.

Revision ID: 20261226_slack_workspace_owner
Revises: 20261225_null_gdoc_filename
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261226_slack_workspace_owner"
down_revision: Union[str, None] = "20261225_null_gdoc_filename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("slack_workspaces", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_slack_workspaces_owner_id",
        "slack_workspaces",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE slack_workspaces w SET owner_id = (
            SELECT c.user_id FROM slack_user_credentials c
            WHERE c.workspace_id = w.id
            ORDER BY c.id
            LIMIT 1
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_slack_workspaces_owner_id", "slack_workspaces", type_="foreignkey")
    op.drop_column("slack_workspaces", "owner_id")
