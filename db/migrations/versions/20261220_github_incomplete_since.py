"""GitHub repos remember since when items may be stored without fields GitHub
withheld, so the first sync that gets every field re-fetches them.

Revision ID: 20261220_github_incomplete_since
Revises: 20261219_github_content_mode
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "20261220_github_incomplete_since"
down_revision = "20261219_github_content_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "github_repos",
        sa.Column("incomplete_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("github_repos", "incomplete_since")
