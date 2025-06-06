"""oauth codes

Revision ID: 66771d293b27
Revises: 58439dd3088b
Create Date: 2025-06-06 12:36:11.737507

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "66771d293b27"
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
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_client.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("state"),
    )
    op.add_column(
        "user_sessions", sa.Column("oauth_state_id", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "fk_user_sessions_oauth_state_id",
        "user_sessions",
        "oauth_states",
        ["oauth_state_id"],
        ["state"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_user_sessions_oauth_state_id", "user_sessions", type_="foreignkey"
    )
    op.drop_column("user_sessions", "oauth_state_id")
    op.drop_table("oauth_states")
    op.drop_table("oauth_client")
