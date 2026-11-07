"""Queue handlers: runs a queue delivery was claimed into name their queue, and
deliveries carry the key their subscription orders and coalesces them by, and the
items coalescing folded into them.

Revision ID: 20261213_queue_handlers
Revises: 20261212_raw_events
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261213_queue_handlers"
down_revision = "20261212_raw_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_runs ADD COLUMN queue TEXT")
    op.execute(
        "ALTER TABLE agent_trigger_matches ADD COLUMN order_key TEXT, "
        "ADD COLUMN coalesced BIGINT[] NOT NULL DEFAULT '{}'"
    )
    op.execute(
        "CREATE INDEX agent_runs_queue_pending_idx ON agent_runs (user_id, queue, created_at) "
        "WHERE queue IS NOT NULL AND status = 'pending'"
    )
    op.execute(
        "CREATE INDEX agent_trigger_matches_order_idx ON agent_trigger_matches "
        "(trigger_id, order_key, created_at) WHERE order_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX agent_trigger_matches_order_idx")
    op.execute("DROP INDEX agent_runs_queue_pending_idx")
    op.execute("ALTER TABLE agent_trigger_matches DROP COLUMN order_key, DROP COLUMN coalesced")
    op.execute("ALTER TABLE agent_runs DROP COLUMN queue")
