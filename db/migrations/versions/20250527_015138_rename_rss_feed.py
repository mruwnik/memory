"""Rename rss feed

Revision ID: 1b535e1b044e
Revises: d897c6353a84
Create Date: 2025-05-27 01:51:38.553777

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1b535e1b044e"
down_revision: Union[str, None] = "d897c6353a84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "article_feeds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "check_interval", sa.Integer(), server_default="3600", nullable=False
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index(
        "article_feeds_active_idx",
        "article_feeds",
        ["active", "last_checked_at"],
        unique=False,
    )
    op.create_index(
        "article_feeds_tags_idx",
        "article_feeds",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )
    op.drop_index("rss_feeds_active_idx", table_name="rss_feeds")
    op.drop_index("rss_feeds_tags_idx", table_name="rss_feeds", postgresql_using="gin")
    op.drop_table("rss_feeds")


def downgrade() -> None:
    op.create_table(
        "rss_feeds",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("url", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("title", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("description", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.TEXT()),
            server_default=sa.text("'{}'::text[]"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "last_checked_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "active",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="rss_feeds_pkey"),
        sa.UniqueConstraint("url", name="rss_feeds_url_key"),
    )
    op.create_index(
        "rss_feeds_tags_idx",
        "rss_feeds",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "rss_feeds_active_idx", "rss_feeds", ["active", "last_checked_at"], unique=False
    )
    op.drop_index(
        "article_feeds_tags_idx", table_name="article_feeds", postgresql_using="gin"
    )
    op.drop_index("article_feeds_active_idx", table_name="article_feeds")
    op.drop_table("article_feeds")
