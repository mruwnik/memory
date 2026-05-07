"""Add SlackApp table for multi-tenant Slack app config.

This migration creates the slack_apps table (one row per Slack app registered
at api.slack.com), the slack_app_users join table, and adds a slack_app_id
foreign key on slack_user_credentials so each credential is scoped to a
specific (app, workspace, user) triple.

The new uniqueness constraint on slack_user_credentials becomes
(slack_app_id, workspace_id, user_id), replacing the old
(workspace_id, user_id). Apps are configured via the wizard endpoints
(/slack/apps + /slack/apps/{id}/wizard-nonce); deployments going forward
provision Slack apps through the UI, not env vars.

Revision ID: 20260506_slack_apps
Revises: 20260507_calendar_account_user_id
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260506_slack_apps"
down_revision: Union[str, None] = "20260507_calendar_account_user_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_apps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("signing_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column(
            "setup_state",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_slack_apps_client_id"),
        sa.CheckConstraint(
            "setup_state IN ('draft', 'signing_verified', 'live', 'degraded')",
            name="valid_slack_app_setup_state",
        ),
    )
    op.create_index("slack_apps_setup_state_idx", "slack_apps", ["setup_state"])
    op.create_index("slack_apps_created_by_idx", "slack_apps", ["created_by_user_id"])

    op.create_table(
        "slack_app_users",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("slack_app_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["slack_app_id"], ["slack_apps.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "slack_app_id"),
    )

    op.add_column(
        "slack_user_credentials",
        sa.Column("slack_app_id", sa.BigInteger(), nullable=True),
    )

    # Add NOT NULL via NOT VALID + VALIDATE to avoid a long AccessExclusiveLock.
    op.execute(
        "ALTER TABLE slack_user_credentials "
        "ADD CONSTRAINT slack_user_credentials_slack_app_id_not_null "
        "CHECK (slack_app_id IS NOT NULL) NOT VALID"
    )
    op.execute(
        "ALTER TABLE slack_user_credentials "
        "VALIDATE CONSTRAINT slack_user_credentials_slack_app_id_not_null"
    )
    op.alter_column(
        "slack_user_credentials", "slack_app_id", nullable=False
    )
    op.execute(
        "ALTER TABLE slack_user_credentials "
        "DROP CONSTRAINT slack_user_credentials_slack_app_id_not_null"
    )

    op.create_foreign_key(
        "fk_slack_user_credentials_slack_app_id",
        "slack_user_credentials",
        "slack_apps",
        ["slack_app_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "unique_slack_credential_per_user",
        "slack_user_credentials",
        type_="unique",
    )
    op.create_unique_constraint(
        "unique_slack_credential_per_app_workspace_user",
        "slack_user_credentials",
        ["slack_app_id", "workspace_id", "user_id"],
    )
    op.create_index(
        "slack_credentials_app_idx",
        "slack_user_credentials",
        ["slack_app_id"],
    )


def downgrade() -> None:
    op.drop_index("slack_credentials_app_idx", table_name="slack_user_credentials")
    op.drop_constraint(
        "unique_slack_credential_per_app_workspace_user",
        "slack_user_credentials",
        type_="unique",
    )
    op.create_unique_constraint(
        "unique_slack_credential_per_user",
        "slack_user_credentials",
        ["workspace_id", "user_id"],
    )
    op.drop_constraint(
        "fk_slack_user_credentials_slack_app_id",
        "slack_user_credentials",
        type_="foreignkey",
    )
    op.drop_column("slack_user_credentials", "slack_app_id")

    op.drop_table("slack_app_users")

    op.drop_index("slack_apps_created_by_idx", table_name="slack_apps")
    op.drop_index("slack_apps_setup_state_idx", table_name="slack_apps")
    op.drop_table("slack_apps")
