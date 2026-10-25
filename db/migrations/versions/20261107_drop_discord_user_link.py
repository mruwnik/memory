"""Drop discord_users.system_user_id; Discord accounts reach a user through their Person.

Revision ID: 20261107_drop_discord_user_link
Revises: 20261106_unique_person_user
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261107_drop_discord_user_link"
down_revision: Union[str, None] = "20261106_unique_person_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("discord_users_system_user_idx", table_name="discord_users")
    op.drop_column("discord_users", "system_user_id")


def downgrade() -> None:
    op.add_column("discord_users", sa.Column("system_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "discord_users_system_user_id_fkey",
        "discord_users",
        "users",
        ["system_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("discord_users_system_user_idx", "discord_users", ["system_user_id"])
