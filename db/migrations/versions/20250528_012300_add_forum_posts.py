"""Add forum posts

Revision ID: 2524646f56f6
Revises: 1b535e1b044e
Create Date: 2025-05-28 01:23:00.079366

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2524646f56f6"
down_revision: Union[str, None] = "1b535e1b044e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "forum_post",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("authors", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slug", sa.Text(), nullable=True),
        sa.Column("karma", sa.Integer(), nullable=True),
        sa.Column("votes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("words", sa.Integer(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("images", sa.ARRAY(sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("forum_post_slug_idx", "forum_post", ["slug"], unique=False)
    op.create_index("forum_post_title_idx", "forum_post", ["title"], unique=False)
    op.create_index("forum_post_url_idx", "forum_post", ["url"], unique=False)


def downgrade() -> None:
    op.drop_index("forum_post_url_idx", table_name="forum_post")
    op.drop_index("forum_post_title_idx", table_name="forum_post")
    op.drop_index("forum_post_slug_idx", table_name="forum_post")
    op.drop_table("forum_post")
