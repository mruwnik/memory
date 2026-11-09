"""Index source_item by (inserted_at, id) and (type, inserted_at, id) for
newest-first item browsing, overall and per item type.

Built CONCURRENTLY, so the upgrade doesn't lock source_item for writes; on a
large instance it takes a while.

Revision ID: 20261222_item_inserted_idx
Revises: 20261221_agent_run_notify_seen
Create Date: 2026-11-09
"""

from alembic import op

revision = "20261222_item_inserted_idx"
down_revision = "20261221_agent_run_notify_seen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS source_inserted_at_idx "
            "ON source_item (inserted_at, id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS source_type_inserted_at_idx "
            "ON source_item (type, inserted_at, id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS source_type_inserted_at_idx")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS source_inserted_at_idx")
