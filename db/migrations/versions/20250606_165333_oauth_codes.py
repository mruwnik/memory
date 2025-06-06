"""oauth codes

Revision ID: 1d6bc8015ea9
Revises: 58439dd3088b
Create Date: 2025-06-06 16:53:33.044558

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1d6bc8015ea9"
down_revision: Union[str, None] = "58439dd3088b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_client",
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret", sa.String(), nullable=True),
        sa.Column("client_id_issued_at", sa.Numeric(), nullable=False),
        sa.Column("client_secret_expires_at", sa.Numeric(), nullable=True),
        sa.Column("redirect_uris", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.String(), nullable=False),
        sa.Column("grant_types", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("response_types", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("client_uri", sa.String(), nullable=True),
        sa.Column("logo_uri", sa.String(), nullable=True),
        sa.Column("contacts", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("tos_uri", sa.String(), nullable=True),
        sa.Column("policy_uri", sa.String(), nullable=True),
        sa.Column("jwks_uri", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=True),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_client.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("access_token_session_id", sa.String(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["access_token_session_id"],
            ["user_sessions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_client.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "user_sessions", sa.Column("oauth_state_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "user_sessions_oauth_state_id_fkey",
        "user_sessions",
        "oauth_states",
        ["oauth_state_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "user_sessions_oauth_state_id_fkey", "user_sessions", type_="foreignkey"
    )
    op.drop_column("user_sessions", "oauth_state_id")
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_states")
    op.drop_table("oauth_client")
