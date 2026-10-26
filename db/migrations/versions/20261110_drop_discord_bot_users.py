"""Discord bots belong to their registrant; remove per-bot user sharing."""
from alembic import op
import sqlalchemy as sa

revision = "20261110_drop_discord_bot_users"
down_revision = "20261109_slack_reconcile"
branch_labels = None
depends_on = None


def upgrade():
    # Retain a legacy bot's earliest remaining user if its registrant is missing.
    op.execute(
        "UPDATE discord_bots b SET created_by_user_id = "
        "(SELECT min(u.user_id) FROM discord_bot_users u WHERE u.bot_id = b.id) "
        "WHERE b.created_by_user_id IS NULL"
    )
    op.drop_table("discord_bot_users")


def downgrade():
    op.create_table(
        "discord_bot_users",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("bot_id", sa.BigInteger(), sa.ForeignKey("discord_bots.id", ondelete="CASCADE"), primary_key=True),
    )
    op.execute(
        "INSERT INTO discord_bot_users (user_id, bot_id) "
        "SELECT created_by_user_id, id FROM discord_bots WHERE created_by_user_id IS NOT NULL"
    )
