"""proper user id type

Revision ID: 7dc03dbf184c
Revises: 35a2c1b610b6
Create Date: 2025-10-20 22:09:11.243681

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7dc03dbf184c"
down_revision: Union[str, None] = "35a2c1b610b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_channels", sa.Column("system_prompt", sa.Text(), nullable=True)
    )
    op.add_column(
        "discord_channels",
        sa.Column(
            "chattiness_threshold", sa.Integer(), nullable=False, server_default="50"
        ),
    )
    op.add_column(
        "discord_servers", sa.Column("system_prompt", sa.Text(), nullable=True)
    )
    op.add_column(
        "discord_servers",
        sa.Column(
            "chattiness_threshold", sa.Integer(), nullable=False, server_default="50"
        ),
    )
    op.add_column("discord_users", sa.Column("system_prompt", sa.Text(), nullable=True))
    op.add_column(
        "discord_users",
        sa.Column(
            "chattiness_threshold", sa.Integer(), nullable=False, server_default="50"
        ),
    )
    op.alter_column(
        "scheduled_llm_calls",
        "user_id",
        existing_type=sa.INTEGER(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "scheduled_llm_calls",
        "user_id",
        existing_type=sa.BigInteger(),
        type_=sa.INTEGER(),
        existing_nullable=False,
    )
    op.drop_column("discord_users", "chattiness_threshold")
    op.drop_column("discord_users", "system_prompt")
    op.drop_column("discord_servers", "chattiness_threshold")
    op.drop_column("discord_servers", "system_prompt")
    op.drop_column("discord_channels", "chattiness_threshold")
    op.drop_column("discord_channels", "system_prompt")
