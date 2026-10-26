"""Optional shared provider defaults for system agents."""
from alembic import op
import sqlalchemy as sa

revision = "20261111_system_default_keys"
down_revision = "20261110_drop_discord_bot_users"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_provider_keys", sa.Column("is_system_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("llm_provider_keys_one_system_default", "llm_provider_keys", ["provider"], unique=True, postgresql_where=sa.text("is_system_default"))


def downgrade():
    op.drop_index("llm_provider_keys_one_system_default", table_name="llm_provider_keys")
    op.drop_column("llm_provider_keys", "is_system_default")
