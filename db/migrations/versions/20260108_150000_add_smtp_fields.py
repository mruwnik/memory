"""Add SMTP fields to email accounts for sending.

Revision ID: 20260108_150000
Revises: 20260108_120000
Create Date: 2026-01-08

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260108_150000"
down_revision = "20260108_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add SMTP server field (optional - inferred from IMAP if not set)
    op.add_column(
        "email_accounts",
        sa.Column("smtp_server", sa.Text(), nullable=True),
    )

    # Add SMTP port field (optional - defaults to 587 for TLS)
    op.add_column(
        "email_accounts",
        sa.Column("smtp_port", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "smtp_port")
    op.drop_column("email_accounts", "smtp_server")
