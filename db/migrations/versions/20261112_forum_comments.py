"""Forum comments and the forum sync config.

The LessWrong config row is seeded from the LESSWRONG_SYNC_INTERVAL env var it
replaces (seconds; 0 turned the sync off), so an upgrade keeps an instance's schedule.
"""
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261112_forum_comments"
down_revision = "20261111_system_default_keys"
branch_labels = None
depends_on = None


def setting(name: str, type_, default: str) -> sa.Column:
    return sa.Column(name, type_, nullable=False, server_default=sa.text(default))


def upgrade():
    op.add_column("forum_post", sa.Column("synced_comment_count", sa.Integer(), nullable=True))
    op.add_column(
        "forum_post", sa.Column("comments_checked_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "forum_comment",
        sa.Column("id", sa.BigInteger(), sa.ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("post_id", sa.BigInteger(), sa.ForeignKey("forum_post.id", ondelete="CASCADE"), nullable=False),
        sa.Column("comment_id", sa.Text(), nullable=False, unique=True),
        sa.Column("parent_comment_id", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("karma", sa.Integer(), nullable=True),
        sa.Column("votes", sa.Integer(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("forum_comment_post_idx", "forum_comment", ["post_id"])

    op.create_table(
        "forum_sync_configs",
        sa.Column("forum", sa.Text(), primary_key=True),
        setting("active", sa.Boolean(), "true"),
        setting("check_interval", sa.Integer(), "1440"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_result", postgresql.JSONB(), nullable=True),
        setting("days_back", sa.Integer(), "30"),
        setting("max_items", sa.Integer(), "1000"),
        setting("min_karma", sa.Integer(), "10"),
        setting("af", sa.Boolean(), "false"),
        setting("comments_enabled", sa.Boolean(), "true"),
        setting("min_comment_karma", sa.Integer(), "5"),
        setting("comment_recheck_divisor", sa.Integer(), "10"),
        setting("comment_recheck_max_days", sa.Integer(), "30"),
        setting("comment_max_age_days", sa.Integer(), "365"),
        setting("max_comment_fetches", sa.Integer(), "200"),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("sync_failing_since", sa.DateTime(timezone=True), nullable=True),
    )
    interval = int(os.getenv("LESSWRONG_SYNC_INTERVAL", 60 * 60 * 24))
    op.execute(
        sa.text(
            "INSERT INTO forum_sync_configs (forum, active, check_interval) "
            "VALUES ('lesswrong', :active, :check_interval)"
        ).bindparams(active=interval > 0, check_interval=max(interval // 60, 60) if interval > 0 else 60 * 24)
    )


def downgrade():
    op.drop_table("forum_sync_configs")
    op.execute(
        "DELETE FROM source_item WHERE id IN (SELECT id FROM forum_comment)"
    )
    op.drop_index("forum_comment_post_idx", table_name="forum_comment")
    op.drop_table("forum_comment")
    op.drop_column("forum_post", "comments_checked_at")
    op.drop_column("forum_post", "synced_comment_count")
