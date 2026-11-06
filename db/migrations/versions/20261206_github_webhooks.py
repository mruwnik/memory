"""GitHub webhooks: per-account signing secret, per-repo last event time, PR head SHA and check summary.

Revision ID: 20261206_github_webhooks
Revises: 20261206_slack_bot_user
Create Date: 2026-11-06
"""

from alembic import op

revision = "20261206_github_webhooks"
down_revision = "20261206_slack_bot_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE github_accounts ADD COLUMN IF NOT EXISTS webhook_secret_encrypted BYTEA"
    )
    op.execute(
        "ALTER TABLE github_repos ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE github_item "
        "ADD COLUMN IF NOT EXISTS head_sha TEXT, "
        "ADD COLUMN IF NOT EXISTS checks JSONB"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS gh_head_sha_idx ON github_item (repo_id, head_sha) "
        "WHERE head_sha IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS gh_head_sha_idx")
    op.execute(
        "ALTER TABLE github_item DROP COLUMN IF EXISTS checks, DROP COLUMN IF EXISTS head_sha"
    )
    op.execute("ALTER TABLE github_repos DROP COLUMN IF EXISTS last_event_at")
    op.execute("ALTER TABLE github_accounts DROP COLUMN IF EXISTS webhook_secret_encrypted")
