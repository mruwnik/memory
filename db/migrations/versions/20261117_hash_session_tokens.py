"""Store login sessions, OAuth access and refresh tokens and poll edit tokens as SHA-256 hashes.

Existing tokens keep working. A downgrade can't restore them: after one, everyone
is logged out, OAuth clients have to authorize again and poll edit links stop working.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261117_hash_session_tokens"
down_revision = "20261116_session_api_key"
branch_labels = None
depends_on = None

# Must match memory.common.db.models.users.hash_token.
def sha256(column: str) -> str:
    return f"encode(sha256(convert_to({column}, 'UTF8')), 'hex')"


SESSION_FK = "oauth_refresh_tokens_access_token_session_id_fkey"


def upgrade():
    # The session id is the token, so rewriting it means rewriting the refresh
    # tokens that point at it. Ending a session unlinks its refresh tokens, which
    # stay for replay detection.
    op.drop_constraint(SESSION_FK, "oauth_refresh_tokens", type_="foreignkey")
    op.execute(f"UPDATE user_sessions SET id = {sha256('id')}")
    op.execute(
        "UPDATE oauth_refresh_tokens SET access_token_session_id = "
        f"{sha256('access_token_session_id')} WHERE access_token_session_id IS NOT NULL"
    )
    op.create_foreign_key(
        SESSION_FK, "oauth_refresh_tokens", "user_sessions", ["access_token_session_id"], ["id"],
        ondelete="SET NULL",
    )

    op.add_column("oauth_refresh_tokens", sa.Column("token_hash", sa.String(), nullable=True))
    op.execute(f"UPDATE oauth_refresh_tokens SET token_hash = {sha256('token')}")
    op.alter_column("oauth_refresh_tokens", "token_hash", nullable=False)
    op.create_unique_constraint(
        "oauth_refresh_tokens_token_hash_key", "oauth_refresh_tokens", ["token_hash"]
    )
    op.drop_column("oauth_refresh_tokens", "token")

    op.alter_column("poll_responses", "edit_token", type_=sa.String(64))
    op.execute(f"UPDATE poll_responses SET edit_token = {sha256('edit_token')}")
    op.alter_column("poll_responses", "edit_token", new_column_name="edit_token_hash")


def downgrade():
    # The plaintext is gone, and the hashes, which a reader of the tables has,
    # must not become tokens: sessions are deleted, refresh tokens revoked and
    # edit tokens replaced with random ones.
    op.execute("DELETE FROM user_sessions")
    op.drop_constraint(SESSION_FK, "oauth_refresh_tokens", type_="foreignkey")
    op.create_foreign_key(
        SESSION_FK, "oauth_refresh_tokens", "user_sessions", ["access_token_session_id"], ["id"]
    )
    op.add_column("oauth_refresh_tokens", sa.Column("token", sa.String(), nullable=True))
    op.execute("UPDATE oauth_refresh_tokens SET token = 'revoked:' || token_hash, revoked = true")
    op.alter_column("oauth_refresh_tokens", "token", nullable=False)
    op.drop_column("oauth_refresh_tokens", "token_hash")
    op.alter_column("poll_responses", "edit_token_hash", new_column_name="edit_token")
    op.alter_column(
        "poll_responses", "edit_token", type_=sa.String(32), postgresql_using="md5(random()::text)"
    )
