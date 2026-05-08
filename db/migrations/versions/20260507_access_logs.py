"""Add access_logs table for audit logging.

The AccessLog model has existed for a while but no migration was created
to back it. Tests that exercise the model fail because the table does not
exist. Production code that calls log_access() also silently fails — these
log inserts have been raising IntegrityError in the background.

Revision ID: 20260507_access_logs
Revises: 20260506_slack_apps
Create Date: 2026-05-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260507_access_logs"
down_revision: Union[str, None] = "20260506_slack_apps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "access_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("item_id", sa.BigInteger(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_access_logs_user_time",
        "access_logs",
        ["user_id", "timestamp"],
    )
    op.create_index(
        "idx_access_logs_item",
        "access_logs",
        ["item_id"],
        postgresql_where=sa.text("item_id IS NOT NULL"),
    )
    op.create_index(
        "idx_access_logs_time",
        "access_logs",
        ["timestamp"],
    )


def downgrade() -> None:
    op.drop_index("idx_access_logs_time", table_name="access_logs")
    op.drop_index("idx_access_logs_item", table_name="access_logs")
    op.drop_index("idx_access_logs_user_time", table_name="access_logs")
    op.drop_table("access_logs")
