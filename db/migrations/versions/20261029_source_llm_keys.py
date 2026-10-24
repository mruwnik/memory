"""Per-source choice of which provider key pays for LLM work, and Discord bot registrants.

Every source starts on "default": the default key of whoever brought each item
in. Existing bots are credited to their lowest-id authorized user.

Revision ID: 20261029_source_llm_keys
Revises: 20261029_llm_spend
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_source_llm_keys"
down_revision: Union[str, None] = "20261029_llm_spend"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HOLDERS = (
    "email_accounts",
    "article_feeds",
    "github_accounts",
    "google_folders",
    "calendar_accounts",
    "transcript_accounts",
    "slack_workspaces",
    "discord_servers",
)


def upgrade() -> None:
    for table in HOLDERS:
        op.add_column(
            table,
            sa.Column(
                "llm_key_id",
                sa.Integer(),
                sa.ForeignKey("llm_provider_keys.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "llm_disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
        )
    op.add_column(
        "discord_bots",
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE discord_bots b SET created_by_user_id = "
        "(SELECT min(user_id) FROM discord_bot_users u WHERE u.bot_id = b.id)"
    )


def downgrade() -> None:
    op.drop_column("discord_bots", "created_by_user_id")
    for table in HOLDERS:
        op.drop_column(table, "llm_disabled")
        op.drop_column(table, "llm_key_id")
