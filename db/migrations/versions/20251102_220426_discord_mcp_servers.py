"""discord mcp servers

Revision ID: 9b887449ea92
Revises: 1954477b25f4
Create Date: 2025-11-02 22:04:26.259323

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b887449ea92"
down_revision: Union[str, None] = "1954477b25f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discord_mcp_servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("discord_bot_user_id", sa.BigInteger(), nullable=False),
        sa.Column("mcp_server_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("code_verifier", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["discord_bot_user_id"],
            ["discord_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_index(
        "discord_mcp_state_idx", "discord_mcp_servers", ["state"], unique=False
    )
    op.create_index(
        "discord_mcp_user_url_idx",
        "discord_mcp_servers",
        ["discord_bot_user_id", "mcp_server_url"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("discord_mcp_user_url_idx", table_name="discord_mcp_servers")
    op.drop_index("discord_mcp_state_idx", table_name="discord_mcp_servers")
    op.drop_table("discord_mcp_servers")
