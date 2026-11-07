"""Events record who made each change, so triggers can filter on it, and runs
record what their worker posted, so a trigger never matches its own posts.

Revision ID: 20261213_trigger_loop_filters
Revises: 20261213_queue_handlers
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261213_trigger_loop_filters"
down_revision = "20261213_queue_handlers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail rather than hang behind the router and workers holding these tables.
    op.execute("SET LOCAL lock_timeout = '30s'")
    op.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS actors JSONB")
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS posted JSONB")
    op.execute("SET LOCAL lock_timeout = 0")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS posted")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS actors")
