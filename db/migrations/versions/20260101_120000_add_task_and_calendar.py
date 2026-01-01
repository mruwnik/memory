"""Add task, calendar_event, and calendar_accounts tables

Revision ID: g3b4c5d6e7f8
Revises: add_exclude_folder_ids
Create Date: 2026-01-01 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "g3b4c5d6e7f8"
down_revision: Union[str, None] = "add_exclude_folder_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create calendar_accounts table (source for syncing)
    op.create_table(
        "calendar_accounts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("calendar_type", sa.Text(), nullable=False),
        sa.Column("caldav_url", sa.Text(), nullable=True),
        sa.Column("caldav_username", sa.Text(), nullable=True),
        sa.Column("caldav_password", sa.Text(), nullable=True),
        sa.Column("google_account_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "calendar_ids",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("check_interval", sa.Integer(), server_default="15", nullable=False),
        sa.Column("sync_past_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("sync_future_days", sa.Integer(), server_default="90", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["google_account_id"], ["google_accounts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("calendar_type IN ('caldav', 'google')"),
    )
    op.create_index(
        "calendar_accounts_active_idx",
        "calendar_accounts",
        ["active", "last_sync_at"],
        unique=False,
    )
    op.create_index(
        "calendar_accounts_type_idx",
        "calendar_accounts",
        ["calendar_type"],
        unique=False,
    )

    # Create task table
    op.create_table(
        "task",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("task_title", sa.Text(), nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("recurrence", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_item_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_item_id"], ["source_item.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'done', 'cancelled')",
            name="task_status_check",
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('low', 'medium', 'high', 'urgent')",
            name="task_priority_check",
        ),
    )
    op.create_index("task_due_date_idx", "task", ["due_date"], unique=False)
    op.create_index("task_status_idx", "task", ["status"], unique=False)
    op.create_index("task_priority_idx", "task", ["priority"], unique=False)
    op.create_index("task_source_item_idx", "task", ["source_item_id"], unique=False)

    # Create calendar_event table
    op.create_table(
        "calendar_event",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("event_title", sa.Text(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column("calendar_account_id", sa.BigInteger(), nullable=True),
        sa.Column("calendar_name", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["calendar_account_id"], ["calendar_accounts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("calendar_event_start_idx", "calendar_event", ["start_time"], unique=False)
    op.create_index("calendar_event_end_idx", "calendar_event", ["end_time"], unique=False)
    op.create_index("calendar_event_account_idx", "calendar_event", ["calendar_account_id"], unique=False)
    op.create_index("calendar_event_calendar_idx", "calendar_event", ["calendar_name"], unique=False)
    op.create_index(
        "calendar_event_external_idx",
        "calendar_event",
        ["calendar_account_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop calendar_event
    op.drop_index("calendar_event_external_idx", table_name="calendar_event")
    op.drop_index("calendar_event_calendar_idx", table_name="calendar_event")
    op.drop_index("calendar_event_account_idx", table_name="calendar_event")
    op.drop_index("calendar_event_end_idx", table_name="calendar_event")
    op.drop_index("calendar_event_start_idx", table_name="calendar_event")
    op.drop_table("calendar_event")

    # Drop task
    op.drop_index("task_source_item_idx", table_name="task")
    op.drop_index("task_priority_idx", table_name="task")
    op.drop_index("task_status_idx", table_name="task")
    op.drop_index("task_due_date_idx", table_name="task")
    op.drop_table("task")

    # Drop calendar_accounts
    op.drop_index("calendar_accounts_type_idx", table_name="calendar_accounts")
    op.drop_index("calendar_accounts_active_idx", table_name="calendar_accounts")
    op.drop_table("calendar_accounts")
