"""A DM's messages that another owner's bot delivered stop being that bot's,
so its owner can no longer edit, delete or react to them: rows from before
DM pushes were held to the DM's owner kept the pushing bot. A DM with no bot
has no owner to compare with and is left alone. Data only; touches no index
or constraint, so instances older than the schema squash run it as is.

Revision ID: 20261218_disown_dm_messages
Revises: 20261217_subscriptions_contract
Create Date: 2026-11-08
"""

from alembic import op

revision = "20261218_disown_dm_messages"
down_revision = "20261217_subscriptions_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE discord_message m
        SET bot_id = NULL
        FROM discord_channels c, discord_bots holder, discord_bots deliverer
        WHERE m.channel_id = c.id
          AND c.server_id IS NULL
          AND c.channel_type IN ('dm', 'group_dm')
          AND holder.id = c.bot_id
          AND deliverer.id = m.bot_id
          AND m.bot_id <> c.bot_id
          AND (
            holder.created_by_user_id IS NULL
            OR deliverer.created_by_user_id IS DISTINCT FROM holder.created_by_user_id
          )
        """
    )


def downgrade() -> None:
    # Which bot delivered a disowned message isn't kept.
    pass
