"""Derive scheduled_tasks.enabled from next_scheduled_time

Revision ID: 20260529_st_enabled_derived
Revises: 20260516_gh_verified_login
Create Date: 2026-05-29

Drops the redundant ``enabled`` column. Before dropping, any task that was
explicitly disabled (enabled=false) has its next_scheduled_time cleared so the
derived ``enabled`` (next_scheduled_time IS NOT NULL) stays False for it. The
partial dispatch index is rebuilt without the enabled predicate.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260529_st_enabled_derived"
down_revision: Union[str, None] = "20260516_gh_verified_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Preserve "disabled" semantics: a disabled task must not have a pending run.
    op.execute(
        "UPDATE scheduled_tasks SET next_scheduled_time = NULL WHERE enabled = false"
    )
    # 2. Drop the partial index whose predicate references the column.
    op.drop_index("ix_scheduled_tasks_next_time_enabled", table_name="scheduled_tasks")
    # 3. Drop the column.
    op.drop_column("scheduled_tasks", "enabled")
    # 4. Recreate the dispatch index without the enabled predicate.
    op.create_index(
        "ix_scheduled_tasks_next_time_enabled",
        "scheduled_tasks",
        ["next_scheduled_time"],
        postgresql_where=sa.text("next_scheduled_time IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_tasks_next_time_enabled", table_name="scheduled_tasks")
    op.add_column(
        "scheduled_tasks",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Best-effort restore: tasks with a pending run are enabled.
    op.execute(
        "UPDATE scheduled_tasks SET enabled = (next_scheduled_time IS NOT NULL)"
    )
    op.alter_column("scheduled_tasks", "enabled", server_default=None)
    op.create_index(
        "ix_scheduled_tasks_next_time_enabled",
        "scheduled_tasks",
        ["next_scheduled_time"],
        postgresql_where=sa.text("enabled = true AND next_scheduled_time IS NOT NULL"),
    )
