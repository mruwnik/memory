"""Add deleted_at column to slack_message for soft-delete support.

Slack delivers `message_deleted` events when a user deletes a message; we
soft-delete by stamping `deleted_at` so audit history is preserved while
search results exclude the row. A partial index on rows where
`deleted_at IS NOT NULL` keeps the supporting NOT EXISTS lookup cheap
without adding overhead for the typical case (non-deleted rows).

Revision ID: 20260506_slack_message_deleted_at
Revises: 20260506_slack_apps
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260506_slack_message_deleted_at"
down_revision: Union[str, None] = "20260506_slack_apps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "slack_message",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "slack_message_deleted_at_idx",
        "slack_message",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("slack_message_deleted_at_idx", table_name="slack_message")
    op.drop_column("slack_message", "deleted_at")
