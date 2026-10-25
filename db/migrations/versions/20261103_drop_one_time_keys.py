"""Delete one-time API keys; the key type no longer exists.

Downgrade cannot restore the deleted rows.

Revision ID: 20261103_drop_one_time_keys
Revises: 20261029_summary_request_keys
Create Date: 2026-11-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261103_drop_one_time_keys"
down_revision: Union[str, None] = "20261029_summary_request_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM api_keys WHERE key_type = 'one_time'")


def downgrade() -> None:
    pass
