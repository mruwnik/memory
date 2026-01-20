"""Add api_keys table for multiple API keys per user.

Creates table for storing API keys with type, expiry, one-time support, and scopes.
Migrates existing api_key values from users table to the new table.

Revision ID: 20260120_120000
Revises: 20260118_120000
Create Date: 2026-01-20

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision = "20260120_120000"
down_revision = "20260118_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the api_key_type enum
    api_key_type_enum = sa.Enum(
        "internal", "mcp", "discord", "google", "github", "external",
        name="api_key_type"
    )
    api_key_type_enum.create(op.get_bind(), checkfirst=True)

    # Create api_keys table
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column(
            "key_type",
            api_key_type_enum,
            nullable=False,
            server_default="internal",
        ),
        sa.Column("scopes", ARRAY(sa.String()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_one_time", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # Create indexes
    op.create_index("idx_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("idx_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("idx_api_keys_key_type", "api_keys", ["key_type"])

    # Migrate existing api_keys from users table to api_keys table
    # We need to hash the existing keys and store them
    conn = op.get_bind()

    # Get all users with api_keys
    users_with_keys = conn.execute(
        sa.text("SELECT id, api_key FROM users WHERE api_key IS NOT NULL")
    ).fetchall()

    import hashlib

    for user_id, api_key in users_with_keys:
        # Hash the existing key
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        key_prefix = api_key[:12] + "..." if len(api_key) > 12 else api_key

        # Determine key type from prefix
        if api_key.startswith("bot_"):
            key_type = "internal"
        elif api_key.startswith("user_"):
            key_type = "internal"
        else:
            key_type = "external"

        # Insert into api_keys table
        conn.execute(
            sa.text("""
                INSERT INTO api_keys (user_id, key_hash, key_prefix, name, key_type, is_active)
                VALUES (:user_id, :key_hash, :key_prefix, :name, :key_type, true)
            """),
            {
                "user_id": user_id,
                "key_hash": key_hash,
                "key_prefix": key_prefix,
                "name": "Migrated from legacy api_key",
                "key_type": key_type,
            },
        )

    # Update the check constraint to no longer require api_key
    # First drop the old constraint
    op.drop_constraint("user_has_auth_method", "users", type_="check")

    # Create new constraint that checks for password OR existing api_keys
    # Note: We can't easily check the api_keys table in a check constraint,
    # so we make password_hash OR api_key optional (rely on application logic)
    # Actually, let's just remove the constraint entirely since api_keys
    # are now in a separate table
    # For backwards compatibility during migration, we keep the api_key column
    # but mark it as deprecated


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_api_keys_key_type", table_name="api_keys")
    op.drop_index("idx_api_keys_user_id", table_name="api_keys")
    op.drop_index("idx_api_keys_key_hash", table_name="api_keys")

    # Drop table
    op.drop_table("api_keys")

    # Drop enum
    sa.Enum(name="api_key_type").drop(op.get_bind(), checkfirst=True)

    # Restore check constraint
    op.create_check_constraint(
        "user_has_auth_method",
        "users",
        "password_hash IS NOT NULL OR api_key IS NOT NULL",
    )
