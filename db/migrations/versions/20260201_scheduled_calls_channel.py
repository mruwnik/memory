"""Add generic channel fields to scheduled_llm_calls and remove legacy Discord fields.

Adds channel_type and channel_identifier to support multiple notification
channels (Discord, Slack, Email). Removes discord_channel_id and discord_user_id.

Revision ID: 20260201_scheduled_calls_channel
Revises: 20260201_source_item_updated_at
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_scheduled_calls_channel"
down_revision: Union[str, None] = "20260201_source_item_updated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add generic channel fields
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("channel_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("channel_identifier", sa.String(255), nullable=True),
    )

    # Drop legacy Discord foreign keys and columns
    op.drop_constraint(
        "scheduled_llm_calls_discord_channel_id_fkey",
        "scheduled_llm_calls",
        type_="foreignkey",
    )
    op.drop_constraint(
        "scheduled_llm_calls_discord_user_id_fkey",
        "scheduled_llm_calls",
        type_="foreignkey",
    )
    op.drop_column("scheduled_llm_calls", "discord_channel_id")
    op.drop_column("scheduled_llm_calls", "discord_user_id")


def downgrade() -> None:
    # Re-add legacy Discord columns
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("discord_channel_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("discord_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "scheduled_llm_calls_discord_channel_id_fkey",
        "scheduled_llm_calls",
        "discord_channels",
        ["discord_channel_id"],
        ["id"],
    )
    op.create_foreign_key(
        "scheduled_llm_calls_discord_user_id_fkey",
        "scheduled_llm_calls",
        "discord_users",
        ["discord_user_id"],
        ["id"],
    )

    # Drop generic channel fields
    op.drop_column("scheduled_llm_calls", "channel_identifier")
    op.drop_column("scheduled_llm_calls", "channel_type")
