"""GitHub repos record whether Memory could install its webhook on them.

Revision ID: 20261207_github_webhook_status
Revises: 20261206_github_webhooks
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261207_github_webhook_status"
down_revision = "20261206_github_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE github_repos "
        "ADD COLUMN IF NOT EXISTS webhook_status TEXT, "
        "ADD COLUMN IF NOT EXISTS webhook_checked_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE github_repos "
        "DROP COLUMN IF EXISTS webhook_checked_at, "
        "DROP COLUMN IF EXISTS webhook_status"
    )
