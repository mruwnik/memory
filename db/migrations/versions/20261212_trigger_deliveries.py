"""A trigger's match is its delivery: pending until it ends done, failed, expired
or skipped, with the deferral, dispatch and run it went to. Batches wait here
instead of in Redis.

Matches that already exist were delivered, except per-item ones made in the last
hour, which go back to pending: a fire still in the broker at deploy finds them,
and the sweep resends the rest, whose run dedupe returns any run already started.
Such a match is an edit when the trigger matched the item before; the changes its
ingester reported weren't kept, so it says "edited" or "new".

Revision ID: 20261212_trigger_deliveries
Revises: 20261212_github_pr_draft
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261212_trigger_deliveries"
down_revision = "20261212_github_pr_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_trigger_matches "
        "ADD COLUMN status TEXT NOT NULL DEFAULT 'done', "
        "ADD COLUMN edited BOOLEAN NOT NULL DEFAULT false, "
        "ADD COLUMN changes TEXT[] NOT NULL DEFAULT '{}', "
        "ADD COLUMN not_before TIMESTAMPTZ, "
        "ADD COLUMN dispatched_at TIMESTAMPTZ, "
        "ADD COLUMN sends INTEGER NOT NULL DEFAULT 0, "
        "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0, "
        "ADD COLUMN error TEXT, "
        "ADD COLUMN run_id VARCHAR REFERENCES agent_runs(id) ON DELETE SET NULL, "
        "ADD CONSTRAINT agent_trigger_matches_status_check CHECK "
        "(status IN ('pending', 'done', 'failed', 'expired') OR status LIKE 'skipped:%')"
    )
    op.execute("ALTER TABLE agent_trigger_matches ALTER COLUMN status SET DEFAULT 'pending'")
    earlier = (
        "EXISTS (SELECT 1 FROM agent_trigger_matches o WHERE o.trigger_id = m.trigger_id "
        "AND o.item_id = m.item_id AND o.created_at < m.created_at)"
    )
    op.execute(
        "UPDATE agent_trigger_matches m SET status = 'pending', dispatched_at = m.created_at, "
        f"sends = 1, edited = {earlier}, "
        f"changes = CASE WHEN {earlier} THEN '{{edited}}'::text[] ELSE '{{new}}'::text[] END "
        "FROM agent_triggers t WHERE t.id = m.trigger_id "
        "AND m.created_at > now() - interval '1 hour' "
        "AND t.config->'delivery'->>'mode' IS DISTINCT FROM 'batch'"
    )
    op.execute(
        "CREATE INDEX agent_trigger_matches_pending_idx ON agent_trigger_matches "
        "(trigger_id, created_at) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX agent_trigger_matches_run_idx ON agent_trigger_matches (run_id) "
        "WHERE run_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX agent_trigger_matches_run_idx")
    op.execute("DROP INDEX agent_trigger_matches_pending_idx")
    op.execute(
        "ALTER TABLE agent_trigger_matches "
        "DROP CONSTRAINT agent_trigger_matches_status_check, "
        "DROP COLUMN status, DROP COLUMN edited, DROP COLUMN changes, "
        "DROP COLUMN not_before, DROP COLUMN dispatched_at, DROP COLUMN sends, "
        "DROP COLUMN attempts, "
        "DROP COLUMN error, DROP COLUMN run_id"
    )
