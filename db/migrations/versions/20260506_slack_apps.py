"""Add SlackApp table for multi-tenant Slack app config.

This migration creates the slack_apps table (one row per Slack app registered
at api.slack.com), the slack_app_users join table, and adds a slack_app_id
foreign key on slack_user_credentials so each credential is scoped to a
specific (app, workspace, user) triple.

If the SLACK_CLIENT_ID environment variable is set at migration time, a
"default" SlackApp row is created from current env-var values and existing
SlackUserCredentials rows are backfilled to point at it. Otherwise the
migration runs cleanly on a deployment that has never used Slack before.

The new uniqueness constraint on slack_user_credentials becomes
(slack_app_id, workspace_id, user_id), replacing the old
(workspace_id, user_id). Pre-flight dedup runs before the constraint is
added so existing duplicates do not block deployment.

Operational requirements:
* If SLACK_CLIENT_ID is set OR slack_user_credentials has rows, this
  migration imports `memory.common.db.models.secrets.encrypt_value` and
  therefore requires `SECRETS_ENCRYPTION_KEY` to be configured at
  migration time. Anyone with populated Slack credentials must already
  have this key set (those credentials are encrypted with it), so this
  is implicit but not previously documented.
* If slack_user_credentials has rows but SLACK_CLIENT_ID is unset, the
  migration aborts loudly. Inserting a placeholder row would silently
  orphan the credentials at runtime (`get_legacy_slack_app` queries by
  the env-var value, which would now mismatch the placeholder client_id).
* downgrade() is non-faithful in two small ways: (1) credentials deleted
  by the pre-flight dedup are not restored (no row history kept); (2)
  the legacy SlackApp row created by the upgrade is dropped along with
  the table, but `slack_user_credentials.slack_app_id` rows would have
  been NULL in the pre-upgrade schema — downgrade restores the column
  drop, so all backfilled FK values are lost. Standard alembic shape.

Revision ID: 20260506_slack_apps
Revises: 20260506_transcript_accounts
Create Date: 2026-05-06
"""

import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260506_slack_apps"
down_revision: Union[str, None] = "20260506_transcript_accounts"
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

    connection = op.get_bind()
    legacy_client_id = os.getenv("SLACK_CLIENT_ID", "").strip()
    legacy_client_secret = os.getenv("SLACK_CLIENT_SECRET", "").strip()
    has_existing_creds = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM slack_user_credentials)")
    ).scalar()

    # Three operational paths:
    # 1. legacy_client_id set                → INSERT/UPSERT SlackApp + backfill
    # 2. has_existing_creds, no legacy_client_id → ABORT — placeholder would orphan creds
    # 3. neither                             → fresh deployment, skip
    if has_existing_creds and not legacy_client_id:
        raise RuntimeError(
            "slack_user_credentials has rows but SLACK_CLIENT_ID is unset. "
            "Inserting a placeholder SlackApp row would silently orphan these "
            "credentials at runtime (get_legacy_slack_app queries by the "
            "env-var value). Either set SLACK_CLIENT_ID to the real Slack "
            "client_id used to OAuth those credentials and re-run the "
            "migration, or DELETE the orphaned credentials first."
        )

    if legacy_client_id:
        from memory.common.db.models.secrets import encrypt_value

        client_secret_enc = (
            encrypt_value(legacy_client_secret) if legacy_client_secret else None
        )

        # ON CONFLICT here exists to make the migration idempotent.
        # In practice the row should never pre-exist (this is the same
        # transaction that creates the table), but if a future redeploy
        # ever re-runs the legacy backfill against a wizard-created row,
        # we MUST overwrite the squatting-relevant fields:
        #   * created_by_user_id → NULL — env-var deployment has no owner;
        #     leaving an attacker-set value here would let them later read
        #     and rotate the migrated secret via /slack/apps once the
        #     wizard CRUD endpoints ship (slack-changes.md §4 S4).
        #   * client_secret_encrypted → env-var truth (only if we actually
        #     have one; else keep the existing row's value via COALESCE
        #     so we don't null out a legitimately wizard-set secret).
        #   * setup_state → 'live' — env-var deployments are by
        #     definition past wizard verification.
        #   * is_active → true — re-enables a row a previous operator may
        #     have manually deactivated.
        # See slack-changes.md §3.1 squatting mitigation; CWE-639.
        result = connection.execute(
            sa.text(
                """
                INSERT INTO slack_apps (
                    client_id, name, client_secret_encrypted,
                    setup_state, is_active, created_by_user_id
                )
                VALUES (
                    :client_id, :name, :secret, 'live', true, NULL
                )
                ON CONFLICT (client_id) DO UPDATE
                    SET created_by_user_id = NULL,
                        client_secret_encrypted = COALESCE(
                            EXCLUDED.client_secret_encrypted,
                            slack_apps.client_secret_encrypted
                        ),
                        name = EXCLUDED.name,
                        setup_state = 'live',
                        is_active = true,
                        updated_at = now()
                RETURNING id
                """
            ),
            {
                "client_id": legacy_client_id,
                "name": "Default (env-var migration)",
                "secret": client_secret_enc,
            },
        )
        default_app_id = result.scalar_one()

        connection.execute(
            sa.text(
                "UPDATE slack_user_credentials "
                "SET slack_app_id = :app_id "
                "WHERE slack_app_id IS NULL"
            ),
            {"app_id": default_app_id},
        )

    # Pre-flight dedup: pick the newest credential for each
    # (slack_app_id, workspace_id, user_id) and drop older duplicates.
    # Older duplicates only exist if the same user OAuthed twice into the
    # same workspace under what is now a single app — unlikely, but possible.
    # RETURNING + print() so any deletion is visible in the alembic log;
    # silent destruction of credentials would be operationally hostile.
    deleted = connection.execute(
        sa.text(
            """
            DELETE FROM slack_user_credentials
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY slack_app_id, workspace_id, user_id
                        ORDER BY updated_at DESC NULLS LAST, id DESC
                    ) AS rn
                    FROM slack_user_credentials
                    WHERE slack_app_id IS NOT NULL
                ) ranked
                WHERE ranked.rn > 1
            )
            RETURNING id, slack_app_id, workspace_id, user_id, created_at
            """
        )
    ).fetchall()
    if deleted:
        print(
            f"[slack_apps migration] Pre-flight dedup removed {len(deleted)} "
            "duplicate slack_user_credentials row(s):"
        )
        for row in deleted:
            print(
                f"  id={row.id} slack_app_id={row.slack_app_id} "
                f"workspace_id={row.workspace_id!r} user_id={row.user_id} "
                f"created_at={row.created_at}"
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
