"""Add send_enabled flag to email accounts.

Allows configuring whether an account can be used for sending emails,
independent of the active flag which controls sync.

Revision ID: 20260108_180000
Revises: 20260108_150000
Create Date: 2026-01-08

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260108_180000"
down_revision = "20260108_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_accounts",
        sa.Column(
            "send_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "send_enabled")
