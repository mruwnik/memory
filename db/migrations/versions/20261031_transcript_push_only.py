"""Allow push-only transcript accounts, which have no provider API key.

Also clears stored webhook secrets: they were free text that nothing read,
and they now authenticate pushes, so an old weak value must not go live.

Revision ID: 20261031_transcript_push_only
Revises: 20261030_sync_failing_since
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261031_transcript_push_only"
down_revision: Union[str, None] = "20261030_sync_failing_since"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "transcript_accounts", "api_key_encrypted", existing_type=sa.LargeBinary(), nullable=True
    )
    op.execute("UPDATE transcript_accounts SET webhook_secret_encrypted = NULL")


def downgrade() -> None:
    # Push-only accounts can't exist without a nullable key.
    op.execute("DELETE FROM transcript_accounts WHERE api_key_encrypted IS NULL")
    op.alter_column(
        "transcript_accounts", "api_key_encrypted", existing_type=sa.LargeBinary(), nullable=False
    )
