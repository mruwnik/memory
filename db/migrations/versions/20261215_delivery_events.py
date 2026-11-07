"""Deliveries name the event they were routed from, so the webhook log can show
what each delivery triggered. Deliveries routed before this have none. A
subscription's latest delivery is read by (trigger_id, created_at).

The indexes are built concurrently, so a large agent_trigger_matches isn't
locked against routing while they build. IF NOT EXISTS lets a run interrupted
mid-build be repeated (drop an index left INVALID first).

Revision ID: 20261215_delivery_events
Revises: 20261214_subscription_server
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261215_delivery_events"
down_revision = "20261214_subscription_server"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_trigger_matches ADD COLUMN IF NOT EXISTS event_id BIGINT")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS agent_trigger_matches_event_idx "
            "ON agent_trigger_matches (event_id) WHERE event_id IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS agent_trigger_matches_trigger_created_idx "
            "ON agent_trigger_matches (trigger_id, created_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS agent_trigger_matches_trigger_created_idx")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS agent_trigger_matches_event_idx")
    op.execute("ALTER TABLE agent_trigger_matches DROP COLUMN IF EXISTS event_id")
