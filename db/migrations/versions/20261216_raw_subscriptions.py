"""Raw-event subscriptions and item removals: agent_triggers takes kind 'raw',
agent_trigger_matches no longer cascades from source_item, so a removed item's
delivery can outlive it, and raw subscription runs are indexed by the raw event
they deliver (built concurrently).

Revision ID: 20261216_raw_subscriptions
Revises: 20261215_delivery_events
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261216_raw_subscriptions"
down_revision = "20261215_delivery_events"
branch_labels = None
depends_on = None

# Looked up rather than named: older instances may name the constraint differently.
DROP_ITEM_FKEY = """
DO $$
DECLARE c text;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'agent_trigger_matches'::regclass
          AND confrelid = 'source_item'::regclass
          AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE agent_trigger_matches DROP CONSTRAINT %I', c);
    END LOOP;
END $$
"""


def upgrade() -> None:
    take_raw_triggers()
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS agent_runs_raw_event_idx "
            "ON agent_runs (((trigger_ref->>'event_id')::bigint)) WHERE trigger_kind = 'raw'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS agent_runs_raw_event_idx")
    refuse_raw_triggers()


def take_raw_triggers() -> None:
    """The upgrade's transactional part."""
    op.execute("ALTER TABLE agent_triggers DROP CONSTRAINT IF EXISTS agent_triggers_kind_check")
    op.execute(
        "ALTER TABLE agent_triggers ADD CONSTRAINT agent_triggers_kind_check "
        "CHECK (kind IN ('ingest', 'cron', 'raw'))"
    )
    op.execute(DROP_ITEM_FKEY)


def refuse_raw_triggers() -> None:
    """The downgrade's transactional part."""
    op.execute(
        "DELETE FROM agent_trigger_matches m "
        "WHERE NOT EXISTS (SELECT 1 FROM source_item s WHERE s.id = m.item_id)"
    )
    op.execute(
        "ALTER TABLE agent_trigger_matches ADD CONSTRAINT agent_trigger_matches_item_id_fkey "
        "FOREIGN KEY (item_id) REFERENCES source_item(id) ON DELETE CASCADE"
    )
    op.execute("DELETE FROM agent_triggers WHERE kind = 'raw'")
    op.execute("ALTER TABLE agent_triggers DROP CONSTRAINT IF EXISTS agent_triggers_kind_check")
    op.execute(
        "ALTER TABLE agent_triggers ADD CONSTRAINT agent_triggers_kind_check "
        "CHECK (kind IN ('ingest', 'cron'))"
    )
