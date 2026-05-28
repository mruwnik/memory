"""Index source_item.updated_at for the reconciliation sweep.

``reconcile_access_control``'s recent tier runs
``WHERE updated_at >= cutoff`` against ``source_item`` — the largest table
in the schema — every ``ACCESS_CONTROL_RECONCILE_INTERVAL``. Without an
index that is a full sequential scan each run; this adds the index so it
is a cheap range scan.

Revision ID: 20260516_source_updated_at_index
Revises: 20260515_ac_inherited_flags
Create Date: 2026-05-16
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260516_source_updated_at_index"
down_revision: Union[str, None] = "20260515_ac_inherited_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("source_updated_at_idx", "source_item", ["updated_at"])


def downgrade() -> None:
    op.drop_index("source_updated_at_idx", table_name="source_item")
