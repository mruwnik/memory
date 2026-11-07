"""An events outbox: ingest writes item events in the item's transaction and a
router hands them to triggers.

Revision ID: 20261211_events
Revises: 20261210_trigger_subscriptions
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261211_events"
down_revision = "20261210_trigger_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE events ("
        "id BIGSERIAL PRIMARY KEY, "
        "kind TEXT NOT NULL DEFAULT 'item', "
        "action TEXT, "
        "item_id BIGINT, "
        "changes TEXT[], "
        "backfill BOOLEAN NOT NULL DEFAULT false, "
        "attempts INTEGER NOT NULL DEFAULT 0, "
        "error TEXT, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "routed_at TIMESTAMPTZ, "
        "CONSTRAINT events_kind_check CHECK (kind IN ('item', 'raw', 'tick', 'timer')), "
        "CONSTRAINT events_item_check CHECK (kind <> 'item' OR (item_id IS NOT NULL "
        "AND action IN ('added', 'changed', 'removed'))))"
    )
    op.execute("CREATE INDEX events_pending_idx ON events (id) WHERE routed_at IS NULL")
    op.execute("CREATE INDEX events_routed_at_idx ON events (routed_at)")


def downgrade() -> None:
    op.execute("DROP TABLE events")
