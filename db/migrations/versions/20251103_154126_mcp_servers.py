"""mcp servers

Revision ID: 89861d5f1102
Revises: 1954477b25f4
Create Date: 2025-11-03 15:41:26.254854

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "89861d5f1102"
down_revision: Union[str, None] = "1954477b25f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mcp_server_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column(
            "available_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_index("mcp_state_idx", "mcp_servers", ["state"], unique=False)
    op.create_table(
        "mcp_server_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mcp_server_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
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
            ["mcp_server_id"],
            ["mcp_servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "mcp_assignment_entity_idx",
        "mcp_server_assignments",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "mcp_assignment_server_idx",
        "mcp_server_assignments",
        ["mcp_server_id"],
        unique=False,
    )
    op.create_index(
        "mcp_assignment_unique_idx",
        "mcp_server_assignments",
        ["mcp_server_id", "entity_type", "entity_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("mcp_assignment_unique_idx", table_name="mcp_server_assignments")
    op.drop_index("mcp_assignment_server_idx", table_name="mcp_server_assignments")
    op.drop_index("mcp_assignment_entity_idx", table_name="mcp_server_assignments")
    op.drop_table("mcp_server_assignments")
    op.drop_index("mcp_state_idx", table_name="mcp_servers")
    op.drop_table("mcp_servers")
