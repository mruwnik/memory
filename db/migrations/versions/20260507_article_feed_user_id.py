"""Add user_id ownership column to article_feeds.

Closes the cross-tenant IDOR where any authenticated user could
update / delete every other user's article feed (the table had no
ownership tracking at all).

Existing rows have NULL user_id and are admin-only via the API
ownership filter — secure default. Operators can attribute legacy
rows to a real user with a manual UPDATE if desired.

Revision ID: 20260507_article_feed_user_id
Revises: 20260507_snapshot_user_dedup
Create Date: 2026-05-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260507_article_feed_user_id"
down_revision: Union[str, None] = "20260507_snapshot_user_dedup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "article_feeds",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "article_feeds_user_idx", "article_feeds", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("article_feeds_user_idx", table_name="article_feeds")
    op.drop_column("article_feeds", "user_id")
