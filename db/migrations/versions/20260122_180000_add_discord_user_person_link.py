"""Add person_id to discord_users for linking Discord accounts to Person contacts.

Revision ID: 20260122_180000
Revises: 20260122_150000
Create Date: 2026-01-22 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260122_180000"
down_revision: Union[str, None] = "20260122_150000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add person_id column to discord_users
    op.add_column(
        "discord_users",
        sa.Column("person_id", sa.BigInteger(), nullable=True),
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_discord_users_person_id",
        "discord_users",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add index for person_id lookups
    op.create_index(
        "discord_users_person_idx",
        "discord_users",
        ["person_id"],
    )


def downgrade() -> None:
    op.drop_index("discord_users_person_idx", table_name="discord_users")
    op.drop_constraint("fk_discord_users_person_id", "discord_users", type_="foreignkey")
    op.drop_column("discord_users", "person_id")
