"""Per-user external MCP servers with encrypted credentials.

Drops the Discord-era mcp_servers / mcp_server_assignments tables: their tokens
were plaintext and per server rather than per user. Servers are re-registered.

Revision ID: 20261026_external_mcp_servers
Revises: 20261025_agent_triggers
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261026_external_mcp_servers"
down_revision: Union[str, None] = "20261025_agent_triggers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("mcp_server_assignments")
    op.drop_table("mcp_servers")
    op.create_table(
        "external_mcp_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("headers_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column(
            "iss_parameter_supported",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("authorization_endpoint", sa.Text(), nullable=True),
        sa.Column("token_endpoint", sa.Text(), nullable=True),
        sa.Column("revocation_endpoint", sa.Text(), nullable=True),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("token_endpoint_auth_method", sa.Text(), nullable=True),
        sa.Column("client_source", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tools_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tools_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "name", name="external_mcp_servers_user_name"),
        sa.CheckConstraint(
            "transport IN ('http', 'sse')", name="external_mcp_servers_transport"
        ),
        sa.CheckConstraint(
            "auth_type IN ('oauth', 'headers', 'none')",
            name="external_mcp_servers_auth_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending_auth', 'connected', 'needs_reauth', 'error')",
            name="external_mcp_servers_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("external_mcp_servers")
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mcp_server_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column(
            "available_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "disabled_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
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
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"]),
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
