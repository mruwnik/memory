"""Add secrets table for encrypted secret storage.

Creates table for storing encrypted secrets (API keys, tokens, etc.)
with symbolic names for lookup, tied to a specific user.

Revision ID: 20260117_120000
Revises: 20260116_120000
Create Date: 2026-01-17

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260117_120000"
down_revision = "20260116_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name", name="unique_secret_per_user"),
    )
    op.create_index("idx_secrets_name", "secrets", ["name"])
    op.create_index("idx_secrets_user", "secrets", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_secrets_user", table_name="secrets")
    op.drop_index("idx_secrets_name", table_name="secrets")
    op.drop_table("secrets")
