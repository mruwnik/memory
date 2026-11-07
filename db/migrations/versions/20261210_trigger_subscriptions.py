"""Triggers get an owner, a name, a handler and shaping; runs link to their trigger,
which dedupes and rate-limits them.

Revision ID: 20261210_trigger_subscriptions
Revises: 20261209_github_pr_branches
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261210_trigger_subscriptions"
down_revision = "20261209_github_pr_branches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_triggers "
        "ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, "
        "ADD COLUMN name TEXT, "
        "ADD COLUMN handler JSONB, "
        "ADD COLUMN coalesce_mode TEXT NOT NULL DEFAULT 'none', "
        "ADD COLUMN order_by TEXT NOT NULL DEFAULT 'none', "
        "ADD COLUMN max_per_hour INTEGER, "
        "ADD COLUMN backlog_max INTEGER, "
        "ALTER COLUMN definition_id DROP NOT NULL"
    )
    op.execute(
        "UPDATE agent_triggers t SET user_id = d.user_id, name = 'trigger-' || t.id, "
        "handler = jsonb_build_object('agent', t.definition_id) "
        "FROM agent_definitions d WHERE d.id = t.definition_id"
    )
    op.execute(
        "ALTER TABLE agent_triggers "
        "ALTER COLUMN user_id SET NOT NULL, "
        "ALTER COLUMN name SET NOT NULL, "
        "ALTER COLUMN handler SET NOT NULL, "
        "ADD CONSTRAINT agent_triggers_user_name_key UNIQUE (user_id, name), "
        "ADD CONSTRAINT agent_triggers_handler_check CHECK ("
        "CASE WHEN definition_id IS NOT NULL "
        "THEN handler = jsonb_build_object('agent', definition_id) "
        "ELSE ((jsonb_typeof(handler->'agent') = 'string' AND handler->>'agent' <> '' "
        "AND handler - 'agent' = '{}'::jsonb) "
        "OR (jsonb_typeof(handler->'queue') = 'string' AND handler->>'queue' <> '' "
        "AND handler - 'queue' = '{}'::jsonb)) IS TRUE END), "
        "ADD CONSTRAINT agent_triggers_coalesce_mode_check "
        "CHECK (coalesce_mode IN ('none', 'latest')), "
        "ADD CONSTRAINT agent_triggers_order_by_check "
        "CHECK (order_by IN ('none', 'item', 'thread')), "
        "ADD CONSTRAINT agent_triggers_max_per_hour_check CHECK (max_per_hour > 0), "
        "ADD CONSTRAINT agent_triggers_backlog_max_check CHECK (backlog_max > 0)"
    )

    op.execute(
        "ALTER TABLE agent_runs ADD COLUMN trigger_id INTEGER "
        "REFERENCES agent_triggers(id) ON DELETE SET NULL"
    )
    # A trigger moved to another definition may have redelivered under its old
    # dedupe key: only the first such run joins the trigger's unique dedupe index.
    op.execute(
        "UPDATE agent_runs r SET trigger_id = t.id FROM agent_triggers t "
        "WHERE r.trigger_kind = 'ingest' AND r.trigger_ref->>'trigger_id' = t.id::text "
        "AND (r.dedupe_key IS NULL OR r.id IN ("
        "SELECT DISTINCT ON (trigger_ref->>'trigger_id', dedupe_key) id FROM agent_runs "
        "WHERE trigger_kind = 'ingest' "
        "ORDER BY trigger_ref->>'trigger_id', dedupe_key, created_at, id))"
    )
    op.execute("DROP INDEX agent_runs_definition_dedupe_key")
    op.execute(
        "CREATE UNIQUE INDEX agent_runs_definition_dedupe_key ON agent_runs "
        "(definition_id, dedupe_key) WHERE dedupe_key IS NOT NULL AND trigger_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX agent_runs_trigger_dedupe_key ON agent_runs "
        "(trigger_id, dedupe_key) WHERE dedupe_key IS NOT NULL AND trigger_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX agent_runs_trigger_created_idx ON agent_runs (trigger_id, created_at) "
        "WHERE trigger_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX agent_runs_trigger_created_idx")
    op.execute("DROP INDEX agent_runs_trigger_dedupe_key")
    op.execute("DROP INDEX agent_runs_definition_dedupe_key")
    op.execute("ALTER TABLE agent_runs DROP COLUMN trigger_id")
    op.execute(
        "CREATE UNIQUE INDEX agent_runs_definition_dedupe_key ON agent_runs "
        "(definition_id, dedupe_key) WHERE dedupe_key IS NOT NULL"
    )
    # Triggers without a definition (queue or code-agent handlers) can't be kept.
    op.execute("DELETE FROM agent_triggers WHERE definition_id IS NULL")
    op.execute(
        "ALTER TABLE agent_triggers "
        "ALTER COLUMN definition_id SET NOT NULL, "
        "DROP COLUMN backlog_max, "
        "DROP COLUMN max_per_hour, "
        "DROP COLUMN order_by, "
        "DROP COLUMN coalesce_mode, "
        "DROP COLUMN handler, "
        "DROP COLUMN name, "
        "DROP COLUMN user_id"
    )
