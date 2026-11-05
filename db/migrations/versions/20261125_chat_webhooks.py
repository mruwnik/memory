"""Slack apps and Discord bots for webhook delivery.

A Slack client_id is unique per registering user, not globally, and Slack apps
get OAuth and Web API URLs. Discord bots get a surrogate id, so several users can
connect the same application: existing rows keep their id (the application's
snowflake, which stays in application_id) and new rows take small sequence
values that never reach snowflake range. They also get the Webhook Events public
key and a REST API URL. Both record when their last event arrived.

Revision ID: 20261125_chat_webhooks
Revises: 20261124_drop_claude_containers
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20261125_chat_webhooks"
down_revision = "20261124_drop_claude_containers"
branch_labels = None
depends_on = None

SLACK_URLS = {
    "authorize_url": "https://slack.com/oauth/v2/authorize",
    "token_url": "https://slack.com/api/oauth.v2.access",
    "api_base_url": "https://slack.com/api",
}
DISCORD_API_BASE_URL = "https://discord.com/api/v10"


def split_slack_scopes() -> None:
    """Slack returns granted scopes comma-separated; rows stored them as one item."""
    op.execute(
        r"""
        UPDATE slack_user_credentials
        SET scopes = array_remove(
            string_to_array(
                regexp_replace(array_to_string(scopes, ','), '[,\s]+', ',', 'g'), ','
            ),
            ''
        )
        WHERE array_to_string(scopes, ',') ~ '\s' OR EXISTS (
            SELECT 1 FROM unnest(scopes) AS scope WHERE scope LIKE '%,%'
        )
        """
    )


# Older databases predate the schema squash, so the client_id-only unique
# constraint (or index) may carry another name: drop whichever exists.
DROP_CLIENT_ID_UNIQUE = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT con.conname FROM pg_constraint con
        WHERE con.conrelid = 'slack_apps'::regclass AND con.contype = 'u'
          AND con.conkey = ARRAY[(
              SELECT attnum FROM pg_attribute
              WHERE attrelid = 'slack_apps'::regclass AND attname = 'client_id'
          )]::smallint[]
    LOOP
        EXECUTE format('ALTER TABLE slack_apps DROP CONSTRAINT %I', r.conname);
    END LOOP;
    FOR r IN
        SELECT ic.relname FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = 'slack_apps'::regclass AND i.indisunique AND NOT i.indisprimary
          AND i.indkey::text = (
              SELECT attnum::text FROM pg_attribute
              WHERE attrelid = 'slack_apps'::regclass AND attname = 'client_id'
          )
    LOOP
        EXECUTE format('DROP INDEX %I', r.relname);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    split_slack_scopes()
    op.execute(DROP_CLIENT_ID_UNIQUE)
    op.create_unique_constraint(
        "uq_slack_apps_client_id_user", "slack_apps", ["client_id", "created_by_user_id"]
    )
    for column, default in SLACK_URLS.items():
        op.add_column(
            "slack_apps",
            sa.Column(column, sa.Text(), nullable=False, server_default=default),
        )
    op.add_column(
        "slack_apps", sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Databases created from the original bigint PK already have this sequence.
    op.execute("CREATE SEQUENCE IF NOT EXISTS discord_bots_id_seq OWNED BY discord_bots.id")
    op.execute(
        "ALTER TABLE discord_bots ALTER COLUMN id SET DEFAULT nextval('discord_bots_id_seq')"
    )
    op.add_column("discord_bots", sa.Column("application_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE discord_bots SET application_id = id")
    op.alter_column("discord_bots", "application_id", nullable=False)
    op.create_unique_constraint(
        "uq_discord_bots_application_user",
        "discord_bots",
        ["application_id", "created_by_user_id"],
    )
    op.add_column("discord_bots", sa.Column("public_key", sa.Text(), nullable=True))
    op.add_column(
        "discord_bots",
        sa.Column(
            "api_base_url", sa.Text(), nullable=False, server_default=DISCORD_API_BASE_URL
        ),
    )
    op.add_column(
        "discord_bots", sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.add_column(
        "discord_channels",
        sa.Column(
            "bot_id",
            sa.BigInteger(),
            sa.ForeignKey("discord_bots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "slack_message",
        sa.Column(
            "slack_app_id",
            sa.BigInteger(),
            sa.ForeignKey("slack_apps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Servers so far came from gateway bots, whose tokens Discord checked.
    op.add_column(
        "discord_servers",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_table(
        "discord_bot_servers",
        sa.Column(
            "bot_id", sa.BigInteger(), sa.ForeignKey("discord_bots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "server_id", sa.BigInteger(), sa.ForeignKey("discord_servers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.execute(
        "INSERT INTO discord_bot_servers (bot_id, server_id) "
        "SELECT bot_id, id FROM discord_servers WHERE bot_id IS NOT NULL"
    )
    # Existing history belongs to the app of each workspace's earliest
    # credential, the same first-come rule as DMs below.
    op.execute(
        """
        UPDATE slack_message m SET slack_app_id = (
            SELECT c.slack_app_id FROM slack_user_credentials c
            WHERE c.workspace_id = m.workspace_id
            ORDER BY c.id LIMIT 1
        )
        WHERE m.slack_app_id IS NULL
        """
    )
    # A DM's owner is the owner of the bot that delivered its first message.
    op.execute(
        """
        UPDATE discord_channels c SET bot_id = (
            SELECT m.bot_id FROM discord_message m
            WHERE m.channel_id = c.id AND m.bot_id IS NOT NULL
            ORDER BY m.message_id LIMIT 1
        )
        WHERE c.server_id IS NULL
        """
    )


def refuse_lossy_downgrade() -> None:
    """The old schema keys bots by application snowflake and allows one Slack
    app per client_id; rows that don't fit would have to be deleted."""
    bind = op.get_bind()
    extra_bots = bind.execute(
        sa.text("SELECT count(*) FROM discord_bots WHERE id <> application_id")
    ).scalar()
    shared_apps = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT client_id FROM slack_apps "
            "GROUP BY client_id HAVING count(*) > 1) shared"
        )
    ).scalar()
    if extra_bots or shared_apps:
        raise RuntimeError(
            f"Cannot downgrade 20261125_chat_webhooks: {extra_bots} Discord bots were added "
            f"under the new ids and {shared_apps} Slack client_ids are registered by several "
            "users. Delete those rows first."
        )


def downgrade() -> None:
    refuse_lossy_downgrade()
    op.drop_table("discord_bot_servers")
    op.drop_column("discord_servers", "verified")
    op.drop_column("slack_message", "slack_app_id")
    op.drop_column("discord_channels", "bot_id")
    for column in ("last_event_at", "api_base_url", "public_key"):
        op.drop_column("discord_bots", column)
    op.drop_constraint("uq_discord_bots_application_user", "discord_bots", type_="unique")
    op.drop_column("discord_bots", "application_id")
    op.drop_column("slack_apps", "last_event_at")
    for column in SLACK_URLS:
        op.drop_column("slack_apps", column)
    op.drop_constraint("uq_slack_apps_client_id_user", "slack_apps", type_="unique")
    op.create_unique_constraint("uq_slack_apps_client_id", "slack_apps", ["client_id"])
