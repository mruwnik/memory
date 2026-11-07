"""PRs record whether they are drafts and the head commit check rollup they last finished with.

GitHub's rollup state isn't stored, so for PRs whose stored checks have all
finished it is derived from them, so their next sync doesn't report
checks_complete for checks that finished before the upgrade.

Revision ID: 20261212_github_pr_draft
Revises: 20261211_events
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261212_github_pr_draft"
down_revision = "20261211_events"
branch_labels = None
depends_on = None

# Postgres doesn't promise to test jsonb_typeof before calling jsonb_each_text on a JSON null.
OUTCOMES = (
    "SELECT value FROM jsonb_each_text("
    "CASE WHEN jsonb_typeof(checks) = 'object' THEN checks ELSE '{}'::jsonb END)"
)
PENDING = "('queued', 'in_progress', 'waiting', 'requested', 'pending', 'expected')"
FAILED = "('failure', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale')"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE github_item "
        "ADD COLUMN IF NOT EXISTS draft BOOLEAN, "
        "ADD COLUMN IF NOT EXISTS checks_complete_key TEXT"
    )
    op.execute(
        f"""
        UPDATE github_item SET checks_complete_key = head_sha || ':' || CASE
            WHEN EXISTS ({OUTCOMES} WHERE value = 'error') THEN 'ERROR'
            WHEN EXISTS ({OUTCOMES} WHERE value IN {FAILED}) THEN 'FAILURE'
            ELSE 'SUCCESS' END
        WHERE head_sha IS NOT NULL
          AND EXISTS ({OUTCOMES})
          AND NOT EXISTS ({OUTCOMES} WHERE value IN {PENDING})
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE github_item "
        "DROP COLUMN IF EXISTS checks_complete_key, "
        "DROP COLUMN IF EXISTS draft"
    )
