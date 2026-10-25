"""Summary requests carry the provider key that pays for them.

Requests queued on the server's key can't be submitted or collected without
it, so they are dropped; reingesting an item queues it again on its key.

Revision ID: 20261029_summary_request_keys
Revises: 20261029_source_llm_keys
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_summary_request_keys"
down_revision: Union[str, None] = "20261029_source_llm_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A request inserted between the delete and the NOT NULL column would fail the migration.
    # Fail rather than hang behind workers that were left running.
    op.execute("SET LOCAL lock_timeout = '30s'")
    op.execute("LOCK TABLE summary_request IN ACCESS EXCLUSIVE MODE")
    op.execute("SET LOCAL lock_timeout = 0")
    op.execute("DELETE FROM summary_request")
    op.add_column(
        "summary_request",
        sa.Column(
            "provider_key_id",
            sa.Integer(),
            sa.ForeignKey("llm_provider_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("summary_request_key_idx", "summary_request", ["provider_key_id"])


def downgrade() -> None:
    op.drop_index("summary_request_key_idx", table_name="summary_request")
    op.drop_column("summary_request", "provider_key_id")
