"""Add api_keys table for multiple API keys per user.

Creates a dedicated table for API keys with support for:
- Multiple keys per user
- Key types (discord, google, internal, etc.)
- Expiration dates
- One-time use keys
- Scopes override

Migrates existing api_key values from the users table.

Revision ID: 20260122_120000
Revises: 20260121_120000
Create Date: 2026-01-22

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260122_120000"
down_revision = "20260121_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the api_keys table
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("key_type", sa.String(), nullable=False, server_default="internal"),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("is_one_time", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("key", name="unique_api_key"),
    )
    op.create_index("idx_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("idx_api_keys_key", "api_keys", ["key"])
    op.create_index("idx_api_keys_key_type", "api_keys", ["key_type"])

    # Migrate existing api_key values from users table
    # We insert into api_keys for each user that has an api_key
    connection = op.get_bind()
    connection.execute(
        sa.text("""
            INSERT INTO api_keys (user_id, key, name, key_type)
            SELECT id, api_key, 'Legacy API Key',
                   CASE
                       WHEN user_type = 'bot' THEN 'internal'
                       ELSE 'internal'
                   END
            FROM users
            WHERE api_key IS NOT NULL
        """)
    )


def downgrade() -> None:
    # Note: We don't migrate keys back to users table since the users table
    # might have multiple keys now. The legacy api_key column will remain
    # with its original value if it exists.
    op.drop_index("idx_api_keys_key_type", table_name="api_keys")
    op.drop_index("idx_api_keys_key", table_name="api_keys")
    op.drop_index("idx_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
