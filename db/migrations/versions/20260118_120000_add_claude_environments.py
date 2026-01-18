"""Add claude_environments table.

Creates table for persistent Claude Code environments backed by Docker volumes.

Volume name format: claude-env-u{user_id}-{env_id}-{slugified_name}
Example: claude-env-u42-7-my-dev-environment

Revision ID: 20260118_120000
Revises: 20260117_120000
Create Date: 2026-01-18

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260118_120000"
down_revision = "20260117_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claude_environments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("volume_name", sa.Text(), nullable=False),
        sa.Column("initialized_from_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["initialized_from_snapshot_id"],
            ["claude_config_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("volume_name", name="unique_environment_volume"),
    )

    op.create_index("idx_environments_user", "claude_environments", ["user_id"])
    # Note: No explicit index on volume_name needed - the unique constraint creates one


def downgrade() -> None:
    op.drop_index("idx_environments_user", table_name="claude_environments")
    op.drop_table("claude_environments")
