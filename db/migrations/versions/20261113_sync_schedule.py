"""One sync schedule shape for polled sources, and a per-feed ingest delay.

Beat used to fan out to every row on env-var intervals (EMAIL_SYNC_INTERVAL,
ARTICLE_FEED_SYNC_INTERVAL, GITHUB_SYNC_INTERVAL, GOOGLE_DRIVE_SYNC_INTERVAL,
CALENDAR_SYNC_INTERVAL; seconds), so a row synced at most once per env interval
whatever its own check_interval said. Each row's check_interval is raised to
that env interval, so an upgrade keeps an instance's schedule; email accounts,
which had no interval of their own, take it outright.
"""
import os

from alembic import op
import sqlalchemy as sa

revision = "20261113_sync_schedule"
down_revision = "20261112_forum_comments"
branch_labels = None
depends_on = None

POLLED = {
    # table: env var that set its beat interval, and its default in seconds
    "article_feeds": ("ARTICLE_FEED_SYNC_INTERVAL", 30 * 60),
    "email_accounts": ("EMAIL_SYNC_INTERVAL", 60 * 60),
    "github_repos": ("GITHUB_SYNC_INTERVAL", 60 * 60),
    "google_folders": ("GOOGLE_DRIVE_SYNC_INTERVAL", 60 * 60),
    "calendar_accounts": ("CALENDAR_SYNC_INTERVAL", 60 * 60),
}


def env_minutes(name: str, default: int) -> int:
    return max(int(os.getenv(name, default)) // 60, 1)


def upgrade():
    op.alter_column("article_feeds", "last_checked_at", new_column_name="last_sync_at")
    op.add_column(
        "article_feeds",
        sa.Column("ingest_delay", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.alter_column("calendar_accounts", "sync_past_days", new_column_name="days_back")
    op.add_column(
        "email_accounts",
        sa.Column("check_interval", sa.Integer(), nullable=False, server_default=sa.text("60")),
    )
    # The LessWrong sync listed posts up to 8 hours old.
    op.add_column(
        "forum_sync_configs",
        sa.Column("ingest_delay", sa.Integer(), nullable=False, server_default=sa.text("480")),
    )

    for table, (env_var, default) in POLLED.items():
        op.add_column(table, sa.Column("sync_queued_at", sa.DateTime(timezone=True), nullable=True))
        interval = "GREATEST(check_interval, :env)" if table != "email_accounts" else ":env"
        op.execute(
            sa.text(
                f"UPDATE {table} SET check_interval = {interval}, sync_queued_at = last_sync_at"
            ).bindparams(env=env_minutes(env_var, default))
        )
    op.add_column(
        "forum_sync_configs", sa.Column("sync_queued_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE forum_sync_configs SET sync_queued_at = last_sync_at")

    op.create_table(
        "delayed_feed_items",
        sa.Column("url", sa.Text(), primary_key=True),
        sa.Column(
            "feed_id",
            sa.BigInteger(),
            sa.ForeignKey("article_feeds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_delayed_feed_items_feed_id", "delayed_feed_items", ["feed_id"])


def downgrade():
    op.drop_index("ix_delayed_feed_items_feed_id", table_name="delayed_feed_items")
    op.drop_table("delayed_feed_items")
    op.drop_column("forum_sync_configs", "sync_queued_at")
    for table in POLLED:
        op.drop_column(table, "sync_queued_at")
    op.drop_column("forum_sync_configs", "ingest_delay")
    op.drop_column("email_accounts", "check_interval")
    op.alter_column("calendar_accounts", "days_back", new_column_name="sync_past_days")
    op.drop_column("article_feeds", "ingest_delay")
    op.alter_column("article_feeds", "last_sync_at", new_column_name="last_checked_at")
