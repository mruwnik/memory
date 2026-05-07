"""Make claude_config_snapshots dedup per-user instead of global.

A globally-unique content_hash means user A uploading bytes that match
user B's existing snapshot would either fail or (worse, before this
change) silently see user B's metadata returned (CWE-200 / IDOR-lite:
leaks claude_account_email, summary, filename, etc.).

Switch to a per-user uniqueness constraint so each user has their own
row and metadata, while still preventing accidental duplicate uploads
within a single user's snapshot list.

Revision ID: 20260507_snapshot_user_dedup
Revises: 20260506_transcript_accounts
Create Date: 2026-05-07
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260507_snapshot_user_dedup"
down_revision: Union[str, None] = "20260506_transcript_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the two existing global-unique constraints on content_hash.
    # The original migration added both an auto-named one (column-level
    # `unique=True`) and an explicitly-named `unique_snapshot_hash`.
    op.execute(
        "ALTER TABLE claude_config_snapshots "
        "DROP CONSTRAINT IF EXISTS unique_snapshot_hash"
    )
    op.execute(
        "ALTER TABLE claude_config_snapshots "
        "DROP CONSTRAINT IF EXISTS claude_config_snapshots_content_hash_key"
    )

    # Per-user uniqueness: same user can't upload the same bytes twice,
    # but two different users can each have a row for the same hash.
    op.create_unique_constraint(
        "unique_snapshot_user_hash",
        "claude_config_snapshots",
        ["user_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "unique_snapshot_user_hash",
        "claude_config_snapshots",
        type_="unique",
    )
    # Restore global uniqueness. May fail if duplicate (user_id, content_hash)
    # rows now exist across users — operator must resolve before downgrading.
    op.create_unique_constraint(
        "unique_snapshot_hash",
        "claude_config_snapshots",
        ["content_hash"],
    )
