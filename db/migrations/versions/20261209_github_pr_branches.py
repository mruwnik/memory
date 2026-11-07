"""PRs record their base commit, head branch and head repo.

Revision ID: 20261209_github_pr_branches
Revises: 20261209_github_org_hooks
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261209_github_pr_branches"
down_revision = "20261209_github_org_hooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE github_item "
        "ADD COLUMN IF NOT EXISTS base_sha TEXT, "
        "ADD COLUMN IF NOT EXISTS head_ref TEXT, "
        "ADD COLUMN IF NOT EXISTS head_repo TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE github_item "
        "DROP COLUMN IF EXISTS head_repo, "
        "DROP COLUMN IF EXISTS head_ref, "
        "DROP COLUMN IF EXISTS base_sha"
    )
