"""Add cascade delete for user foreign keys.

Fixes issue #38: deleting users via API fails due to FK constraints.

This migration adds ondelete behavior to all tables referencing users.id:
- projects, sessions, telemetry_events, scheduled_llm_calls,
  claude_config_snapshots, claude_environments: CASCADE (delete with user)
- pending_jobs: SET NULL (preserve job history, clear user reference)

Revision ID: 20260122_120000
Revises: 20260121_120000
Create Date: 2026-01-22

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260122_120000"
down_revision = "20260121_120000"
branch_labels = None
depends_on = None


# Tables that should CASCADE delete when user is deleted
CASCADE_TABLES = [
    ("projects", "user_id", "projects_user_id_fkey"),
    ("sessions", "user_id", "sessions_user_id_fkey"),
    ("telemetry_events", "user_id", "telemetry_events_user_id_fkey"),
    ("scheduled_llm_calls", "user_id", "scheduled_llm_calls_user_id_fkey"),
    ("claude_config_snapshots", "user_id", "claude_config_snapshots_user_id_fkey"),
    ("claude_environments", "user_id", "claude_environments_user_id_fkey"),
]

# Tables that should SET NULL when user is deleted (preserve records)
SET_NULL_TABLES = [
    ("pending_jobs", "user_id", "pending_jobs_user_id_fkey"),
]


def upgrade() -> None:
    # Add CASCADE delete for user-owned tables
    for table, column, constraint in CASCADE_TABLES:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "users",
            [column],
            ["id"],
            ondelete="CASCADE",
        )

    # Add SET NULL for tables where we want to preserve records
    for table, column, constraint in SET_NULL_TABLES:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "users",
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Remove CASCADE/SET NULL - restore original NO ACTION behavior
    for table, column, constraint in CASCADE_TABLES + SET_NULL_TABLES:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "users",
            [column],
            ["id"],
        )
