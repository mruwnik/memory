"""Add Gmail support to email accounts.

Revision ID: 20260103_150000
Revises: 20260103_120000
Create Date: 2026-01-03

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260103_150000"
down_revision = "20260103_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add account_type column with default 'imap' for existing accounts
    op.add_column(
        "email_accounts",
        sa.Column(
            "account_type",
            sa.Text(),
            nullable=False,
            server_default="imap",
        ),
    )

    # Add google_account_id foreign key for Gmail accounts
    op.add_column(
        "email_accounts",
        sa.Column(
            "google_account_id",
            sa.BigInteger(),
            sa.ForeignKey("google_accounts.id"),
            nullable=True,
        ),
    )

    # Add sync_error column for error tracking
    op.add_column(
        "email_accounts",
        sa.Column("sync_error", sa.Text(), nullable=True),
    )

    # Make IMAP fields nullable (for Gmail accounts that don't need them)
    op.alter_column("email_accounts", "imap_server", nullable=True)
    op.alter_column("email_accounts", "imap_port", nullable=True)
    op.alter_column("email_accounts", "username", nullable=True)
    op.alter_column("email_accounts", "password", nullable=True)
    op.alter_column("email_accounts", "use_ssl", nullable=True)

    # Add index on account_type
    op.create_index(
        "email_accounts_type_idx",
        "email_accounts",
        ["account_type"],
    )

    # Add check constraint for account_type values
    op.create_check_constraint(
        "email_accounts_type_check",
        "email_accounts",
        "account_type IN ('imap', 'gmail')",
    )


def downgrade() -> None:
    # Remove check constraint
    op.drop_constraint("email_accounts_type_check", "email_accounts")

    # Remove index
    op.drop_index("email_accounts_type_idx", table_name="email_accounts")

    # Make IMAP fields non-nullable again
    # Note: This may fail if there are Gmail accounts with null IMAP fields
    op.alter_column("email_accounts", "imap_server", nullable=False)
    op.alter_column("email_accounts", "imap_port", nullable=False)
    op.alter_column("email_accounts", "username", nullable=False)
    op.alter_column("email_accounts", "password", nullable=False)
    op.alter_column("email_accounts", "use_ssl", nullable=False)

    # Remove columns
    op.drop_column("email_accounts", "sync_error")
    op.drop_column("email_accounts", "google_account_id")
    op.drop_column("email_accounts", "account_type")
