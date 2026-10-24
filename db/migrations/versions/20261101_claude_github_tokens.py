"""Per-user GitHub PATs injected into Claude sessions.

Revision ID: 20261101_claude_github_tokens
Revises: 20261031_transcript_push_only
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261101_claude_github_tokens"
down_revision: Union[str, None] = "20261031_transcript_push_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "claude_github_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("token_hint", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "kind", name="claude_github_tokens_user_kind"),
        sa.CheckConstraint(
            "kind IN ('clone', 'write')", name="claude_github_tokens_kind"
        ),
    )


def downgrade() -> None:
    op.drop_table("claude_github_tokens")
