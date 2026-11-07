"""Forget webhook statuses from when Memory set the GitHub App's own webhook.

Revision ID: 20261209_github_org_hooks
Revises: 20261208_push_missed
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261209_github_org_hooks"
down_revision = "20261208_push_missed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE github_repos SET webhook_status = NULL "
        "WHERE webhook_status IN ('app_hook', 'app_settings')"
    )


def downgrade() -> None:
    pass
