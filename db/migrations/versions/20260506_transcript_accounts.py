"""Add transcript_accounts table for per-user transcript provider integrations.

Revision ID: 20260506_transcript_accounts
Revises: 20260216_report_connect_urls
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260506_transcript_accounts"
down_revision: Union[str, None] = "20260216_report_connect_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transcript_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("webhook_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column(
            "sync_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sensitivity",
            sa.String(length=20),
            nullable=False,
            server_default="basic",
        ),
        sa.Column(
            "config_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        # NOTE: no provider CHECK constraint — provider validation lives in
        # the PROVIDERS dispatch dict in workers/tasks/transcripts.py so
        # adding a provider doesn't require a coordinated schema change.
        sa.CheckConstraint(
            "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
            name="valid_transcript_account_sensitivity",
        ),
        sa.UniqueConstraint(
            "user_id", "provider", "name", name="unique_transcript_account_per_user"
        ),
    )
    op.create_index(
        "transcript_accounts_user_idx", "transcript_accounts", ["user_id"]
    )
    op.create_index(
        "transcript_accounts_active_idx",
        "transcript_accounts",
        ["active", "last_sync_at"],
    )
    op.create_index(
        "transcript_accounts_provider_idx", "transcript_accounts", ["provider"]
    )
    op.create_index(
        "transcript_accounts_project_idx", "transcript_accounts", ["project_id"]
    )
    op.create_index(
        "transcript_accounts_tags_idx",
        "transcript_accounts",
        ["tags"],
        postgresql_using="gin",
    )

    op.add_column(
        "meeting",
        sa.Column(
            "transcript_account_id",
            sa.BigInteger(),
            sa.ForeignKey("transcript_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "meeting_transcript_account_idx", "meeting", ["transcript_account_id"]
    )


def downgrade() -> None:
    op.drop_index("meeting_transcript_account_idx", table_name="meeting")
    op.drop_column("meeting", "transcript_account_id")

    op.drop_index("transcript_accounts_tags_idx", table_name="transcript_accounts")
    op.drop_index(
        "transcript_accounts_project_idx", table_name="transcript_accounts"
    )
    op.drop_index(
        "transcript_accounts_provider_idx", table_name="transcript_accounts"
    )
    op.drop_index(
        "transcript_accounts_active_idx", table_name="transcript_accounts"
    )
    op.drop_index("transcript_accounts_user_idx", table_name="transcript_accounts")
    op.drop_table("transcript_accounts")
