"""Sessions minted for unattended work can act through an API key."""
from alembic import op
import sqlalchemy as sa

revision = "20261116_session_api_key"
down_revision = "20261115_hash_api_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_sessions",
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_user_sessions_api_key_id", "user_sessions", ["api_key_id"])


def downgrade():
    op.drop_index("ix_user_sessions_api_key_id", table_name="user_sessions")
    op.drop_column("user_sessions", "api_key_id")
