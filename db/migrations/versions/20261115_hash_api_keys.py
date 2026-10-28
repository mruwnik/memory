"""Store API keys as SHA-256 hashes with a display preview; drop the plaintext.

Existing keys keep working. Their plaintext can't be shown again, and a
downgrade can't restore it: after one, every API key must be reissued.

users.api_key goes too: nothing authenticates with it, but it may still hold the
plaintext of keys copied into api_keys when that table was introduced.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261115_hash_api_keys"
down_revision = "20261114_drop_secrets"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_keys", sa.Column("key_hash", sa.String(), nullable=True))
    op.add_column("api_keys", sa.Column("key_preview", sa.String(), nullable=True))
    # Must match memory.common.db.models.users.hash_token and api_key_preview.
    op.execute(
        "UPDATE api_keys SET "
        "key_hash = encode(sha256(convert_to(key, 'UTF8')), 'hex'), "
        "key_preview = CASE WHEN position('_' in key) > 0 AND split_part(key, '_', 1) IN "
        "('internal', 'discord', 'google', 'github', 'mcp', 'bot', 'user') "
        "THEN split_part(key, '_', 1) || '_' ELSE '' END || '...' || right(key, 4)"
    )
    op.alter_column("api_keys", "key_hash", nullable=False)
    op.alter_column("api_keys", "key_preview", nullable=False)
    op.create_unique_constraint("api_keys_key_hash_key", "api_keys", ["key_hash"])
    op.drop_column("api_keys", "key")
    op.drop_constraint("user_has_auth_method", "users", type_="check")
    op.drop_column("users", "api_key")


def downgrade():
    op.add_column("users", sa.Column("api_key", sa.String(), nullable=True, unique=True))
    # NOT VALID: bots made since the upgrade have neither a password nor a users.api_key.
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT user_has_auth_method "
        "CHECK (password_hash IS NOT NULL OR api_key IS NOT NULL) NOT VALID"
    )
    # The plaintext is gone. Filled from the hash only to stay unique and non-null:
    # revoked and prefixed so the hash, which a reader of the table has, is no key.
    op.add_column("api_keys", sa.Column("key", sa.String(), nullable=True))
    op.execute("UPDATE api_keys SET key = 'revoked:' || key_hash, revoked = true")
    op.alter_column("api_keys", "key", nullable=False)
    op.create_unique_constraint("api_keys_key_key", "api_keys", ["key"])
    op.drop_column("api_keys", "key_preview")
    op.drop_column("api_keys", "key_hash")
