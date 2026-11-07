"""Push channels record when polling found something they never delivered.

Revision ID: 20261208_push_missed
Revises: 20261207_github_pr_issue_types
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261208_push_missed"
down_revision = "20261207_github_pr_issue_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("github_repos", "slack_apps"):
        op.execute(
            f"ALTER TABLE {table} "
            "ADD COLUMN IF NOT EXISTS push_missing_since TIMESTAMP WITH TIME ZONE, "
            "ADD COLUMN IF NOT EXISTS push_last_miss_at TIMESTAMP WITH TIME ZONE"
        )
    op.execute(
        "ALTER TABLE slack_channels ADD COLUMN IF NOT EXISTS push_since TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE slack_channels DROP COLUMN IF EXISTS push_since")
    for table in ("github_repos", "slack_apps"):
        op.execute(
            f"ALTER TABLE {table} "
            "DROP COLUMN IF EXISTS push_last_miss_at, "
            "DROP COLUMN IF EXISTS push_missing_since"
        )
