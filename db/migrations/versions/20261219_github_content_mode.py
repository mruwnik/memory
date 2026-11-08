"""GitHub repos choose how much of each issue and PR they store: everything
("full"), or only what change detection needs ("metadata"). GitHub items record
a hash of their body, which detects edits without the body itself.

Revision ID: 20261219_github_content_mode
Revises: 20261218_disown_dm_messages
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "20261219_github_content_mode"
down_revision = "20261218_disown_dm_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "github_repos",
        sa.Column("content_mode", sa.Text(), nullable=False, server_default="full"),
    )
    op.create_check_constraint(
        "github_repos_content_mode_check",
        "github_repos",
        "content_mode IN ('full', 'metadata')",
    )
    op.add_column("github_item", sa.Column("body_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("github_item", "body_hash")
    op.drop_constraint("github_repos_content_mode_check", "github_repos", type_="check")
    op.drop_column("github_repos", "content_mode")
