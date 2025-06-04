"""observation session as string

Revision ID: 58439dd3088b
Revises: 77cdbfc882e2
Create Date: 2025-06-04 16:21:13.610668

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "58439dd3088b"
down_revision: Union[str, None] = "77cdbfc882e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "agent_observation",
        "session_id",
        existing_type=sa.UUID(),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "agent_observation",
        "session_id",
        existing_type=sa.Text(),
        type_=sa.UUID(),
        existing_nullable=True,
    )
