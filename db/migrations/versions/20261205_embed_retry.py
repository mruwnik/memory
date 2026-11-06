"""Failed embeds count their attempts, schedule their next retry and keep their error type.

Revision ID: 20261205_embed_retry
Revises: 20261205_meeting_backfill
Create Date: 2026-11-06
"""

from alembic import op

revision = "20261205_embed_retry"
down_revision = "20261205_meeting_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Metadata-only on Postgres 11+ (constant default), so fast on a large source_item.
    op.execute(
        "ALTER TABLE source_item "
        "ADD COLUMN IF NOT EXISTS embed_attempts INTEGER NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS embed_error TEXT, "
        "ADD COLUMN IF NOT EXISTS embed_retry_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE source_item "
        "DROP COLUMN IF EXISTS embed_retry_at, "
        "DROP COLUMN IF EXISTS embed_error, "
        "DROP COLUMN IF EXISTS embed_attempts"
    )
