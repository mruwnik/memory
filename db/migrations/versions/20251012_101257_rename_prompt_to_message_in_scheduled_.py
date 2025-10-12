"""rename_prompt_to_message_in_scheduled_calls

Revision ID: c86079073c1d
Revises: 2fb3223dc71b
Create Date: 2025-10-12 10:12:57.421009

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c86079073c1d"
down_revision: Union[str, None] = "2fb3223dc71b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename prompt column to message in scheduled_llm_calls table
    op.alter_column("scheduled_llm_calls", "prompt", new_column_name="message")


def downgrade() -> None:
    # Rename message column back to prompt in scheduled_llm_calls table
    op.alter_column("scheduled_llm_calls", "message", new_column_name="prompt")
