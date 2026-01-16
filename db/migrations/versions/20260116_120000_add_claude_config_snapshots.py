"""Add claude_config_snapshots table and SSH key fields to users.

Creates table for storing Claude Code config snapshots and adds
SSH key fields to users table for container Git operations.

Revision ID: 20260116_120000
Revises: 20260112_120000
Create Date: 2026-01-16

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260116_120000"
down_revision = "20260112_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add SSH key fields to users table
    # Public key is stored as plaintext (safe to expose)
    op.add_column("users", sa.Column("ssh_public_key", sa.String(), nullable=True))
    # Private key is encrypted at rest using Fernet with SSH_KEY_ENCRYPTION_SECRET
    op.add_column(
        "users", sa.Column("ssh_private_key_encrypted", sa.LargeBinary(), nullable=True)
    )

    # Create claude_config_snapshots table
    op.create_table(
        "claude_config_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("claude_account_email", sa.Text(), nullable=True),
        sa.Column("subscription_type", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="unique_snapshot_hash"),
    )

    op.create_index("idx_snapshots_user", "claude_config_snapshots", ["user_id"])
    op.create_index("idx_snapshots_hash", "claude_config_snapshots", ["content_hash"])

    # Generate SSH keys for existing users
    from memory.common.db.models.users import User, generate_ssh_keypair
    from memory.common import settings

    if not settings.SSH_KEY_ENCRYPTION_SECRET:
        print("WARNING: SSH_KEY_ENCRYPTION_SECRET not set, skipping SSH key generation")
        return

    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)
    try:
        for user in session.query(User).filter(User.ssh_public_key.is_(None)).all():
            generate_ssh_keypair(user)
        session.commit()
    finally:
        session.close()


def downgrade() -> None:
    op.drop_table("claude_config_snapshots")
    op.drop_column("users", "ssh_private_key_encrypted")
    op.drop_column("users", "ssh_public_key")
