"""Discord bots choose their real-time delivery; every bot needs a token.

``realtime`` is "gateway" (the collector connects) or "webhook" (signed pushes,
checked with ``public_key``). Bots that had a public key become webhook bots,
the rest gateway bots, so nothing changes for either. Bots registered without
a token are paused: they can't run the catch-up or send. Adding a token in the
bot's settings resumes one. The downgrade keeps them paused, and a gateway
bot that kept its public key comes back as a webhook bot (the old rule).

Revision ID: 20261127_discord_realtime
Revises: 20261126_per_user_reactions
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20261127_discord_realtime"
down_revision = "20261126_per_user_reactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discord_bots", sa.Column("realtime", sa.Text(), nullable=False, server_default="gateway")
    )
    op.execute("UPDATE discord_bots SET realtime = 'webhook' WHERE public_key IS NOT NULL")
    op.create_check_constraint(
        "ck_discord_bots_realtime", "discord_bots", "realtime IN ('gateway', 'webhook')"
    )
    op.create_check_constraint(
        "ck_discord_bots_webhook_key", "discord_bots", "realtime <> 'webhook' OR public_key IS NOT NULL"
    )
    op.execute("UPDATE discord_bots SET is_active = false WHERE token_encrypted IS NULL")


def downgrade() -> None:
    op.drop_constraint("ck_discord_bots_webhook_key", "discord_bots", type_="check")
    op.drop_constraint("ck_discord_bots_realtime", "discord_bots", type_="check")
    op.drop_column("discord_bots", "realtime")
