"""Add user_id to people for linking Person records to User accounts.

Revision ID: 20260122_200000
Revises: 20260122_180000
Create Date: 2026-01-22 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260122_200000"
down_revision: Union[str, None] = "20260122_180000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add user_id column to people
    op.add_column(
        "people",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_people_user_id",
        "people",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add index for user_id lookups
    op.create_index(
        "people_user_idx",
        "people",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("people_user_idx", table_name="people")
    op.drop_constraint("fk_people_user_id", "people", type_="foreignkey")
    op.drop_column("people", "user_id")
