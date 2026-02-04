"""Rename scheduled_llm_calls to scheduled_tasks and add task_executions table.

Revision ID: 20260204_scheduled_tasks
Revises: 20260204_env_clone_source
Create Date: 2026-02-04
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260204_scheduled_tasks"
down_revision = "20260204_env_clone_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create task_executions table first (no FK yet)
    op.create_table(
        "task_executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("scheduled_time", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(), nullable=True),
        sa.Column("data", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Rename scheduled_llm_calls table to scheduled_tasks
    op.rename_table("scheduled_llm_calls", "scheduled_tasks")

    # 3. Add new columns to scheduled_tasks
    op.add_column("scheduled_tasks", sa.Column("task_type", sa.String(20), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("cron_expression", sa.String(100), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("enabled", sa.Boolean(), nullable=True, server_default="true"))
    op.add_column("scheduled_tasks", sa.Column("updated_at", sa.DateTime(), nullable=True))

    # 4. Rename columns and adjust nullability
    op.alter_column("scheduled_tasks", "scheduled_time", new_column_name="next_scheduled_time")
    op.alter_column("scheduled_tasks", "next_scheduled_time", nullable=True)  # Allow NULL for completed tasks
    op.alter_column("scheduled_tasks", "message", nullable=True)  # Allow NULL for non-notification tasks
    op.alter_column("scheduled_tasks", "channel_type", new_column_name="notification_channel")
    op.alter_column("scheduled_tasks", "channel_identifier", new_column_name="notification_target")

    # 5. Migrate existing data
    op.execute("UPDATE scheduled_tasks SET task_type = 'notification' WHERE task_type IS NULL")
    op.execute("UPDATE scheduled_tasks SET enabled = (status != 'cancelled') WHERE enabled IS NULL")

    # 6. Create TaskExecution rows from existing execution data
    # Use uuid-ossp extension for UUID generation (works on all PostgreSQL versions)
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("""
        INSERT INTO task_executions (id, task_id, scheduled_time, started_at, finished_at, status, response, error_message, celery_task_id)
        SELECT
            uuid_generate_v4()::text,
            id,
            next_scheduled_time,
            executed_at,
            executed_at,
            status,
            response,
            error_message,
            celery_task_id
        FROM scheduled_tasks
        WHERE status != 'pending' OR executed_at IS NOT NULL
    """)

    # 7. Make task_type not nullable
    op.alter_column("scheduled_tasks", "task_type", nullable=False)
    op.alter_column("scheduled_tasks", "enabled", nullable=False, server_default=None)

    # 8. Drop old columns from scheduled_tasks
    op.drop_column("scheduled_tasks", "status")
    op.drop_column("scheduled_tasks", "response")
    op.drop_column("scheduled_tasks", "error_message")
    op.drop_column("scheduled_tasks", "executed_at")
    op.drop_column("scheduled_tasks", "celery_task_id")
    op.drop_column("scheduled_tasks", "model")
    op.drop_column("scheduled_tasks", "system_prompt")
    op.drop_column("scheduled_tasks", "allowed_tools")

    # 9. Add FK constraint to task_executions
    op.create_foreign_key(
        "fk_task_executions_task_id",
        "task_executions",
        "scheduled_tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 10. Create indexes
    op.create_index(
        "ix_scheduled_tasks_next_time_enabled",
        "scheduled_tasks",
        ["next_scheduled_time"],
        postgresql_where=sa.text("enabled = true AND next_scheduled_time IS NOT NULL"),
    )
    op.create_index("ix_scheduled_tasks_user_id", "scheduled_tasks", ["user_id"])
    op.create_index(
        "ix_task_executions_task_id_scheduled_time",
        "task_executions",
        ["task_id", "scheduled_time"],
    )
    op.create_index(
        "ix_task_executions_status_active",
        "task_executions",
        ["status"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index(
        "ix_task_executions_celery_task_id",
        "task_executions",
        ["celery_task_id"],
        postgresql_where=sa.text("celery_task_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_task_executions_celery_task_id", table_name="task_executions")
    op.drop_index("ix_task_executions_status_active", table_name="task_executions")
    op.drop_index("ix_task_executions_task_id_scheduled_time", table_name="task_executions")
    op.drop_index("ix_scheduled_tasks_user_id", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_next_time_enabled", table_name="scheduled_tasks")

    # Drop FK
    op.drop_constraint("fk_task_executions_task_id", "task_executions", type_="foreignkey")

    # Add back old columns
    op.add_column("scheduled_tasks", sa.Column("status", sa.String(), nullable=True, server_default="pending"))
    op.add_column("scheduled_tasks", sa.Column("response", sa.Text(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("executed_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("celery_task_id", sa.String(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("model", sa.String(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("system_prompt", sa.Text(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("allowed_tools", postgresql.JSON(), nullable=True))

    # Migrate data back from task_executions
    op.execute("""
        UPDATE scheduled_tasks st
        SET
            status = te.status,
            response = te.response,
            error_message = te.error_message,
            executed_at = te.finished_at,
            celery_task_id = te.celery_task_id
        FROM (
            SELECT DISTINCT ON (task_id) *
            FROM task_executions
            ORDER BY task_id, scheduled_time DESC
        ) te
        WHERE st.id = te.task_id
    """)

    op.execute("UPDATE scheduled_tasks SET status = 'pending' WHERE status IS NULL")

    # Ensure all rows have a next_scheduled_time before making it NOT NULL
    # Tasks that were completed one-time tasks won't have a next_scheduled_time, use NOW() as fallback
    op.execute("UPDATE scheduled_tasks SET next_scheduled_time = NOW() WHERE next_scheduled_time IS NULL")

    # Rename columns back and restore nullability
    # Note: message was nullable in old schema as well (some scheduled tasks didn't have messages)
    # op.alter_column("scheduled_tasks", "message", nullable=False)  # Don't restore - was actually nullable
    op.alter_column("scheduled_tasks", "next_scheduled_time", nullable=False)  # Restore NOT NULL
    op.alter_column("scheduled_tasks", "next_scheduled_time", new_column_name="scheduled_time")
    op.alter_column("scheduled_tasks", "notification_channel", new_column_name="channel_type")
    op.alter_column("scheduled_tasks", "notification_target", new_column_name="channel_identifier")

    # Drop new columns
    op.drop_column("scheduled_tasks", "task_type")
    op.drop_column("scheduled_tasks", "cron_expression")
    op.drop_column("scheduled_tasks", "enabled")
    op.drop_column("scheduled_tasks", "updated_at")

    # Rename table back
    op.rename_table("scheduled_tasks", "scheduled_llm_calls")

    # Drop task_executions table
    op.drop_table("task_executions")
