"""Deleting a Discord bot keeps its messages, clearing their bot_id.

Revision ID: 20261202_discord_msg_bot_null
Revises: 20261201_discord_channel_parent
Create Date: 2026-11-06
"""

from alembic import op

revision = "20261202_discord_msg_bot_null"
down_revision = "20261201_discord_channel_parent"
branch_labels = None
depends_on = None


# Older databases predate the schema squash, so the foreign key may carry
# another name: replace whichever one points discord_message.bot_id at discord_bots.
REPLACE_FK = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT con.conname FROM pg_constraint con
        WHERE con.conrelid = 'discord_message'::regclass AND con.contype = 'f'
          AND con.confrelid = 'discord_bots'::regclass
    LOOP
        EXECUTE format('ALTER TABLE discord_message DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;
ALTER TABLE discord_message ADD CONSTRAINT discord_message_bot_id_fkey
    FOREIGN KEY (bot_id) REFERENCES discord_bots(id) ON DELETE {action};
"""


def upgrade() -> None:
    op.execute(REPLACE_FK.format(action="SET NULL"))


def downgrade() -> None:
    op.execute(REPLACE_FK.format(action="NO ACTION"))
