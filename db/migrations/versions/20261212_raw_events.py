"""Raw events: every webhook delivery is an event row, deduped per source
account by its delivery id; item events link the raw event that caused them.

Revision ID: 20261212_raw_events
Revises: 20261212_trigger_deliveries
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261212_raw_events"
down_revision = "20261212_trigger_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE events "
        "ADD COLUMN source TEXT, "
        "ADD COLUMN type TEXT, "
        "ADD COLUMN account_id BIGINT, "
        "ADD COLUMN container TEXT, "
        "ADD COLUMN delivery_id TEXT, "
        "ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE, "
        "ADD COLUMN outcome TEXT, "
        "ADD COLUMN payload JSONB, "
        "ADD COLUMN caused_by BIGINT, "
        "ADD CONSTRAINT events_outcome_check CHECK "
        "(outcome IN ('stored', 'not_collected', 'no_item_type', 'rejected')), "
        "ADD CONSTRAINT events_raw_check CHECK (kind <> 'raw' OR (source IS NOT NULL "
        "AND type IS NOT NULL AND account_id IS NOT NULL AND delivery_id IS NOT NULL "
        "AND outcome IS NOT NULL))"
    )
    op.execute("CREATE UNIQUE INDEX events_delivery_idx ON events (source, account_id, delivery_id) WHERE kind = 'raw'")
    op.execute("CREATE INDEX events_account_idx ON events (source, account_id, id) WHERE kind = 'raw'")
    op.execute("CREATE INDEX events_caused_by_idx ON events (caused_by) WHERE caused_by IS NOT NULL")
    op.execute("CREATE INDEX events_payload_age_idx ON events (created_at) WHERE payload IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX events_payload_age_idx")
    op.execute("DROP INDEX events_caused_by_idx")
    op.execute("DROP INDEX events_account_idx")
    op.execute("DROP INDEX events_delivery_idx")
    op.execute(
        "ALTER TABLE events "
        "DROP CONSTRAINT events_raw_check, "
        "DROP CONSTRAINT events_outcome_check, "
        "DROP COLUMN caused_by, "
        "DROP COLUMN payload, "
        "DROP COLUMN outcome, "
        "DROP COLUMN owner_id, "
        "DROP COLUMN delivery_id, "
        "DROP COLUMN container, "
        "DROP COLUMN account_id, "
        "DROP COLUMN type, "
        "DROP COLUMN source"
    )
